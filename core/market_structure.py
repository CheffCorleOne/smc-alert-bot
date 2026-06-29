"""
Market Structure Analysis — BOS, CHoCH, Swing Points.

This is the most critical module in the SMC engine. It detects:
- Swing Highs and Swing Lows (pivot-based)
- Break of Structure (BOS) — trend continuation
- Change of Character (CHoCH) — trend reversal
- Multi-timeframe bias determination
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
import numpy as np

from utils.logger import get_logger

logger = get_logger("structure")


# ── Data Structures ──────────────────────────────────────────

@dataclass
class SwingPoint:
    """A swing high or swing low point."""
    index: int               # Bar index in the DataFrame
    price: float             # High (for swing high) or Low (for swing low)
    timestamp: datetime
    type: str                # 'high' or 'low'


@dataclass
class BOSEvent:
    """Break of Structure event — trend continuation signal."""
    index: int               # Bar that broke the structure
    price: float             # Close price at the break
    swing_price: float       # The swing level that was broken
    direction: str           # 'bullish' or 'bearish'
    timestamp: datetime


@dataclass
class CHoCHEvent:
    """Change of Character event — potential trend reversal signal."""
    index: int
    price: float
    swing_price: float
    direction: str           # 'bullish' (reversal from bear to bull) or 'bearish'
    previous_trend: str      # The trend before the CHoCH
    timestamp: datetime
    has_displacement: bool = False # Whether the break was impulsive


# ── Swing Point Detection ────────────────────────────────────

# Bounded result cache. Swing detection is an O(n·bars) Python loop that is
# re-run many times per scan (bias, liquidity, POI, confirmation, TP all detect
# swings on the same frames). Caching by CONTENT hash keeps it correct (same
# bars → same result) while avoiding the repeated recompute.
_SWING_CACHE: "dict[tuple, List[SwingPoint]]" = {}
_SWING_CACHE_MAX = 512


def _swing_cache_key(df: pd.DataFrame, col: str, left_bars: int, right_bars: int):
    if df is None or len(df) == 0:
        return None
    try:
        # Price column is float64 -> tobytes() is a stable content hash.
        # (Do NOT hash the tz-aware time column via tobytes: it becomes an
        # object array of pointers and hashes differently each call.)
        price_h = hash(df[col].to_numpy().tobytes())
        first_ts = int(df["time"].iloc[0].value)
        last_ts = int(df["time"].iloc[-1].value)
    except Exception:
        return None
    return (col, len(df), price_h, first_ts, last_ts, left_bars, right_bars)


def _cache_store(key, value):
    if key is None:
        return
    if len(_SWING_CACHE) >= _SWING_CACHE_MAX:
        # Simple eviction: drop an arbitrary (oldest-ish) entry.
        _SWING_CACHE.pop(next(iter(_SWING_CACHE)), None)
    _SWING_CACHE[key] = value


def detect_swing_highs(
    df: pd.DataFrame, left_bars: int = 3, right_bars: int = 3
) -> List[SwingPoint]:
    """Cached wrapper around the pivot-high detector (see _compute_swing_highs)."""
    key = _swing_cache_key(df, "high", left_bars, right_bars)
    if key is not None and key in _SWING_CACHE:
        return _SWING_CACHE[key]
    result = _compute_swing_highs(df, left_bars, right_bars)
    _cache_store(key, result)
    return result


def detect_swing_lows(
    df: pd.DataFrame, left_bars: int = 3, right_bars: int = 3
) -> List[SwingPoint]:
    """Cached wrapper around the pivot-low detector (see _compute_swing_lows)."""
    key = _swing_cache_key(df, "low", left_bars, right_bars)
    if key is not None and key in _SWING_CACHE:
        return _SWING_CACHE[key]
    result = _compute_swing_lows(df, left_bars, right_bars)
    _cache_store(key, result)
    return result


def _compute_swing_highs(
    df: pd.DataFrame, left_bars: int = 3, right_bars: int = 3
) -> List[SwingPoint]:
    """
    Detect swing highs using a pivot-high algorithm.

    A swing high is a bar whose high is higher than the highs
    of `left_bars` bars to its left AND `right_bars` bars to its right.

    Args:
        df: OHLCV DataFrame with 'high' and 'time' columns.
        left_bars: Number of bars to the left to compare.
        right_bars: Number of bars to the right to compare.

    Returns:
        List of SwingPoint objects with type='high'.
    """
    highs = df["high"].values
    times = df["time"].values
    swing_highs = []

    for i in range(left_bars, len(highs) - right_bars):
        is_pivot = True
        current_high = highs[i]

        # Check left side
        for j in range(1, left_bars + 1):
            if highs[i - j] >= current_high:
                is_pivot = False
                break

        if not is_pivot:
            continue

        # Check right side
        for j in range(1, right_bars + 1):
            if highs[i + j] >= current_high:
                is_pivot = False
                break

        if is_pivot:
            swing_highs.append(SwingPoint(
                index=i,
                price=current_high,
                timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                type="high",
            ))

    return swing_highs


def _compute_swing_lows(
    df: pd.DataFrame, left_bars: int = 3, right_bars: int = 3
) -> List[SwingPoint]:
    """
    Detect swing lows using a pivot-low algorithm.

    A swing low is a bar whose low is lower than the lows
    of `left_bars` bars to its left AND `right_bars` bars to its right.

    Args:
        df: OHLCV DataFrame with 'low' and 'time' columns.
        left_bars: Number of bars to the left to compare.
        right_bars: Number of bars to the right to compare.

    Returns:
        List of SwingPoint objects with type='low'.
    """
    lows = df["low"].values
    times = df["time"].values
    swing_lows = []

    for i in range(left_bars, len(lows) - right_bars):
        is_pivot = True
        current_low = lows[i]

        for j in range(1, left_bars + 1):
            if lows[i - j] <= current_low:
                is_pivot = False
                break

        if not is_pivot:
            continue

        for j in range(1, right_bars + 1):
            if lows[i + j] <= current_low:
                is_pivot = False
                break

        if is_pivot:
            swing_lows.append(SwingPoint(
                index=i,
                price=current_low,
                timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                type="low",
            ))

    return swing_lows


# ── Break of Structure (BOS) ────────────────────────────────

def detect_bos(
    df: pd.DataFrame,
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
) -> List[BOSEvent]:
    """
    Detect Break of Structure events.

    Bullish BOS: a candle CLOSES above the most recent swing high.
    Bearish BOS: a candle CLOSES below the most recent swing low.

    BOS confirms continuation of the existing trend.

    Args:
        df: OHLCV DataFrame.
        swing_highs: Detected swing highs.
        swing_lows: Detected swing lows.

    Returns:
        List of BOSEvent objects in chronological order.
    """
    closes = df["close"].values
    times = df["time"].values
    bos_events = []
    used_highs = set()
    used_lows = set()

    for i in range(len(closes)):
        # Check bullish BOS — close above previous swing high
        for sh in reversed(swing_highs):
            if sh.index >= i:
                continue
            if sh.index in used_highs:
                continue
            if closes[i] > sh.price:
                bos_events.append(BOSEvent(
                    index=i,
                    price=closes[i],
                    swing_price=sh.price,
                    direction="bullish",
                    timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                ))
                used_highs.add(sh.index)
                break

        # Check bearish BOS — close below previous swing low
        for sl in reversed(swing_lows):
            if sl.index >= i:
                continue
            if sl.index in used_lows:
                continue
            if closes[i] < sl.price:
                bos_events.append(BOSEvent(
                    index=i,
                    price=closes[i],
                    swing_price=sl.price,
                    direction="bearish",
                    timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                ))
                used_lows.add(sl.index)
                break

    return sorted(bos_events, key=lambda b: b.index)


# ── Change of Character (CHoCH) ─────────────────────────────

def detect_choch(
    df: pd.DataFrame,
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
    current_trend: Optional[str] = None,
    displacement_multiplier: float = 1.5,
) -> List[CHoCHEvent]:
    """
    Detect Change of Character events.

    CHoCH is a break AGAINST the current trend direction:
    - In a bullish trend (HH/HL): a close below the most recent swing low = bearish CHoCH
    - In a bearish trend (LH/LL): a close above the most recent swing high = bullish CHoCH

    This is the most important signal for entries — it indicates
    smart money has shifted direction.

    Args:
        df: OHLCV DataFrame.
        swing_highs: Detected swing highs.
        swing_lows: Detected swing lows.
        current_trend: 'bullish' or 'bearish'. If None, auto-detected.

    Returns:
        List of CHoCHEvent objects.
    """
    if not swing_highs or not swing_lows:
        return []

    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    times = df["time"].values
    choch_events = []

    # Calculate average candle body size for displacement check
    bodies = np.abs(closes - opens)
    avg_body = np.mean(bodies[-20:]) if len(bodies) >= 20 else np.mean(bodies)

    def is_displaced(idx: int) -> bool:
        """Check if candle at idx shows displacement (strong body)."""
        body = abs(closes[idx] - opens[idx])
        # ICT Displacement: candle body > Nx average body of recent candles
        # OR candle is significantly larger than its own wicks
        return body > (avg_body * displacement_multiplier)

    # Auto-detect trend if not provided
    if current_trend is None:
        current_trend = _detect_current_trend(swing_highs, swing_lows)

    if current_trend is None:
        return []

    # Merge swings chronologically to track trend changes
    all_swings = sorted(swing_highs + swing_lows, key=lambda s: s.index)

    # Walk recent bars and flip the working trend each time a swing is broken.
    trend = current_trend

    # Now scan for CHoCH in recent bars
    lookback = min(100, len(closes))
    for i in range(len(closes) - lookback, len(closes)):
        # Find the most recent swing points relative to bar i
        past_swings = [s for s in all_swings if s.index < i]
        if not past_swings:
            continue
            
        last_sh = next((s for s in reversed(past_swings) if s.type == "high"), None)
        last_sl = next((s for s in reversed(past_swings) if s.type == "low"), None)
        
        if trend == "bullish" and last_sl:
            if closes[i] < last_sl.price:
                choch_events.append(CHoCHEvent(
                    index=i,
                    price=closes[i],
                    swing_price=last_sl.price,
                    direction="bearish",
                    previous_trend="bullish",
                    timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                    has_displacement=is_displaced(i),
                ))
                trend = "bearish" # Trend flipped
        elif trend == "bearish" and last_sh:
            if closes[i] > last_sh.price:
                choch_events.append(CHoCHEvent(
                    index=i,
                    price=closes[i],
                    swing_price=last_sh.price,
                    direction="bullish",
                    previous_trend="bearish",
                    timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                    has_displacement=is_displaced(i),
                ))
                trend = "bullish"

    return choch_events


# ── Trend Confirmation ───────────────────────────────────────

def is_higher_high_higher_low(
    swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]
) -> bool:
    """
    Check if structure shows Higher Highs and Higher Lows (bullish).

    Requires at least 2 swing highs and 2 swing lows.
    Checks the last 2 of each.
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return False

    sh = sorted(swing_highs, key=lambda s: s.index)
    sl = sorted(swing_lows, key=lambda s: s.index)

    hh = sh[-1].price > sh[-2].price   # Higher High
    hl = sl[-1].price > sl[-2].price   # Higher Low
    return hh and hl


