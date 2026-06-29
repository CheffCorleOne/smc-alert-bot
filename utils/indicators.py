"""
Shared technical indicators.

Single source of truth for indicator math that was previously duplicated
across ``core.entry_engine`` and ``core.liquidity``.
"""

from __future__ import annotations

import pandas as pd


def atr(df: pd.DataFrame | None, period: int = 14) -> float:
    """
    Average True Range of the most recent bar (simple moving average of TR).

    Returns 0.0 when there is not enough data. Behaviour matches the previous
    duplicated implementations exactly (SMA of True Range, not Wilder's RMA).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|).
    """
    if df is None or len(df) < period + 1:
        return 0.0

    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_series = tr.rolling(window=period).mean()
    last = atr_series.iloc[-1]
    return float(last) if not pd.isna(last) else 0.0


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Full ATR series (SMA of True Range). Useful for backtests / vectorised use."""
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - close_prev).abs(), (low - close_prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()
