"""
SMC Entry Engine — Master signal confirmation logic.

Implements a strict 9-step top-down analysis pipeline.
Only generates a signal when ALL criteria are confirmed.
It's better to miss 10 trades than take 1 bad one.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

import pandas as pd

from config import BotConfig, get_instrument_profile, get_pip_size
from core.market_structure import (
    detect_swing_highs, detect_swing_lows, detect_choch, get_market_bias, detect_structure_shift, detect_bos_after_index,
    recent_dealing_range,
)
from core.liquidity import LiquidityEngine
from core.poi import POIEngine, OrderBlock
from core.premium_discount import (
    get_premium_discount_zones, is_in_discount,
    is_in_premium, is_in_ote,
)
from core.sessions import SessionManager
from core.pending_intent import PendingIntent, IntentManager
from broker.mt5_client import get_mt5
from utils.indicators import atr as calculate_atr
from utils.logger import get_logger

logger = get_logger("entry")


# ── Signal Result ────────────────────────────────────────────

@dataclass
class SignalResult:
    """Complete trade signal with all details."""
    symbol: str
    direction: str               # 'buy' or 'sell'
    entry_price: float
    sl_price: float
    tp_price: float              # Target profit level
    rr_ratio: float
    setup_score: float           # 0–100
    setup_type: str              # e.g. "OB+CHoCH after EQL sweep"
    session: str
    timestamp: datetime
    confidence: str              # 'high' | 'medium'
    steps_log: Dict[str, str]    # step_name → "PASS: reason" or "FAIL: reason"


@dataclass
class AnalysisStep:
    """Result of a single analysis step."""
    name: str
    passed: bool
    reason: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class SMCEntryEngine:
    """
    Master decision module for the SMC trading bot.

    Performs a strict 9-step top-down analysis:
    1. HTF Bias (W1 + D1)               [weight: 25%]
    2. Liquidity target (D1 + H4)        [weight: 20%]
    3. Sweep confirmation (H4 + H1)      [weight: 25%]
    4. Structure shift / CHoCH+BOS (H1→M15→M5) [weight: 15%]
    5. POI entry zone (M15 + M5)         [weight: 10%]
    6. Session & time filter             [weight: 5%]
    7. Confirmation candle (M5)
    8. Risk/reward check (dynamic structural TP)
    9. Daily limits

    When all 9 pass → immediate market order.
    When steps 1-4 pass but 5-7 fail → PendingIntent (limit order at POI).
    """

    WEIGHTS = {
        "htf_bias": 25,
        "liquidity": 20,
        "sweep": 20,
        "choch": 15,
        "poi_zone": 20,
    }

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.session_mgr = SessionManager()
        self.intent_mgr = IntentManager(
            max_age_hours=config.intent_max_age_hours,
            max_total=config.max_pending_intents,
        )

    def _profile(self, symbol: str) -> Dict:
        """Return per-instrument tuning with safe defaults."""
        return get_instrument_profile(symbol)

    def _swing_params(self, symbol: str) -> tuple[int, int]:
        profile = self._profile(symbol)
        return (
            int(profile.get("swing_left_bars", self.config.swing_left_bars)),
            int(profile.get("swing_right_bars", self.config.swing_right_bars)),
        )

    def _profile_list(self, symbol: str, key: str, default: List[str]) -> List[str]:
        value = self._profile(symbol).get(key, default)
        return list(value) if isinstance(value, (list, tuple)) and value else default

    def _profile_atr(self, data: Dict[str, Optional[pd.DataFrame]], symbol: str) -> float:
        profile = self._profile(symbol)
        atr_tf = str(profile.get("atr_timeframe", "M15"))
        df = data.get(atr_tf)
        if df is None:
            df = data.get("M15")
        return calculate_atr(df, period=14) if df is not None else 0.0

    def analyze(
        self,
        symbol: str,
        data: Dict[str, Optional[pd.DataFrame]],
        daily_trades: int = 0,
        daily_drawdown_pct: float = 0.0,
        open_positions: Optional[List] = None,
    ) -> tuple[Optional[SignalResult], int, str, str]:
        """
        Complete top-down analysis for a symbol.

        Args:
            symbol: Base trading symbol (e.g. "EURUSD").
            data: Dict of timeframe → DataFrame (W1, D1, H4, H1, M15, M5).
            daily_trades: Number of trades already taken today.
            daily_drawdown_pct: Current daily drawdown percentage.
            open_positions: List of currently open positions.

        Returns:
            (SignalResult or None, failed_step_number, fail_reason, htf_bias)
        """
        pip_size = get_pip_size(symbol)
        steps: List[AnalysisStep] = []
        steps_log: Dict[str, str] = {}

        logger.info(f"[{symbol}] ═══ Analyzing ═══")
        tf_info = ", ".join([tf for tf, df in data.items() if df is not None])
        logger.info(f"[{symbol}] Data: {tf_info}")

        # ── STEP 1: HTF Bias (W1 + D1) ──
        step1 = self._step1_htf_bias(data, symbol)
        steps.append(step1)
        steps_log["htf_bias"] = f"{'PASS' if step1.passed else 'FAIL'}: {step1.reason or step1.data.get('bias', '')}"
        if not step1.passed:
            logger.info(f"[{symbol}]   ✗ Step 1/9 FAIL: {step1.reason}")
            return None, 1, step1.reason, "unknown"
        htf_bias = step1.data["bias"]
        logger.info(f"[{symbol}]   ✓ Step 1/9: HTF bias = {htf_bias} ({step1.data['confidence']})")

        # ── STEP 2: Liquidity Target (D1 + H4) ──
        step2 = self._step2_liquidity_target(data, symbol, htf_bias)
        steps.append(step2)
        steps_log["liquidity"] = f"{'PASS' if step2.passed else 'FAIL'}: {step2.reason or step2.data.get('target_type', '')}"
        if not step2.passed:
            logger.info(f"[{symbol}]   ✗ Step 2/9 FAIL: {step2.reason}")
            return None, 2, step2.reason, htf_bias
        src = step2.data.get('source', 'eqh_eql')
        target_type = step2.data.get('target_type', 'liquidity')
        logger.info(
            f"[{symbol}]   ✓ Step 2/9: Liquidity target at {step2.data.get('target_level')} "
            f"({target_type}, source={src})"
        )

        # ── STEP 3: Sweep Confirmation (H4 + H1 / Judas Swing) ──
        step3 = self._step3_sweep(data, symbol, step2.data)
        steps.append(step3)
        sweep_type = step3.data.get('sweep_type', 'standard') if step3.passed else ''
        sweep_label = 'Judas Swing' if sweep_type == 'judas_swing' else 'Sweep'
        steps_log["sweep"] = f"{'PASS' if step3.passed else 'FAIL'}: {step3.reason or sweep_label}"
        if not step3.passed:
            logger.info(f"[{symbol}]   ✗ Step 3/9 FAIL: {step3.reason}")
            return None, 3, step3.reason, htf_bias
        logger.info(f"[{symbol}]   ✓ Step 3/9: {sweep_label} confirmed at {step3.data.get('sweep_level')}")

        # ── STEP 4: Structure Shift / CHoCH (H1) ──
        step4 = self._step4_choch(data, symbol, htf_bias, step3.data)
        steps.append(step4)
        steps_log["choch"] = f"{'PASS' if step4.passed else 'FAIL'}: {step4.reason or step4.data.get('entry_direction', '')}"
        if not step4.passed:
            logger.info(f"[{symbol}]   ✗ Step 4/9 FAIL: {step4.reason}")
            return None, 4, step4.reason, htf_bias
        direction = step4.data["entry_direction"]
        logger.info(f"[{symbol}]   ✓ Step 4/9: CHoCH confirmed → {direction}")

        # ── STEP 5: POI Entry Zone (M15 + M5) ──
        step5 = self._step5_poi(data, symbol, direction, htf_bias)
        steps.append(step5)
        steps_log["poi_zone"] = f"{'PASS' if step5.passed else 'FAIL'}: {step5.reason or step5.data.get('poi_type', '')}"
        if not step5.passed:
            logger.info(f"[{symbol}]   ✗ Step 5/9 FAIL: {step5.reason}")
            return None, 5, step5.reason, htf_bias
        poi = step5.data["poi"]
        logger.info(f"[{symbol}]   ✓ Step 5/9: POI found — {step5.data.get('poi_type')}")

        # ── STEP 6: Session & Time Filter ──
        step6 = self._step6_session(symbol)
        steps.append(step6)
        steps_log["session"] = f"{'PASS' if step6.passed else 'FAIL'}: {step6.reason or step6.data.get('session', '')}"
        if not step6.passed:
            logger.info(f"[{symbol}]   ✗ Step 6/9 FAIL: {step6.reason}")
            return None, 6, step6.reason, htf_bias
        is_kz = step6.data.get("is_killzone", False)
        kz_name = step6.data.get("session", "Outside Killzone")
        logger.info(f"[{symbol}]   ✓ Step 6/9: Session — {kz_name}")

        # ── STEP 7: Confirmation Candle (M5) ──
        step7 = self._step7_confirmation(data, symbol, direction, poi, step2.data)
        steps.append(step7)
        steps_log["confirmation"] = f"{'PASS' if step7.passed else 'FAIL'}: {step7.reason or step7.data.get('candle_type', '')}"
        if not step7.passed:
            logger.info(f"[{symbol}]   ✗ Step 7/9 FAIL: {step7.reason}")
            return None, 7, step7.reason, htf_bias
        entry_price = step7.data["entry_price"]
        logger.info(f"[{symbol}]   ✓ Step 7/9: Confirmation candle at {entry_price}")

        # Calculate ATR from the instrument profile timeframe for dynamic SL buffer.
        atr_value = self._profile_atr(data, symbol)

        # ── STEP 8: Risk/Reward Check ──
        step8 = self._step8_rr(
            symbol, direction, entry_price, poi,
            step2.data, pip_size, atr_value, data
        )
        steps.append(step8)
        rr_val = step8.data.get('rr', 0) if step8.passed else 0
        rr_info = step8.reason if step8.reason else f'RR 1:{rr_val:.1f}'
        steps_log['rr'] = ('PASS' if step8.passed else 'FAIL') + ': ' + rr_info
        if not step8.passed:
            logger.info(f"[{symbol}]   ✗ Step 8/9 FAIL: {step8.reason}")
            return None, 8, step8.reason, htf_bias
        sl_price = step8.data["sl"]
        tp_price = step8.data["tp"]
        rr = step8.data["rr"]
        logger.info(f"[{symbol}]   ✓ Step 8/9: RR = 1:{rr:.1f} | SL={sl_price} TP={tp_price}")

        # ── STEP 9: Daily Limits ──
        step9 = self._step9_limits(daily_trades, daily_drawdown_pct, open_positions, symbol)
        steps.append(step9)
        steps_log["daily_limits"] = f"{'PASS' if step9.passed else 'FAIL'}: {step9.reason or 'OK'}"
        if not step9.passed:
            logger.info(f"[{symbol}]   ✗ Step 9/9 FAIL: {step9.reason}")
            return None, 9, step9.reason, htf_bias
        logger.info(f"[{symbol}]   ✓ Step 9/9: Daily limits OK")

        # ── ALL STEPS PASSED → Build Signal ──
        is_sb = step6.data.get("is_silver_bullet", False)
        score = self.score_setup(
            is_killzone=is_kz,
            bias_score=int(step1.data.get("score", 0)),
            shift_type=step4.data.get("shift_type", "choch"),
            has_displacement=step4.data.get("has_displacement", False),
            sweep_has_displacement=step3.data.get("sweep_has_displacement", False),
            poi=poi,
            atr_value=atr_value,
            in_ote=step5.data.get("in_ote", False),
            rr=rr,
            is_late_entry=step7.data.get("is_late_entry", False),
            is_silver_bullet=is_sb,
            liq_data=step2.data,
            direction=direction,
        )

        setup_parts = []
        if step5.data.get("has_ob"):
            setup_parts.append("OB")
        if step5.data.get("has_fvg"):
            setup_parts.append("FVG")
        setup_parts.append("CHoCH")
        if step7.data.get("is_late_entry"):
            setup_parts.append("Late")

        # Label the sweep source
        sweep_src = step3.data.get("sweep_type", "standard")
        if sweep_src == "judas_swing":
            sweep_label = "Asian " + ("Low" if step3.data.get("sweep_direction") == "below" else "High")
        else:
            sweep_label = "BSL" if step3.data.get("sweep_direction") == "above" else "SSL"
        setup_type = f"{'+'.join(setup_parts)} after {sweep_label} sweep"

        confidence = "high" if score >= 85 else "medium"

        signal = SignalResult(
            symbol=symbol,
            direction=direction,
            entry_price=round(entry_price, 5),
            sl_price=round(sl_price, 5),
            tp_price=round(tp_price, 5),
            rr_ratio=round(rr, 2),
            setup_score=round(score, 1),
            setup_type=setup_type,
            session=kz_name,
            timestamp=datetime.now(timezone.utc),
            confidence=confidence,
            steps_log=steps_log,
        )

        logger.info(f"[{symbol}]   ★ SIGNAL: {direction.upper()} @ {entry_price}")
        logger.info(f"[{symbol}]     SL={sl_price} TP={tp_price} RR=1:{rr:.1f} Score={score}")
        return signal, 9, "All steps passed", htf_bias

    # ── Individual Steps ─────────────────────────────────────

    def _step1_htf_bias(self, data: Dict, symbol: str) -> AnalysisStep:
        """STEP 1: Determine HTF bias from W1 + D1 (+ H4)."""
        df_w1 = data.get("W1")
        df_d1 = data.get("D1")
        df_h4 = data.get("H4")

        if df_w1 is None or df_d1 is None:
            return AnalysisStep("htf_bias", False, "Missing W1 or D1 data")

        lb, rb = self._swing_params(symbol)
        min_bias_score = int(
            self._profile(symbol).get(
                "min_bias_score",
                getattr(self.config, "min_bias_score", 4),
            )
        )
        bias, confidence, score = get_market_bias(
            df_w1, df_d1, df_h4,
            lb,
            rb,
            min_bias_score=min_bias_score,
            strict=getattr(self.config, "strict_pivot_bias", False),
            allow_weak_bias=not getattr(self.config, "require_aligned_htf_bias", True),
        )

        if bias == "ranging":
            return AnalysisStep("htf_bias", False, "No HTF data to infer directional draw")

        return AnalysisStep("htf_bias", True, data={"bias": bias, "confidence": confidence, "score": score})

    def _step2_liquidity_target(self, data: Dict, symbol: str, htf_bias: str) -> AnalysisStep:
        """STEP 2: Find nearest unswept liquidity target on D1/H4 + Asian Range."""
        liq = LiquidityEngine(symbol)
        lb, rb = self._swing_params(symbol)

        eqh_eql_result = None
        asian_result = None
        current_ref = None
        for tf in ("M5", "M15", "H1"):
            candidate = data.get(tf)
            if candidate is not None and len(candidate) > 0:
                current_ref = candidate
                break
        current_price = (
            float(current_ref["close"].iloc[-1])
            if current_ref is not None and len(current_ref) > 0
            else None
        )

        # --- Source 1: EQH/EQL on H4/D1 (existing logic) ---
        for label, tf in [("H4", "H4"), ("D1", "D1")]:
            df = data.get(tf)
            if df is None or len(df) < 20:
                continue

            sh = detect_swing_highs(df, lb, rb)
            sl = detect_swing_lows(df, lb, rb)

            if htf_bias == "bullish":
                eqls = liq.find_eql(df, sl, self.config.eqh_eql_tolerance_pips)
                unswept = [
                    e for e in eqls
                    if not e.is_swept and (current_price is None or e.price < current_price)
                ]
                if unswept:
                    target = max(unswept, key=lambda e: e.price)
                    bsl = liq.find_bsl(df, sh)
                    eqh_eql_result = AnalysisStep("liquidity", True, data={
                        "target_level": target.price,
                        "target_type": "EQL (sell-side)",
                        "target": target,
                        "tp_levels": bsl,
                        "swing_highs": sh,
                        "swing_lows": sl,
                        "source": "eqh_eql",
                    })
                    break
            else:
                eqhs = liq.find_eqh(df, sh, self.config.eqh_eql_tolerance_pips)
                unswept = [
                    e for e in eqhs
                    if not e.is_swept and (current_price is None or e.price > current_price)
                ]
                if unswept:
                    target = min(unswept, key=lambda e: e.price)
                    ssl = liq.find_ssl(df, sl)
                    eqh_eql_result = AnalysisStep("liquidity", True, data={
                        "target_level": target.price,
                        "target_type": "EQH (buy-side)",
                        "target": target,
                        "tp_levels": ssl,
                        "swing_highs": sh,
                        "swing_lows": sl,
                        "source": "eqh_eql",
                    })
                    break

        # --- Source 2: Asian Range on H1 (ICT Power of 3) ---
        if self.config.use_asian_range:
            df_h1 = data.get("H1")
            asian = liq.get_asian_range(df_h1)

            if asian is not None:
                if (
                    htf_bias == "bullish"
                    and not asian.get("low_swept", False)
                    and (current_price is None or asian["low"] < current_price)
                ):
                    # Bullish → target Asian Low (sell-side liquidity to sweep before going up)
                    # TP: Asian High or BSL from higher TF
                    tp = [asian["high"]]
                    if eqh_eql_result and eqh_eql_result.data.get("tp_levels"):
                        tp.extend(eqh_eql_result.data["tp_levels"])
                    asian_result = AnalysisStep("liquidity", True, data={
                        "target_level": asian["low"],
                        "target_type": "Asian Low (sell-side)",
                        "target": None,  # No EqualLevel object for Asian
                        "tp_levels": tp,
                        "asian_range": asian,
                        "source": "asian_range",
                    })
                elif (
                    htf_bias == "bearish"
                    and not asian.get("high_swept", False)
                    and (current_price is None or asian["high"] > current_price)
                ):
                    # Bearish → target Asian High (buy-side liquidity to sweep before going down)
                    tp = [asian["low"]]
                    if eqh_eql_result and eqh_eql_result.data.get("tp_levels"):
                        tp.extend(eqh_eql_result.data["tp_levels"])
                    asian_result = AnalysisStep("liquidity", True, data={
                        "target_level": asian["high"],
                        "target_type": "Asian High (buy-side)",
                        "target": None,
                        "tp_levels": tp,
                        "asian_range": asian,
                        "source": "asian_range",
                    })

        previous_htf_result = None
        if not eqh_eql_result and not asian_result:
            previous_htf_result = self._previous_htf_liquidity_target(data, htf_bias)
            if previous_htf_result is None:
                previous_htf_result = self._previous_intraday_liquidity_target(data, htf_bias, symbol)

        # --- Decision: pick the best target ---
        if asian_result and eqh_eql_result:
            # Both available: prefer Asian Range during killzones (more intraday-relevant)
            is_kz = self.session_mgr.is_in_killzone(symbol)
            is_gold = "XAU" in symbol.upper()
            if (is_kz and self.config.prefer_asian_in_killzone) or is_gold:
                return asian_result
            else:
                return eqh_eql_result
        elif asian_result:
            return asian_result
        elif eqh_eql_result:
            return eqh_eql_result
        elif previous_htf_result:
            return previous_htf_result

        return AnalysisStep("liquidity", False, "No unswept liquidity targets found")

    def _previous_htf_liquidity_target(self, data: Dict, htf_bias: str) -> Optional[AnalysisStep]:
        """
        Use previous HTF highs/lows as external liquidity when EQH/EQL is absent.

        This is not the old random swing fallback. Previous day/week/session
        extremes are objective liquidity pools that price commonly raids.
        """
        candidates = []
        tp_levels = []

        for tf in ["D1", "H4", "W1"]:
            df = data.get(tf)
            if df is None or len(df) < 3:
                continue

            current_price = float(df["close"].iloc[-1])
            completed = df.iloc[:-1] if len(df) > 3 else df
            recent = completed.tail(12)

            if htf_bias == "bullish":
                lows = [float(v) for v in recent["low"].values if float(v) < current_price]
                highs = [float(v) for v in recent["high"].values if float(v) > current_price]
                if lows:
                    level = max(lows)
                    candidates.append((abs(current_price - level), level, tf))
                tp_levels.extend(sorted(highs))
            else:
                highs = [float(v) for v in recent["high"].values if float(v) > current_price]
                lows = [float(v) for v in recent["low"].values if float(v) < current_price]
                if highs:
                    level = min(highs)
                    candidates.append((abs(level - current_price), level, tf))
                tp_levels.extend(sorted(lows, reverse=True))

        if not candidates:
            return None

        _distance, target_level, tf = sorted(candidates, key=lambda item: item[0])[0]
        target_type = (
            f"Previous {tf} Low (sell-side)"
            if htf_bias == "bullish"
            else f"Previous {tf} High (buy-side)"
        )

        return AnalysisStep("liquidity", True, data={
            "target_level": target_level,
            "target_type": target_type,
            "target": None,
            "tp_levels": tp_levels,
            "source": "previous_htf_extreme",
        })

    def _previous_intraday_liquidity_target(
        self, data: Dict, htf_bias: str, symbol: str
    ) -> Optional[AnalysisStep]:
        """Use H1/M15 session extremes when HTF pools are not visible."""
        candidates = []
        tp_levels = []

        for tf in self._profile_list(symbol, "intraday_liquidity_tf", ["H1", "M15"]):
            df = data.get(tf)
            if df is None or len(df) < 20:
                continue

            current_price = float(df["close"].iloc[-1])
            recent = df.iloc[:-1].tail(48)

            if htf_bias == "bullish":
                lows = [float(v) for v in recent["low"].values if float(v) < current_price]
                highs = [float(v) for v in recent["high"].values if float(v) > current_price]
                if lows:
                    level = max(lows)
                    candidates.append((abs(current_price - level), level, tf))
                tp_levels.extend(sorted(highs))
            else:
                highs = [float(v) for v in recent["high"].values if float(v) > current_price]
                lows = [float(v) for v in recent["low"].values if float(v) < current_price]
                if highs:
                    level = min(highs)
                    candidates.append((abs(level - current_price), level, tf))
                tp_levels.extend(sorted(lows, reverse=True))

        if not candidates:
            return None

        _distance, target_level, tf = sorted(candidates, key=lambda item: item[0])[0]
        target_type = (
            f"Previous {tf} Low (intraday sell-side)"
            if htf_bias == "bullish"
            else f"Previous {tf} High (intraday buy-side)"
        )
        return AnalysisStep("liquidity", True, data={
            "target_level": target_level,
            "target_type": target_type,
            "target": None,
            "tp_levels": tp_levels,
            "source": "previous_intraday_extreme",
        })

    def _step3_sweep(self, data: Dict, symbol: str, liq_data: Dict) -> AnalysisStep:
        """STEP 3: Confirm liquidity sweep (Judas Swing for Asian Range)."""
        target_level = liq_data.get("target_level")
        target_type = liq_data.get("target_type", "")
        source = liq_data.get("source", "eqh_eql")
        liq = LiquidityEngine(symbol)

        direction = "below" if "sell-side" in target_type else "above"

        # Note: Asian-range targets are NOT short-circuited here. Every sweep —
        # including the Judas Swing on the Asian range — must pass the same
        # wick-close and optional displacement/inducement filters below.

        # --- Multi-Timeframe sweep detection (profile-driven) ---
        # Checking lower TFs captures intraday sweeps; indices can stay on H4/H1.
        for tf in self._profile_list(symbol, "sweep_tf", ["H1", "M15", "M5"]):
            df = data.get(tf)
            if df is None or len(df) < 10:
                continue

            # Larger lookback for H1, tighter for lower TFs
            lb_val = 8 if tf == "H4" else 15 if tf == "H1" else 10
            disp_atr = getattr(self.config, 'min_sweep_displacement_atr', 0.8)
            sweep = liq.detect_sweep(
                df,
                target_level,
                direction,
                lookback=lb_val,
                require_displacement=getattr(self.config, "require_sweep_displacement", False),
                displacement_atr_multiple=disp_atr,
                displacement_window=3,
            )
            
            if sweep is not None:
                if self.config.require_inducement and not self._has_inducement_before_sweep(df, sweep.index, target_level, direction):
                    logger.debug(f"  Sweep on {tf} rejected: no inducement before sweep")
                    continue

                logger.debug(f"  Sweep detected on {tf} for {symbol} at {target_level}")
                return AnalysisStep("sweep", True, data={
                    "sweep_level": target_level,
                    "sweep_direction": direction,
                    "sweep": sweep,
                    "sweep_has_displacement": getattr(sweep, "has_displacement", False),
                    "timeframe": tf,
                    "sweep_type": "judas_swing" if source == "asian_range" else "standard",
                    "asian_range": liq_data.get("asian_range"),
                })

        ref_df = None
        for tf in ("M5", "M15", "H1"):
            candidate = data.get(tf)
            if candidate is not None and len(candidate) > 0:
                ref_df = candidate
                break
        if ref_df is not None and len(ref_df) > 0 and target_level:
            current_price = float(ref_df["close"].iloc[-1])
            distance = abs(current_price - target_level)
            return AnalysisStep(
                "sweep",
                False,
                f"No valid wick-close sweep at {target_level} "
                f"(current {current_price:.5f}, distance {distance:.5f})",
            )

        return AnalysisStep("sweep", False, f"No valid wick-close sweep at {target_level}")

    def _has_inducement_before_sweep(
        self,
        df: pd.DataFrame,
        sweep_index: int,
        target_level: float,
        sweep_direction: str,
        lookback: int = 30,
    ) -> bool:
        """Require an internal liquidity grab before the external sweep."""
        start = max(0, sweep_index - lookback)
        before_sweep = df.iloc[start:sweep_index + 1].reset_index(drop=True)
        if len(before_sweep) < 8:
            return False

        swing_highs = detect_swing_highs(before_sweep, left_bars=1, right_bars=1)
        swing_lows = detect_swing_lows(before_sweep, left_bars=1, right_bars=1)
        highs = before_sweep["high"].values
        lows = before_sweep["low"].values

        if sweep_direction == "below":
            internal_lows = [sl for sl in swing_lows if sl.price > target_level]
            return any(
                any(lows[j] < sl.price for j in range(sl.index + 1, len(before_sweep)))
                for sl in internal_lows
            )

        internal_highs = [sh for sh in swing_highs if sh.price < target_level]
        return any(
            any(highs[j] > sh.price for j in range(sh.index + 1, len(before_sweep)))
            for sh in internal_highs
        )

    def _step4_choch(self, data: Dict, symbol: str, htf_bias: str, sweep_data: Dict) -> AnalysisStep:
        """STEP 4: Confirm structure shift (CHoCH or BOS) on H1→M15→M5."""
        sweep_dir = sweep_data.get("sweep_direction")
        sweep_event = sweep_data.get("sweep")
        sweep_type = sweep_data.get("sweep_type", "standard")

        # Swept above (BSL) → expect bearish shift
        # Swept below (SSL/Asian Low) → expect bullish shift
        if sweep_dir == "above":
            expected_shift = "bearish"
            entry_direction = "sell"
        else:
            expected_shift = "bullish"
            entry_direction = "buy"

        # The structure shift must occur AFTER the sweep, chronologically. The
        # sweep may have been found on a different timeframe than the CHoCH, so
        # we anchor on its TIMESTAMP, not its bar index (indices differ per TF).
        # For judas swings we accept any recent shift (after_time=None).
        after_time = (
            sweep_event.timestamp
            if (sweep_event is not None and sweep_type != "judas_swing")
            else None
        )

        lb, rb = self._swing_params(symbol)
        choch_tfs = self._profile_list(symbol, "structure_tf", ["H1", "M15", "M5"])

        # --- Multi-TF CHoCH detection ---
        if self.config.multi_tf_choch:
            shift = detect_structure_shift(
                data, expected_shift, after_index=0, after_time=after_time,
                left_bars=lb, right_bars=rb,
                displacement_multiplier=self.config.mss_displacement_multiplier,
                timeframes=choch_tfs,
            )
            if shift:
                shift_type = shift["type"].upper()  # 'CHOCH' or 'BOS'
                shift_tf = shift["timeframe"]
                has_disp = shift.get("has_displacement", False)
                
                # Filter multi-TF CHoCH by displacement if required
                if shift["type"] == "choch" and self.config.require_mss_displacement and not has_disp:
                    logger.debug(f"  [{symbol}] {shift_type} on {shift_tf} rejected: no displacement")
                else:
                    logger.debug(f"  {shift_type} on {shift_tf} for {symbol} (Displacement: {has_disp})")
                    return AnalysisStep("choch", True, data={
                        "choch": shift["event"],
                        "entry_direction": entry_direction,
                        "shift_type": shift["type"],
                        "shift_tf": shift_tf,
                        "has_displacement": has_disp,
                    })

        # --- Fallback: H1-only CHoCH (original logic) ---
        df_h1 = data.get("H1")
        if df_h1 is not None and len(df_h1) >= 20:
            sh = detect_swing_highs(df_h1, lb, rb)
            sl = detect_swing_lows(df_h1, lb, rb)
            current_trend = "bullish" if expected_shift == "bearish" else "bearish"
            choch_events = detect_choch(
                df_h1, sh, sl, current_trend,
                displacement_multiplier=self.config.mss_displacement_multiplier,
            )

            for ch in choch_events:
                if ch.direction == expected_shift:
                    # Filter by displacement if required
                    if self.config.require_mss_displacement and not ch.has_displacement:
                        logger.debug(f"  [{symbol}] {expected_shift.upper()} CHoCH on H1 rejected: no displacement")
                        continue

                    if sweep_type == "judas_swing" or after_time is None or ch.timestamp >= after_time:
                        return AnalysisStep("choch", True, data={
                            "choch": ch,
                            "entry_direction": entry_direction,
                            "shift_type": "choch",
                            "shift_tf": "H1",
                        })

            # --- BOS as alternative to CHoCH ---
            if self.config.use_bos_as_entry:
                bos = detect_bos_after_index(
                    df_h1, 0, expected_shift, lb, rb, after_time=after_time
                )
                if bos:
                    return AnalysisStep("choch", True, data={
                        "choch": bos,
                        "entry_direction": entry_direction,
                        "shift_type": "bos",
                        "shift_tf": "H1",
                    })

        # Diagnostic info for logs
        df_h1 = data.get("H1")
        reason = f"No {expected_shift} structure shift (CHoCH/BOS) found after sweep"
        if df_h1 is not None and len(df_h1) > 0:
            cur_price = df_h1['close'].iloc[-1]
            reason += f" (current {cur_price:.5f})"

        return AnalysisStep("choch", False, reason)

    def _step5_poi(self, data: Dict, symbol: str, direction: str, htf_bias: str) -> AnalysisStep:
        """STEP 5: Find fresh OB or FVG on M15/M5 in the entry direction."""
        poi = POIEngine(symbol)
        lb, rb = self._swing_params(symbol)

        best_poi = None
        poi_type = ""
        has_ob = False
        has_fvg = False

        poi_tfs = self._profile_list(symbol, "poi_tf", ["M15", "M5"])

        for tf in poi_tfs:
            df = data.get(tf)
            if df is None or len(df) < 20:
                continue

            # Premium/discount zones from the CURRENT working swing leg
            # (most recent swing high/low), not the global lookback extremes.
            sh = detect_swing_highs(df, lb, rb)
            sl = detect_swing_lows(df, lb, rb)
            dealing_range = recent_dealing_range(sh, sl)
            if dealing_range:
                hi, lo = dealing_range
                zones = get_premium_discount_zones(hi, lo)
            else:
                zones = {}

            if direction == "buy":
                obs = poi.find_bullish_ob(df, self.config.ob_strength_min)
                fresh_obs = [ob for ob in obs if ob.is_fresh and not ob.is_breaker]
                # Accept OBs in discount zone OR OTE zone (0.618-0.786 fib)
                valid_obs = [ob for ob in fresh_obs if (
                    not zones or is_in_discount(ob.bottom, zones) or is_in_ote(ob.bottom, zones)
                )]
                if valid_obs:
                    best_poi = valid_obs[-1]
                    has_ob = True
                    poi_type = f"Bullish OB on {tf}"

                fvgs = poi.find_fvg(df, self.config.fvg_min_size_pips)
                bullish_fvgs = [f for f in fvgs if f.type == "bullish" and not f.is_filled]
                valid_fvgs = [f for f in bullish_fvgs if (
                    not zones or is_in_discount(f.bottom, zones) or is_in_ote(f.bottom, zones)
                )]
                if valid_fvgs:
                    has_fvg = True
                    if best_poi is None:
                        best_poi = valid_fvgs[-1]
                        poi_type = f"Bullish FVG on {tf}"
                    else:
                        poi_type += " + FVG"

                # Breaker blocks as fallback POI
                if best_poi is None and self.config.use_breaker_blocks:
                    all_obs = poi.find_bearish_ob(df, self.config.ob_strength_min)
                    breakers = poi.find_breaker_blocks(df, all_obs)
                    bull_breakers = [b for b in breakers if b.type == "bullish"]
                    valid_breakers = [b for b in bull_breakers if (
                        not zones or is_in_discount(b.bottom, zones) or is_in_ote(b.bottom, zones)
                    )]
                    if valid_breakers:
                        bb = valid_breakers[-1]
                        best_poi = OrderBlock(
                            type="bullish", top=bb.top, bottom=bb.bottom,
                            index=bb.index, timestamp=bb.timestamp,
                            is_fresh=True, is_breaker=False, strength=2.0,
                        )
                        has_ob = True
                        poi_type = f"Bullish Breaker on {tf}"

                # Mitigation blocks as fallback POI
                if best_poi is None and self.config.use_mitigation_blocks:
                    all_obs = poi.find_bullish_ob(df, self.config.ob_strength_min)
                    mitigations = poi.find_mitigation_blocks(df, all_obs)
                    bull_mitigations = [m for m in mitigations if m.type == "bullish"]
                    valid_mitigations = [m for m in bull_mitigations if (
                        not zones or is_in_discount(m.bottom, zones) or is_in_ote(m.bottom, zones)
                    )]
                    if valid_mitigations:
                        bm = valid_mitigations[-1]
                        best_poi = OrderBlock(
                            type="bullish", top=bm.top, bottom=bm.bottom,
                            index=bm.index, timestamp=bm.timestamp,
                            is_fresh=True, is_breaker=False, strength=2.0,
                        )
                        has_ob = True
                        poi_type = f"Bullish Mitigation on {tf}"
            else:
                obs = poi.find_bearish_ob(df, self.config.ob_strength_min)
                fresh_obs = [ob for ob in obs if ob.is_fresh and not ob.is_breaker]
                valid_obs = [ob for ob in fresh_obs if (
                    not zones or is_in_premium(ob.top, zones) or is_in_ote(ob.top, zones)
                )]
                if valid_obs:
                    best_poi = valid_obs[-1]
                    has_ob = True
                    poi_type = f"Bearish OB on {tf}"

                fvgs = poi.find_fvg(df, self.config.fvg_min_size_pips)
                bearish_fvgs = [f for f in fvgs if f.type == "bearish" and not f.is_filled]
                valid_fvgs = [f for f in bearish_fvgs if (
                    not zones or is_in_premium(f.top, zones) or is_in_ote(f.top, zones)
                )]
                if valid_fvgs:
                    has_fvg = True
                    if best_poi is None:
                        best_poi = valid_fvgs[-1]
                        poi_type = f"Bearish FVG on {tf}"
                    else:
                        poi_type += " + FVG"

                # Breaker blocks as fallback POI
                if best_poi is None and self.config.use_breaker_blocks:
                    all_obs = poi.find_bullish_ob(df, self.config.ob_strength_min)
                    breakers = poi.find_breaker_blocks(df, all_obs)
                    bear_breakers = [b for b in breakers if b.type == "bearish"]
                    valid_breakers = [b for b in bear_breakers if (
                        not zones or is_in_premium(b.top, zones) or is_in_ote(b.top, zones)
                    )]
                    if valid_breakers:
                        bb = valid_breakers[-1]
                        best_poi = OrderBlock(
                            type="bearish", top=bb.top, bottom=bb.bottom,
                            index=bb.index, timestamp=bb.timestamp,
                            is_fresh=True, is_breaker=False, strength=2.0,
                        )
                        has_ob = True
                        poi_type = f"Bearish Breaker on {tf}"

                # Mitigation blocks as fallback POI
                if best_poi is None and self.config.use_mitigation_blocks:
                    all_obs = poi.find_bearish_ob(df, self.config.ob_strength_min)
                    mitigations = poi.find_mitigation_blocks(df, all_obs)
                    bear_mitigations = [m for m in mitigations if m.type == "bearish"]
                    valid_mitigations = [m for m in bear_mitigations if (
                        not zones or is_in_premium(m.top, zones) or is_in_ote(m.top, zones)
                    )]
                    if valid_mitigations:
                        bm = valid_mitigations[-1]
                        best_poi = OrderBlock(
                            type="bearish", top=bm.top, bottom=bm.bottom,
                            index=bm.index, timestamp=bm.timestamp,
                            is_fresh=True, is_breaker=False, strength=2.0,
                        )
                        has_ob = True
                        poi_type = f"Bearish Mitigation on {tf}"

            if best_poi:
                break

        if best_poi is None:
            return AnalysisStep("poi_zone", False, "No fresh OB/FVG/Breaker/Mitigation in correct zone")

        # Consequent Encroachment (CE) level = 50% of the POI zone
        ce_level = (best_poi.top + best_poi.bottom) / 2

        return AnalysisStep("poi_zone", True, data={
            "poi": best_poi, "poi_type": poi_type,
            "has_ob": has_ob, "has_fvg": has_fvg,
            "ce_level": round(ce_level, 5),
            "in_ote": bool(zones) and is_in_ote(ce_level, zones),
        })

    def _step6_session(self, symbol: str) -> AnalysisStep:
        """STEP 6: Check session and time filters."""
        if self.session_mgr.is_weekend():
            if getattr(self.config, "ignore_weekend_filter", False):
                return AnalysisStep("session", True, data={
                    "session": "Backtest",
                    "is_killzone": False,
                    "is_silver_bullet": False,
                })
            return AnalysisStep("session", False, "Weekend — market closed")

        session_name = self.session_mgr.get_current_session()

        is_silver_bullet = False
        if getattr(self.config, 'use_silver_bullet', False):
            is_silver_bullet = self.session_mgr.is_in_silver_bullet(symbol)

        is_kz = self.session_mgr.should_trade_now(
            symbol, 
            self.config.trade_london_kz, 
            self.config.trade_ny_kz,
            self.config.trade_asian_kz,
            self.config.trade_london_close,
        ) or is_silver_bullet

        kz = self.session_mgr.get_active_killzone(symbol)
        display_name = kz if kz else session_name

        if not is_kz:
            # Allow outside killzone if configured — passes but marks for score penalty
            if getattr(self.config, 'allow_outside_killzone', False):
                logger.debug(f"  [{symbol}] Outside killzone but allow_outside_killzone=True")
                return AnalysisStep("session", True, data={
                    "session": display_name, "is_killzone": False, "is_silver_bullet": False
                })
            return AnalysisStep("session", False, f"Outside enabled killzone ({display_name})")

        return AnalysisStep("session", True, data={"session": display_name, "is_killzone": is_kz, "is_silver_bullet": is_silver_bullet})

    def _step7_confirmation(
        self, data: Dict, symbol: str, direction: str, poi: Any,
        liq_data: Optional[Dict] = None,
    ) -> AnalysisStep:
        """STEP 7: Look for confirmation on the profile entry timeframe."""
        profile = self._profile(symbol)
        entry_tf = str(profile.get("confirmation_tf", "M5"))
        use_m1_fractal = bool(profile.get("use_m1_fractal", True))
        df_m1 = data.get("M1")
        poi_top = poi.top if hasattr(poi, "top") else 0
        poi_bottom = poi.bottom if hasattr(poi, "bottom") else 0
        poi_time = getattr(poi, "timestamp", None)

        # Try Expert M1 Fractal Entry first
        if use_m1_fractal and df_m1 is not None and len(df_m1) > 50 and poi_time is not None:
            # Ensure timezone-aware comparison with M1 times
            target_time = pd.Timestamp(poi_time)
            if df_m1["time"].dt.tz is not None:
                if target_time.tzinfo is None:
                    target_time = target_time.tz_localize("UTC")
                else:
                    target_time = target_time.tz_convert("UTC")
            else:
                if target_time.tzinfo is not None:
                    target_time = target_time.tz_localize(None)

            df_recent = df_m1[df_m1["time"] > target_time].copy()
            if len(df_recent) >= 5:
                # Did price tap the POI on M1?
                tap_idx = -1
                for i, row in enumerate(df_recent.itertuples()):
                    if direction == "buy" and row.low <= poi_top:
                        tap_idx = i
                        break
                    elif direction == "sell" and row.high >= poi_bottom:
                        tap_idx = i
                        break
                
                if tap_idx != -1:
                    df_after_tap = df_recent.iloc[tap_idx:].copy()
                    if len(df_after_tap) >= 10:
                        sh = detect_swing_highs(df_after_tap, left_bars=1, right_bars=1)
                        sl = detect_swing_lows(df_after_tap, left_bars=1, right_bars=1)
                        current_trend = "bearish" if direction == "buy" else "bullish"
                        expected_choch = "bullish" if direction == "buy" else "bearish"
                        choch_events = detect_choch(df_after_tap, sh, sl, current_trend)
                        if any(ch.direction == expected_choch for ch in choch_events):
                            entry = float(df_after_tap.iloc[-1]['close'])
                            return AnalysisStep("confirmation", True, data={
                                "entry_price": entry,
                                "candle_type": "m1_fractal_choch",
                            })

        # Fallback to profile confirmation timeframe.
        df_entry = data.get(entry_tf)
        if df_entry is None or len(df_entry) < 5:
            return AnalysisStep("confirmation", False, f"No {entry_tf} data")

        opens = df_entry["open"].values
        closes = df_entry["close"].values
        highs = df_entry["high"].values
        lows = df_entry["low"].values


        poi_top = poi.top if hasattr(poi, "top") else 0
        poi_bottom = poi.bottom if hasattr(poi, "bottom") else 0

        lookback = int(profile.get("confirmation_lookback_bars", self.config.confirmation_lookback_bars))
        lookback = min(max(lookback, 1), len(df_entry) - 1)
        for i in range(-lookback, 0):
            idx = len(df_entry) + i
            if idx < 1:
                continue

            body_size = abs(closes[idx] - opens[idx])
            candle_range = highs[idx] - lows[idx]
            if candle_range == 0:
                continue
            body_ratio = body_size / candle_range

            if direction == "buy":
                is_bullish = closes[idx] > opens[idx]
                strong_close = is_bullish and body_ratio >= 0.5
                near_poi = lows[idx] <= poi_top
                # Engulfing check
                prev_bearish = closes[idx - 1] < opens[idx - 1]
                engulfing = (prev_bearish and is_bullish and
                             closes[idx] > opens[idx - 1] and
                             opens[idx] < closes[idx - 1])
                # Pin bar / hammer: long lower wick, small body at top
                lower_wick = min(opens[idx], closes[idx]) - lows[idx]
                upper_wick = highs[idx] - max(opens[idx], closes[idx])
                pin_bar = (lower_wick > 2 * body_size and
                           upper_wick < body_size and
                           is_bullish)

                if near_poi and (engulfing or strong_close or pin_bar):
                    ctype = "engulfing" if engulfing else ("pin_bar" if pin_bar else "strong_close")
                    return AnalysisStep("confirmation", True, data={
                        "entry_price": closes[idx],
                        "candle_type": f"{entry_tf.lower()}_{ctype}",
                    })
            else:
                is_bearish = closes[idx] < opens[idx]
                strong_close = is_bearish and body_ratio >= 0.5
                near_poi = highs[idx] >= poi_bottom
                prev_bullish = closes[idx - 1] > opens[idx - 1]
                engulfing = (prev_bullish and is_bearish and
                             closes[idx] < opens[idx - 1] and
                             opens[idx] > closes[idx - 1])
                # Inverted pin bar: long upper wick
                upper_wick = highs[idx] - max(opens[idx], closes[idx])
                lower_wick = min(opens[idx], closes[idx]) - lows[idx]
                pin_bar = (upper_wick > 2 * body_size and
                           lower_wick < body_size and
                           is_bearish)

                if near_poi and (engulfing or strong_close or pin_bar):
                    ctype = "engulfing" if engulfing else ("pin_bar" if pin_bar else "strong_close")
                    return AnalysisStep("confirmation", True, data={
                        "entry_price": closes[idx],
                        "candle_type": f"{entry_tf.lower()}_{ctype}",
                    })

        late = self._late_entry_confirmation(data, symbol, direction, poi, liq_data or {})
        if late.passed:
            return late

        return AnalysisStep("confirmation", False, f"No strong {entry_tf} confirmation candle from POI")

    def _late_entry_confirmation(
        self, data: Dict, symbol: str, direction: str, poi: Any, liq_data: Dict,
    ) -> AnalysisStep:
        """Controlled continuation entry when the ideal tap happened recently."""
        if not getattr(self.config, "enable_late_entry", False):
            return AnalysisStep("confirmation", False, "Late entry disabled")

        max_bars = int(getattr(self.config, "late_entry_max_m5_bars", 6) or 0)
        if max_bars <= 0:
            return AnalysisStep("confirmation", False, "Late entry window disabled")

        profile = self._profile(symbol)
        late_tf = str(profile.get("late_entry_tf", profile.get("confirmation_tf", "M5")))
        df_late = data.get(late_tf)
        if df_late is None or len(df_late) < max_bars + 5:
            return AnalysisStep("confirmation", False, f"No {late_tf} data for late entry")

        poi_top = poi.top if hasattr(poi, "top") else 0
        poi_bottom = poi.bottom if hasattr(poi, "bottom") else 0
        if not poi_top or not poi_bottom:
            return AnalysisStep("confirmation", False, "No POI bounds for late entry")

        recent = df_late.tail(max_bars + 1)
        touched = (
            (recent["low"] <= poi_top).any()
            if direction == "buy"
            else (recent["high"] >= poi_bottom).any()
        )
        if not touched:
            return AnalysisStep("confirmation", False, "No recent POI tap for late entry")

        entry = float(df_late["close"].iloc[-1])
        atr = calculate_atr(df_late, period=14)
        if atr > 0:
            if direction == "buy":
                distance_from_poi = max(0.0, entry - poi_top)
            else:
                distance_from_poi = max(0.0, poi_bottom - entry)
            max_distance = getattr(self.config, "late_entry_max_atr_distance", 0.5) * atr
            if distance_from_poi > max_distance:
                return AnalysisStep(
                    "confirmation",
                    False,
                    f"Late entry too far from POI ({distance_from_poi:.5f} > {max_distance:.5f})",
                )

        tp_levels = liq_data.get("tp_levels", [])
        valid_tp = []
        if tp_levels:
            if direction == "buy":
                valid_tp = [t for t in tp_levels if t > entry]
            else:
                valid_tp = [t for t in tp_levels if t < entry]
        tp = valid_tp[0] if valid_tp else None
        target = liq_data.get("target_level")
        if tp is not None and target is not None:
            full_path = abs(tp - target)
            traveled = abs(entry - target)
            if full_path > 0:
                progress = traveled / full_path
                max_progress = getattr(self.config, "late_entry_max_tp_progress", 0.5)
                if progress > max_progress:
                    return AnalysisStep(
                        "confirmation",
                        False,
                        f"Late entry move too mature ({progress:.0%} to TP)",
                    )

        recent_closes = df_late["close"].tail(3).values
        if direction == "buy" and not (recent_closes[-1] > recent_closes[0]):
            return AnalysisStep("confirmation", False, "Late buy lacks continuation")
        if direction == "sell" and not (recent_closes[-1] < recent_closes[0]):
            return AnalysisStep("confirmation", False, "Late sell lacks continuation")

        return AnalysisStep("confirmation", True, data={
            "entry_price": entry,
            "candle_type": f"{late_tf.lower()}_late_entry_continuation",
            "is_late_entry": True,
        })

    def _step8_rr(
        self, symbol: str, direction: str, entry: float,
        poi: Any, liq_data: Dict, pip_size: float, atr_value: float,
        data: Optional[Dict] = None,
    ) -> AnalysisStep:
        """STEP 8: Calculate SL, TP1 (structural), TP2 (full run), and verify RR."""
        # Use Consequent Encroachment (CE) if enabled for limit/intent entries
        # entry_price passed in is usually the confirmation candle close or boundary
        final_entry = entry
        
        is_limit = liq_data.get("is_limit", False)
        ce_level = liq_data.get("ce_level")
        
        if self.config.use_consequent_encroachment and ce_level:
            # If it's a limit order or we're far from the CE level, we can suggest it
            if is_limit or abs(entry - ce_level) > (2 * pip_size):
                final_entry = ce_level
                logger.debug(f"  [{symbol}] Adjusting entry to Consequent Encroachment (50%): {final_entry}")

        # 1. Calculate structural SL with ATR buffer only.
        atr_multiplier = getattr(self.config, "atr_sl_multiplier", 0.5)
        buffer = atr_multiplier * atr_value if atr_value > 0 else 0.0

        # 2. Calculate structural SL and structural risk.
        if direction == "buy":
            if not hasattr(poi, "bottom"):
                return AnalysisStep("rr", False, "Missing structural POI bottom for buy SL")
            sl_structural = poi.bottom - buffer
        else:
            if not hasattr(poi, "top"):
                return AnalysisStep("rr", False, "Missing structural POI top for sell SL")
            sl_structural = poi.top + buffer
        
        risk_structural = abs(final_entry - sl_structural)
        if risk_structural <= 0:
            return AnalysisStep("rr", False, "Invalid risk calculation (SL = entry)")
        if direction == "buy" and sl_structural >= final_entry:
            return AnalysisStep("rr", False, "Structural SL is not below buy entry")
        if direction == "sell" and sl_structural <= final_entry:
            return AnalysisStep("rr", False, "Structural SL is not above sell entry")

        risk_pips_raw = risk_structural / pip_size

        profile = self._profile(symbol)

        min_sl_atr = profile.get("min_sl_atr_mult", getattr(self.config, "min_sl_atr", 1.5))
        if atr_value > 0 and min_sl_atr > 0:
            min_risk = min_sl_atr * atr_value
            if risk_structural < min_risk:
                min_pips = min_risk / pip_size
                return AnalysisStep(
                    "rr", False,
                    f"SL too tight ({risk_pips_raw:.1f} pips < {min_sl_atr:.1f}x ATR "
                    f"= {min_pips:.1f} pips) — RR would be inflated, skip"
                )

        # 3. Determine structural take-profit levels.
        tp_levels = liq_data.get("tp_levels", [])
        valid_tp_levels = []
        if tp_levels:
            if direction == "buy":
                valid_tp_levels = [tp for tp in tp_levels if tp > final_entry]
            else:
                valid_tp_levels = [tp for tp in tp_levels if tp < final_entry]

        if valid_tp_levels:
            tp2 = valid_tp_levels[0]
        else:
            tp_timeframes = self._profile_list(symbol, "tp_timeframes", ["H4", "D1"])
            tp2 = self._find_structural_tp(
                data, direction, final_entry, risk_structural, timeframes=tp_timeframes
            ) if data else None
            if tp2 is None:
                return AnalysisStep(
                    "rr", False,
                    "No structural TP target found - no clear price objective, skip trade"
                )

        if direction == "buy" and tp2 <= final_entry:
            return AnalysisStep("rr", False, "Structural TP is not above buy entry")
        if direction == "sell" and tp2 >= final_entry:
            return AnalysisStep("rr", False, "Structural TP is not below sell entry")

        tp1_timeframes = self._profile_list(symbol, "tp1_timeframes", ["M15", "H1"])
        tp1 = self._find_structural_tp(
            data, direction, final_entry, risk_structural, timeframes=tp1_timeframes
        ) if data else None
        if direction == "buy" and (tp1 is None or tp1 <= final_entry):
            tp1 = tp2
        elif direction == "sell" and (tp1 is None or tp1 >= final_entry):
            tp1 = tp2
        tp = tp2

        # 4. Evaluate Risk-to-Reward based on structural SL.
        rr_structural = abs(tp - final_entry) / risk_structural
        rr1_structural = abs(tp1 - final_entry) / risk_structural if tp1 else rr_structural
        min_rr = profile.get("min_rr", self.config.min_rr)
        if rr_structural < min_rr:
            return AnalysisStep("rr", False, f"Structural RR {rr_structural:.1f} below minimum {min_rr}")

        # 5. Reject trades that violate broker minimum stop distance.
        try:
            mt5 = get_mt5()
            mt5_symbol = self.config.get_mt5_symbol(symbol)
            sym_info = mt5.symbol_info(mt5_symbol)
            if sym_info is not None:
                broker_min_dist = getattr(sym_info, "trade_stops_level", 0) * getattr(sym_info, "point", 0)
                if broker_min_dist > 0 and risk_structural < broker_min_dist:
                    return AnalysisStep(
                        "rr", False,
                        f"Structural SL {risk_structural/pip_size:.1f} pips is below "
                        f"broker minimum {broker_min_dist/pip_size:.1f} pips - POI too tight, skip trade"
                    )
        except Exception as exc:
            logger.debug(f"[{symbol}] Broker stops_level check skipped: {exc}")

        sl_final = sl_structural
        risk_final = risk_structural
        rr_final = rr_structural
        rr1_final = rr1_structural

        logger.info(
            f"[{symbol}] Structural SL={risk_structural/pip_size:.1f} pips | "
            f"RR={rr_structural:.1f} | TP={tp:.5f}"
        )

        return AnalysisStep("rr", True, data={
            "entry_price": round(final_entry, 5),
            "sl": round(sl_final, 5),
            "tp": round(tp, 5),
            "tp1": round(tp1, 5) if tp1 else round(tp, 5),
            "tp2": round(tp2, 5),
            "rr": rr_final,
            "rr1": round(rr1_final, 1),
            "risk_pips": round(risk_final / pip_size, 1),
        })

    def _step9_limits(
        self, daily_trades: int, daily_dd: float,
        open_positions: Optional[List], symbol: str,
    ) -> AnalysisStep:
        """STEP 9: Check daily trade limits and drawdown."""
        if daily_trades >= self.config.max_trades_per_day:
            return AnalysisStep("daily_limits", False,
                                f"Daily limit reached ({daily_trades}/{self.config.max_trades_per_day})")

        if self.config.daily_drawdown_pct > 0 and daily_dd >= self.config.daily_drawdown_pct:
            return AnalysisStep("daily_limits", False,
                                f"Daily drawdown hit ({daily_dd:.1f}%/{self.config.daily_drawdown_pct}%)")

        if open_positions:
            names = self.config.symbol_exposure_names(symbol)
            for pos in open_positions:
                ps = (getattr(pos, "symbol", "") or "").upper()
                if ps in names:
                    return AnalysisStep(
                        "daily_limits", False,
                        f"Already have open position on {symbol} ({ps})"
                    )

        return AnalysisStep("daily_limits", True)

    # ── Scoring ──────────────────────────────────────────────

    # Quality-factor weights (sum = 100). Tunable; these drive setup_score so
    # that min_setup_score / min_limit_setup_score actually discriminate.
    QUALITY_WEIGHTS = {
        "htf_bias": 18,    # strength/alignment of the HTF draw
        "structure": 20,   # CHoCH+displacement (MSS) > CHoCH > BOS
        "poi": 18,         # tighter POI relative to ATR scores higher
        "sweep": 14,       # sweep with displacement is cleaner
        "location": 12,    # POI inside OTE (0.62–0.79) is premium location
        "rr": 18,          # reward:risk, saturating at ~3R
    }
    # Reference ceilings used to normalise raw factor values to 0..1.
    _BIAS_SCORE_CEIL = 6.0
    _POI_WIDTH_ATR_CEIL = 1.5
    _RR_CEIL = 3.0

    def score_setup(
        self,
        *,
        is_killzone: bool = False,
        bias_score: int = 0,
        shift_type: str = "choch",
        has_displacement: bool = False,
        sweep_has_displacement: bool = True,
        poi: Any = None,
        atr_value: float = 0.0,
        in_ote: bool = False,
        rr: float = 0.0,
        is_late_entry: bool = False,
        is_silver_bullet: bool = False,
        liq_data: Optional[Dict] = None,
        direction: str = "",
    ) -> float:
        """
        Weighted setup-quality score (0–100) from real confluence factors.

        Unlike the previous "all steps passed → 100" model, this rewards the
        *quality* of each confluence so the score is discriminating:
          - HTF bias strength (|score| toward ±6)
          - structure shift type (MSS > CHoCH > BOS)
          - POI tightness vs ATR (narrow zones are higher conviction)
          - sweep displacement
          - location (inside OTE)
          - reward:risk, saturating at ~3R
        Modifiers: killzone gate, Silver Bullet bonus, late-entry penalty,
        Midnight-Open confluence bonus.
        """
        w = self.QUALITY_WEIGHTS

        def clamp01(x: float) -> float:
            return max(0.0, min(1.0, x))

        # 1. HTF bias strength
        bias_q = clamp01(abs(bias_score) / self._BIAS_SCORE_CEIL)

        # 2. Structure shift quality
        if shift_type == "choch" and has_displacement:
            struct_q = 1.0
        elif shift_type == "choch":
            struct_q = 0.7
        else:  # bos
            struct_q = 0.5

        # 3. POI tightness (narrower POI relative to ATR = better)
        if poi is not None and atr_value > 0 and hasattr(poi, "top"):
            width_atr = abs(poi.top - poi.bottom) / atr_value
            poi_q = clamp01(1.0 - width_atr / self._POI_WIDTH_ATR_CEIL)
        else:
            poi_q = 0.6  # unknown width — neutral-ish

        # 4. Sweep displacement
        sweep_q = 1.0 if sweep_has_displacement else 0.6

        # 5. Location — inside OTE is premium
        location_q = 1.0 if in_ote else 0.6

        # 6. Reward:risk, saturating
        rr_q = clamp01(rr / self._RR_CEIL)

        score = (
            bias_q * w["htf_bias"]
            + struct_q * w["structure"]
            + poi_q * w["poi"]
            + sweep_q * w["sweep"]
            + location_q * w["location"]
            + rr_q * w["rr"]
        )

        # ── Modifiers ────────────────────────────────────────────
        if not is_killzone:
            score -= float(getattr(self.config, "outside_kz_score_penalty", 15.0))

        if is_silver_bullet:
            score += 5.0

        if is_late_entry:
            score -= float(getattr(self.config, "late_entry_score_penalty", 10.0))

        # Midnight Open confluence bonus
        if liq_data and liq_data.get("asian_range"):
            asian = liq_data["asian_range"]
            mo = asian.get("midnight_open")
            target = liq_data.get("target_level")
            if mo and target:
                if (direction == "buy" and target < mo) or (direction == "sell" and target > mo):
                    score += 5.0

        return max(0.0, min(100.0, score))

    def _find_structural_tp(
        self, data: Optional[Dict], direction: str,
        entry: float, risk: float, timeframes: Optional[List[str]] = None,
    ) -> Optional[float]:
        """Find the nearest structural swing level as TP1 target."""
        if data is None:
            return None
        if timeframes is None:
            timeframes = ["M15", "H1"]

        lb = self.config.swing_left_bars
        rb = self.config.swing_right_bars

        for tf in timeframes:
            df = data.get(tf)
            if df is None or len(df) < 20:
                continue

            sh = detect_swing_highs(df, lb, rb)
            sl = detect_swing_lows(df, lb, rb)

            if direction == "buy":
                # Find nearest swing high above entry
                candidates = [s.price for s in sh if s.price > entry + risk]
                if candidates:
                    return min(candidates)
            else:
                # Find nearest swing low below entry
                candidates = [s.price for s in sl if s.price < entry - risk]
                if candidates:
                    return max(candidates)

        return None

    def try_create_intent(
        self,
        symbol: str,
        data: Dict,
        failed_step: int,
        htf_bias: str,
        steps_data: Dict[str, Any],
    ) -> Optional[PendingIntent]:
        """
        Create a PendingIntent when early steps pass but later ones fail.

        This is what makes the bot "think" — instead of discarding a
        partially-confirmed setup, it remembers and waits.

        Called by main.py when analyze() returns None.
        """
        # Only create intents when steps 1-2 passed (we have bias + liquidity)
        if failed_step <= 2:
            return None

        direction_map = {
            "bullish": "buy",
            "bearish": "sell",
        }
        direction = direction_map.get(htf_bias, "")
        if not direction:
            return None

        # Determine what we're waiting for
        if failed_step == 3:
            waiting_for = "sweep"
            detail = f"Waiting for sweep at {steps_data.get('liquidity_target', '?')}"
        elif failed_step == 4:
            waiting_for = "choch"
            detail = "Waiting for CHoCH/BOS after sweep"
        elif failed_step in (5, 6, 7):
            waiting_for = "poi_tap"
            detail = "Waiting for price to reach POI zone"
        else:
            return None

        intent = self.intent_mgr.create(
            symbol=symbol,
            direction=direction,
            htf_bias=htf_bias,
            waiting_for=waiting_for,
            waiting_detail=detail,
            liquidity_target=steps_data.get("liquidity_target"),
            liquidity_type=steps_data.get("liquidity_type", ""),
            sweep_level=steps_data.get("sweep_level"),
            sweep_has_displacement=steps_data.get("sweep_has_displacement", False),
        )

        # If we have POI data and limit orders are enabled, store POI for limit order
        poi_data = steps_data.get("poi")
        if poi_data and hasattr(poi_data, "top") and self.config.intent_use_limit_orders:
            intent.poi_top = poi_data.top
            intent.poi_bottom = poi_data.bottom
            intent.poi_type = steps_data.get("poi_type", "")
            # Set limit entry at the POI zone
            if direction == "buy":
                intent.limit_entry = poi_data.top  # Enter at top of OB
            else:
                intent.limit_entry = poi_data.bottom  # Enter at bottom of OB

        intent.update_narrative()
        return intent

    def check_intent(
        self,
        symbol: str,
        data: Dict,
        daily_trades: int = 0,
        daily_drawdown_pct: float = 0.0,
        open_positions: Optional[List] = None,
    ) -> Optional[SignalResult]:
        """
        Check if a PendingIntent's trigger conditions are now met.

        Called every scan cycle for symbols with active intents.
        Returns a SignalResult if the intent should be executed.
        """
        intent = self.intent_mgr.get(symbol)
        if intent is None:
            return None

        # Validate HTF bias hasn't changed
        step1 = self._step1_htf_bias(data, symbol)
        if not step1.passed:
            intent.invalidate("HTF data lost")
            return None
        current_bias = step1.data["bias"]
        if not self.intent_mgr.validate_intent(symbol, current_bias):
            return None

        pip_size = get_pip_size(symbol)

        # Check what we're waiting for
        if intent.waiting_for == "sweep":
            step2_data = {"target_level": intent.liquidity_target, "target_type": intent.liquidity_type}
            step3 = self._step3_sweep(data, symbol, step2_data)
            if not step3.passed:
                return None
            intent.sweep_level = step3.data.get("sweep_level")
            intent.sweep_has_displacement = step3.data.get("sweep_has_displacement", False)
            intent.waiting_for = "choch"
            intent.waiting_detail = "Sweep confirmed! Waiting for CHoCH/BOS"
            intent.advance_phase("ready", "Sweep detected")
            intent.update_narrative()
            # Don't return yet — fall through to check CHoCH

        if intent.waiting_for == "choch":
            sweep_data = {
                "sweep_direction": "below" if "sell-side" in intent.liquidity_type else "above",
                "sweep": None,
                "sweep_type": "standard",
            }
            step4 = self._step4_choch(data, symbol, intent.htf_bias, sweep_data)
            if not step4.passed:
                return None
            direction = step4.data["entry_direction"]
            intent.choch_direction = step4.data.get("shift_type", "choch")
            intent.waiting_for = "poi_tap"
            intent.waiting_detail = "Structure shift confirmed! Waiting for POI entry"
            intent.advance_phase("armed", "CHoCH/BOS confirmed")
            intent.update_narrative()

        if intent.waiting_for in ("poi_tap", "confirmation"):
            direction = intent.direction

            # Find POI
            step5 = self._step5_poi(data, symbol, direction, intent.htf_bias)
            if not step5.passed:
                return None

            poi = step5.data["poi"]

            # Check session
            step6 = self._step6_session(symbol)
            if not step6.passed:
                return None

            # Re-run liquidity for TP levels
            step2 = self._step2_liquidity_target(data, symbol, intent.htf_bias)
            liq_data = step2.data if step2.passed else {}

            # Check confirmation
            step7 = self._step7_confirmation(data, symbol, direction, poi, liq_data)
            if not step7.passed:
                # POI found but no confirmation yet
                if self.config.intent_use_limit_orders and intent.limit_ticket is None:
                    # Fetch current tick price
                    current_price = 0.0
                    price_tf = str(self._profile(symbol).get("confirmation_tf", "M5"))
                    price_df = data.get(price_tf)
                    if price_df is None:
                        price_df = data.get("M5")
                    if price_df is not None and not price_df.empty:
                        current_price = float(price_df["close"].iloc[-1])
                        
                    # Calculate ATR from the instrument profile timeframe.
                    atr_value = self._profile_atr(data, symbol)
                        
                    # Quality score for the limit decision. RR is not known yet
                    # (step8 runs only if we approve), so use the instrument's
                    # minimum RR as a conservative placeholder for the rr factor.
                    is_kz = step6.data.get("is_killzone", False)
                    is_sb = step6.data.get("is_silver_bullet", False)
                    placeholder_rr = float(self._profile(symbol).get("min_rr", self.config.min_rr))
                    setup_score = self.score_setup(
                        is_killzone=is_kz,
                        bias_score=int(step1.data.get("score", 0)),
                        shift_type=getattr(intent, "choch_direction", "") or "choch",
                        poi=poi,
                        atr_value=atr_value,
                        in_ote=step5.data.get("in_ote", False),
                        rr=placeholder_rr,
                        is_silver_bullet=is_sb,
                        sweep_has_displacement=getattr(intent, "sweep_has_displacement", False),
                        liq_data=liq_data,
                        direction=direction,
                    )
                    
                    # Call algorithmic decision helper
                    use_limit, decision_reason = self._should_use_limit_order(
                        symbol=symbol,
                        direction=direction,
                        poi=poi,
                        data=data,
                        setup_score=setup_score,
                        current_price=current_price,
                        atr_value=atr_value,
                    )
                    
                    if use_limit:
                        limit_entry = poi.top if direction == "buy" else poi.bottom
                        intent.poi_top = poi.top
                        intent.poi_bottom = poi.bottom
                        intent.poi_type = step5.data.get("poi_type", "")
                        intent.limit_entry = limit_entry
                        intent.update_narrative()
                        
                        step8 = self._step8_rr(
                            symbol, direction, limit_entry, poi,
                            liq_data, pip_size, atr_value, data
                        )
                        
                        if step8.passed:
                            # Daily limits check
                            step9 = self._step9_limits(daily_trades, daily_drawdown_pct, open_positions, symbol)
                            if step9.passed:
                                sl_price = step8.data["sl"]
                                tp_price = step8.data["tp"]
                                rr = step8.data["rr"]
                                kz_name = step6.data.get("session", "Intent Limit")
                                
                                signal = SignalResult(
                                    symbol=symbol,
                                    direction=direction,
                                    entry_price=round(limit_entry, 5),
                                    sl_price=round(sl_price, 5),
                                    tp_price=round(tp_price, 5),
                                    rr_ratio=round(rr, 2),
                                    setup_score=round(setup_score, 1),
                                    setup_type="LIMIT_ORDER",
                                    session=kz_name,
                                    timestamp=datetime.now(timezone.utc),
                                    confidence="high" if setup_score >= 85 else "medium",
                                    steps_log={"source": "PendingIntent", "narrative": f"{intent.narrative} | Decision: {decision_reason}"},
                                )
                                # We don't mark as triggered yet, main.py will set limit_ticket
                                return signal
                    else:
                        # Update intent.waiting_detail with why we are waiting for market confirmation
                        intent.waiting_detail = f"Market: {decision_reason}"
                        intent.update_narrative()
                return None

            entry_price = step7.data["entry_price"]

            # RR check
            atr_value = self._profile_atr(data, symbol)

            step8 = self._step8_rr(
                symbol, direction, entry_price, poi,
                liq_data, pip_size, atr_value, data
            )
            if not step8.passed:
                return None

            # Daily limits
            step9 = self._step9_limits(daily_trades, daily_drawdown_pct, open_positions, symbol)
            if not step9.passed:
                return None

            # BUILD SIGNAL from intent
            sl_price = step8.data["sl"]
            tp_price = step8.data["tp"]
            rr = step8.data["rr"]
            kz_name = step6.data.get("session", "Intent")

            setup_score = self.score_setup(
                is_killzone=step6.data.get("is_killzone", False),
                bias_score=int(step1.data.get("score", 0)),
                shift_type=getattr(intent, "choch_direction", "") or "choch",
                poi=poi,
                atr_value=atr_value,
                in_ote=step5.data.get("in_ote", False),
                rr=rr,
                is_silver_bullet=step6.data.get("is_silver_bullet", False),
                sweep_has_displacement=getattr(intent, "sweep_has_displacement", False),
                liq_data=liq_data,
                direction=direction,
            )

            signal = SignalResult(
                symbol=symbol,
                direction=direction,
                entry_price=round(entry_price, 5),
                sl_price=round(sl_price, 5),
                tp_price=round(tp_price, 5),
                rr_ratio=round(rr, 2),
                setup_score=round(setup_score, 1),
                setup_type=f"Intent {intent.poi_type or 'Setup'}",
                session=kz_name,
                timestamp=datetime.now(timezone.utc),
                confidence="high" if setup_score >= 80 else "medium",
                steps_log={"source": "PendingIntent", "narrative": intent.narrative},
            )

            self.intent_mgr.mark_triggered(symbol)
            logger.info(f"[{symbol}] ★ INTENT TRIGGERED: {direction.upper()} @ {entry_price}")
            return signal

        return None

    def _should_use_limit_order(
        self,
        symbol: str,
        direction: str,
        poi: Any,
        data: Dict,
        setup_score: float,
        current_price: float,
        atr_value: float,
    ) -> tuple[bool, str]:
        """
        Determine algorithmically whether to place a Limit Order (aggressive) or wait for Market confirmation (conservative).
        """
        if not getattr(self.config, "intent_use_limit_orders", True):
            return False, "Limit orders globally disabled in settings"

        if atr_value <= 0.0:
            return False, "ATR value is zero/invalid"

        # 1. POI Zone Width check
        poi_width = abs(poi.top - poi.bottom)
        poi_width_atr = poi_width / atr_value
        max_limit_width_atr = getattr(self.config, "max_limit_poi_width_atr", 1.2)
        if poi_width_atr > max_limit_width_atr:
            return False, f"POI zone too wide ({poi_width_atr:.2f} ATR > {max_limit_width_atr:.1f} ATR)"

        # 2. Setup Score check
        min_limit_score = getattr(self.config, "min_limit_setup_score", 75.0)
        if setup_score < min_limit_score:
            return False, f"Setup score too low ({setup_score:.1f} < {min_limit_score:.1f})"

        # 3. Current Price distance check
        limit_entry = poi.top if direction == "buy" else poi.bottom
        
        # Check if already inside POI
        is_inside = False
        if direction == "buy" and current_price <= poi.top:
            is_inside = True
        elif direction == "sell" and current_price >= poi.bottom:
            is_inside = True
            
        if is_inside:
            return False, "Price already inside POI"

        # Check if too close to POI
        distance = abs(current_price - limit_entry)
        distance_atr = distance / atr_value
        if distance_atr < 0.2:
            return False, f"Price too close to POI ({distance_atr:.2f} ATR < 0.2 ATR)"

        return True, f"Approved (width: {poi_width_atr:.2f} ATR, score: {setup_score:.1f})"
