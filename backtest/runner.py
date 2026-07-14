"""
Walk-forward backtester for the SMC engine.

Replays historical multi-timeframe data through ``SMCEntryEngine.analyze`` with
no look-ahead (each step only sees bars up to the current M5 time), simulates
the outcome of every signal on the forward M5 path, and reports metrics.

Known limitation: the engine's session/killzone check reads the wall clock, not
the bar timestamp. For honest structure-logic backtests, run with
``ignore_sessions=True`` (the default), which relaxes the killzone gate so
signals are evaluated on structure alone. Making sessions time-injectable is a
follow-up for the engine rewrite.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import BotConfig
from core.entry_engine import SMCEntryEngine
from backtest.simulator import TradeResult, compute_metrics, simulate_trade


class Backtester:
    def __init__(
        self,
        symbol: str,
        config: Optional[BotConfig] = None,
        step: int = 3,
        warmup: int = 200,
        ignore_sessions: bool = True,
        quiet: bool = True,
        max_hold_bars: int = 288,
        cost_r: float = 0.0,
    ) -> None:
        self.symbol = symbol
        self.config = config or BotConfig()
        if ignore_sessions:
            # Relax the wall-clock killzone gate so structure logic is testable.
            self.config.allow_outside_killzone = True
            self.config.ignore_weekend_filter = True
            self.config.outside_kz_score_penalty = 0.0
        self.engine = SMCEntryEngine(self.config)
        self.step = max(1, step)
        self.warmup = warmup
        # Cap how long a simulated trade may stay open (in M5 bars). Default
        # 288 = one trading day. Without this, far structural TPs "win" months
        # later and grossly inflate win rate / expectancy.
        self.max_hold_bars = max_hold_bars
        # Round-turn cost in R (spread + commission + slippage).
        self.cost_r = cost_r
        if quiet:
            logging.getLogger("smc").setLevel(logging.WARNING)

    def run(self, history: Dict[str, pd.DataFrame]) -> dict:
        """
        Args:
            history: {timeframe: full-history DataFrame} incl. at least 'M5'.

        Returns:
            Metrics dict from ``compute_metrics`` plus the raw trade list under
            key ``_trades``.
        """
        m5 = history.get("M5")
        if m5 is None or len(m5) <= self.warmup + 2:
            return {**compute_metrics([]), "_trades": []}

        time_arrays = {tf: df["time"].to_numpy() for tf, df in history.items()}
        m5_time = time_arrays["M5"]
        m5_high = m5["high"].to_numpy()
        m5_low = m5["low"].to_numpy()
        m5_close = m5["close"].to_numpy()

        results: List[TradeResult] = []
        last_key: Optional[str] = None

        for i in range(self.warmup, len(m5) - 1, self.step):
            t = m5_time[i]
            data = {}
            for tf, df in history.items():
                idx = int(np.searchsorted(time_arrays[tf], t, side="right"))
                data[tf] = df.iloc[:idx] if idx > 0 else None

            try:
                signal, _step, _reason, _bias = self.engine.analyze(
                    symbol=self.symbol, data=data,
                )
            except Exception:
                continue

            if signal is None:
                continue
            if signal.setup_score < self.config.min_setup_score:
                continue

            key = f"{signal.direction}_{signal.entry_price:.5f}"
            if key == last_key:
                continue
            last_key = key

            future = pd.DataFrame({
                "high": m5_high[i + 1:],
                "low": m5_low[i + 1:],
                "close": m5_close[i + 1:],
            })
            results.append(
                simulate_trade(
                    future, signal.direction, signal.entry_price,
                    signal.sl_price, signal.tp_price, signal.rr_ratio,
                    max_bars=self.max_hold_bars, cost_r=self.cost_r,
                )
            )

        metrics = compute_metrics(results)
        metrics["_trades"] = results
        return metrics