def is_lower_high_lower_low(
    swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]
) -> bool:
    """
    Check if structure shows Lower Highs and Lower Lows (bearish).

    Requires at least 2 swing highs and 2 swing lows.
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return False

    sh = sorted(swing_highs, key=lambda s: s.index)
    sl = sorted(swing_lows, key=lambda s: s.index)

    lh = sh[-1].price < sh[-2].price   # Lower High
    ll = sl[-1].price < sl[-2].price   # Lower Low
    return lh and ll


def _detect_current_trend(
    swing_highs: List[SwingPoint], 
    swing_lows: List[SwingPoint],
    strict: bool = True
) -> Optional[str]:
    """Auto-detect the current trend from swing structure."""
    if is_higher_high_higher_low(swing_highs, swing_lows):
        return "bullish"
    if is_lower_high_lower_low(swing_highs, swing_lows):
        return "bearish"
    
    if not strict:
        # Fallback: check if the MOST RECENT action was a break of structure
        if swing_highs and swing_lows:
            sh = sorted(swing_highs, key=lambda s: s.index)
            sl = sorted(swing_lows, key=lambda s: s.index)
            
            # If last two highs are rising, even if low hasn't risen yet (incipient trend)
            if len(sh) >= 2 and sh[-1].price > sh[-2].price:
                return "bullish"
            # If last two lows are falling, even if high hasn't fallen yet
            if len(sl) >= 2 and sl[-1].price < sl[-2].price:
                return "bearish"

    return None


def _detect_directional_bias_from_candles(df: pd.DataFrame) -> Optional[str]:
    """
    Last-resort HTF direction when pivots are mixed.

    SMC still needs a draw-on-liquidity context even when formal HH/HL or
    LH/LL structure is not clean. Use completed candle momentum instead of
    calling the market directionless.
    """
    if df is None or len(df) < 4:
        return None

    closes = df["close"].values
    opens = df["open"].values
    idx = -2 if len(df) >= 2 else -1
    anchor_idx = max(0, len(df) - 8)

    if closes[idx] > closes[anchor_idx]:
        return "bullish"
    if closes[idx] < closes[anchor_idx]:
        return "bearish"
    if closes[idx] > opens[idx]:
        return "bullish"
    if closes[idx] < opens[idx]:
        return "bearish"
    return None


def _detect_trend_from_bos(
    df: pd.DataFrame, left_bars: int = 3, right_bars: int = 3,
) -> Optional[str]:
    """
    Detect trend direction from the most recent BOS events.

    If the latest BOS is bullish AND more recent than the latest bearish BOS,
    the trend is bullish (and vice versa).  This is more responsive than
    waiting for full HH/HL or LH/LL patterns.
    """
    if df is None or len(df) < (left_bars + right_bars + 10):
        return None

    sh = detect_swing_highs(df, left_bars, right_bars)
    sl = detect_swing_lows(df, left_bars, right_bars)
    bos_events = detect_bos(df, sh, sl)

    if not bos_events:
        return None

    last_bos = bos_events[-1]
    # Only consider recent BOS (within last 30% of data)
    recency_threshold = len(df) * 0.7
    if last_bos.index < recency_threshold:
        return None

    return last_bos.direction


# ── BOS After Sweep ──────────────────────────────────────────

def recent_dealing_range(
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
) -> Optional[Tuple[float, float]]:
    """
    Return (high, low) of the CURRENT dealing range for premium/discount.

    ICT measures premium/discount from the most recent working swing leg, not
    the all-time extremes of the lookback. Using global max/min skews the 50%
    equilibrium and mislocates the OTE zone. We take the most recent confirmed
    swing high and swing low (by bar index) and order them.

    Returns None if there isn't at least one of each.
    """
    if not swing_highs or not swing_lows:
        return None
    last_sh = max(swing_highs, key=lambda s: s.index)
    last_sl = max(swing_lows, key=lambda s: s.index)
    hi = max(last_sh.price, last_sl.price)
    lo = min(last_sh.price, last_sl.price)
    if hi <= lo:
        return None
    return hi, lo


def detect_bos_after_index(
    df: pd.DataFrame,
    after_index: int,
    expected_direction: str,
    left_bars: int = 2,
    right_bars: int = 1,
    after_time: Optional[datetime] = None,
) -> Optional[BOSEvent]:
    """
    Find a BOS event AFTER a given point in the expected direction.

    Used to confirm structural continuation after a liquidity sweep.
    For example, after sweeping SSL, we look for bullish BOS (price breaking
    above a swing high) to confirm the reversal.

    Args:
        df: OHLCV DataFrame.
        after_index: Only consider BOS events after this bar index (per-``df``).
        expected_direction: 'bullish' or 'bearish'.
        left_bars: Swing detection sensitivity (left).
        right_bars: Swing detection sensitivity (right).
        after_time: If given, filter by timestamp instead of index. REQUIRED
            when the reference point (e.g. a sweep) was found on a *different*
            timeframe — bar indices are not comparable across timeframes.

    Returns:
        The most recent matching BOSEvent, or None.
    """
    if df is None or len(df) < (left_bars + right_bars + 5):
        return None

    sh = detect_swing_highs(df, left_bars, right_bars)
    sl = detect_swing_lows(df, left_bars, right_bars)
    bos_events = detect_bos(df, sh, sl)

    if after_time is not None:
        matching = [
            b for b in bos_events
            if b.timestamp >= after_time and b.direction == expected_direction
        ]
    else:
        matching = [
            b for b in bos_events
            if b.index > after_index and b.direction == expected_direction
        ]

    return matching[-1] if matching else None


def detect_structure_shift(
    data: dict,
    expected_direction: str,
    after_index: int = 0,
    left_bars: int = 2,
    right_bars: int = 1,
    displacement_multiplier: float = 1.5,
    timeframes: Optional[List[str]] = None,
    after_time: Optional[datetime] = None,
) -> Optional[dict]:
    """
    Unified multi-TF structure shift detection (CHoCH or BOS).

    Checks provided timeframes (default H1 → M15 → M5) for either a CHoCH or BOS in the expected
    direction.  This is the key improvement: instead of requiring CHoCH
    on H1 only, we cascade down to lower timeframes.

    Args:
        data: Dict of timeframe → DataFrame.
        expected_direction: 'bullish' or 'bearish'.
        after_index: Only consider shifts after this bar index (0 = any).
        left_bars: Swing detection sensitivity.
        right_bars: Swing detection sensitivity.
        displacement_multiplier: Multiplier for MSS.
        timeframes: List of timeframes to check.

    Returns:
        Dict with 'type' ('choch' or 'bos'), 'timeframe', 'event',
        'entry_direction', or None.
    """
    current_trend = "bullish" if expected_direction == "bearish" else "bearish"
    entry_dir = "buy" if expected_direction == "bullish" else "sell"
    
    if timeframes is None:
        timeframes = ["H1", "M15", "M5"]

    for tf in timeframes:
        df = data.get(tf)
        if df is None or len(df) < 20:
            continue

        sh = detect_swing_highs(df, left_bars, right_bars)
        sl = detect_swing_lows(df, left_bars, right_bars)

        # Check CHoCH first (stronger signal). When a cross-timeframe reference
        # point is supplied (after_time), filter chronologically by timestamp —
        # bar indices from another timeframe are meaningless on this df.
        choch_events = detect_choch(df, sh, sl, current_trend, displacement_multiplier)
        if after_time is not None:
            matching_choch = [
                ch for ch in choch_events
                if ch.direction == expected_direction and ch.timestamp >= after_time
            ]
        else:
            matching_choch = [
                ch for ch in choch_events
                if ch.direction == expected_direction
                and (after_index == 0 or ch.index >= after_index)
            ]
        
        # Prefer CHoCHs with displacement (MSS)
        if matching_choch:
            # Sort to prefer displaced ones
            matching_choch.sort(key=lambda x: x.has_displacement, reverse=True)
            ch = matching_choch[0]
            
            return {
                "type": "choch",
                "timeframe": tf,
                "event": ch,
                "entry_direction": entry_dir,
                "level": ch.swing_price,
                "has_displacement": ch.has_displacement,
            }

        # Check BOS as alternative (continuation confirmation)
        bos_event = detect_bos_after_index(
            df, after_index, expected_direction, left_bars, right_bars,
            after_time=after_time,
        )
        if bos_event:
            return {
                "type": "bos",
                "timeframe": tf,
                "event": bos_event,
                "entry_direction": entry_dir,
                "level": bos_event.swing_price,
            }

    return None


# ── Draw on Liquidity Bias ───────────────────────────────────

def get_draw_on_liquidity(
    df: pd.DataFrame,
    left_bars: int = 3,
    right_bars: int = 3,
) -> Optional[str]:
    """
    Determine directional bias by examining Draw on Liquidity (DOL).

    ICT concept: price is always drawn towards liquidity pools.
    If the nearest significant untapped pool is above → bullish DOL.
    If below → bearish DOL.

    This serves as a tiebreaker when structural bias is mixed.
    """
    if df is None or len(df) < 20:
        return None

    sh = detect_swing_highs(df, left_bars, right_bars)
    sl = detect_swing_lows(df, left_bars, right_bars)

    if not sh or not sl:
        return None

    current_price = float(df["close"].iloc[-1])

    # BSL = untapped swing highs above current price
    bsl = [s for s in sh if s.price > current_price]
    # SSL = untapped swing lows below current price
    ssl = [s for s in sl if s.price < current_price]

    if not bsl and not ssl:
        return None

    # Distance to nearest BSL vs SSL
    nearest_bsl_dist = min(abs(s.price - current_price) for s in bsl) if bsl else float("inf")
    nearest_ssl_dist = min(abs(current_price - s.price) for s in ssl) if ssl else float("inf")

    # Price tends to be drawn to the CLOSER liquidity pool
    # But we check recency too — more recent pools are more relevant
    if nearest_bsl_dist < nearest_ssl_dist:
        return "bullish"
    elif nearest_ssl_dist < nearest_bsl_dist:
        return "bearish"

    return None


# ── Multi-Timeframe Bias ─────────────────────────────────────

def get_market_bias(
    df_w1: Optional[pd.DataFrame],
    df_d1: Optional[pd.DataFrame],
    df_h4: Optional[pd.DataFrame],
    left_bars: int = 3,
    right_bars: int = 3,
    min_bias_score: int = 3,
    strict: bool = True,
    allow_weak_bias: bool = True,
) -> Tuple[str, str, int]:
    """
    Determine overall market bias from W1, D1, H4 structure.
    Returns (bias, confidence, score).

    Uses a weighted scoring system:
    - W1 = 3 points, D1 = 2 points, H4 = 1 point
    - Score measures alignment strength.
    - Direction is always selected from HTF hierarchy when data exists.

    Enhanced with BOS detection and Draw on Liquidity as secondary sources.
    """
    # Weights: W1=3, D1=2, H4=1
    tf_weights = [("W1", df_w1, 3), ("D1", df_d1, 2), ("H4", df_h4, 1)]
    score = 0
    tf_details = []
    tf_trends = []

    for label, df, weight in tf_weights:
        if df is None or len(df) < (left_bars + right_bars + 5):
            tf_details.append(f"{label}: NO DATA")
            continue

        sh = detect_swing_highs(df, left_bars, right_bars)
        sl = detect_swing_lows(df, left_bars, right_bars)

        trend = _detect_current_trend(sh, sl, strict=strict)
        source = "structure"

        # Fallback 1: BOS-based trend detection
        if trend is None:
            trend = _detect_trend_from_bos(df, left_bars, right_bars)
            source = "bos"

        # Fallback 2: Draw on Liquidity
        if trend is None:
            trend = get_draw_on_liquidity(df, left_bars, right_bars)
            source = "dol"

        # Fallback 3: Candle momentum
        if trend is None:
            trend = _detect_directional_bias_from_candles(df)
            source = "candle"
        
        if trend == "bullish":
            score += weight
            tf_trends.append((label, trend, weight))
            tf_details.append(f"{label}: BULLISH (+{weight}, {source})")
        elif trend == "bearish":
            score -= weight
            tf_trends.append((label, trend, weight))
            tf_details.append(f"{label}: BEARISH (-{weight}, {source})")
        else:
            tf_details.append(f"{label}: RANGING (0)")

    logger.debug(f"Bias Score: {score} | {', '.join(tf_details)}")

    # Determine bias from score when alignment is strong enough.
    if score >= min_bias_score:
        confidence = "high" if score >= (min_bias_score + 2) else "medium"
        return "bullish", confidence, score
    elif score <= -min_bias_score:
        confidence = "high" if score <= -(min_bias_score + 2) else "medium"
        return "bearish", confidence, score

    # Alignment is weaker than min_bias_score. In strict ICT mode we refuse to
    # invent a directional draw — no clean HTF bias means stand aside.
    if not allow_weak_bias:
        return "ranging", "low", score

    # Otherwise keep a directional HTF draw using hierarchy (legacy behaviour).
    # W1 dominates, then D1, then H4. Score remains visible as confidence.
    for label in ("W1", "D1", "H4"):
        for tf_label, trend, _weight in tf_trends:
            if tf_label == label:
                return trend, "low", score

    return "ranging", "low", score

