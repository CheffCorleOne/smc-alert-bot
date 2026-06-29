"""
Shared pytest fixtures and synthetic OHLCV builders.

The SMC detectors are pure functions over pandas DataFrames with columns
[time, open, high, low, close, volume].  None of them import MetaTrader5 at
module load time, so they can be tested fully offline with synthetic data.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

# Make the project root importable (tests/ is one level below it).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_df(
    rows: list[tuple[float, float, float, float]],
    *,
    start: datetime | None = None,
    freq_minutes: int = 60,
    volume: float = 100.0,
) -> pd.DataFrame:
    """
    Build an OHLCV DataFrame from a list of (open, high, low, close) tuples.

    Times are evenly spaced, timezone-aware (UTC), ascending — matching what
    ``data.fetcher`` produces from ``copy_rates_from_pos``.
    """
    if start is None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    times = [start + timedelta(minutes=freq_minutes * i) for i in range(len(rows))]
    return pd.DataFrame(
        {
            "time": pd.to_datetime(times, utc=True),
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [volume] * len(rows),
        }
    )


def candles_from_closes(
    closes: list[float],
    *,
    wick: float = 0.1,
    start: datetime | None = None,
    freq_minutes: int = 60,
) -> pd.DataFrame:
    """
    Build candles from a close path. Open = previous close; high/low extend
    beyond the body by ``wick``. Useful for trend / structure tests.
    """
    rows: list[tuple[float, float, float, float]] = []
    prev = closes[0]
    for c in closes:
        o = prev
        hi = max(o, c) + wick
        lo = min(o, c) - wick
        rows.append((o, hi, lo, c))
        prev = c
    return make_df(rows, start=start, freq_minutes=freq_minutes)


@pytest.fixture
def uptrend_df() -> pd.DataFrame:
    """Explicit HH/HL uptrend: swing highs at idx 3 (5) & 7 (8), lows at idx 1 (0) & 5 (1.5)."""
    return make_df(
        [
            (1.5, 2.0, 1.0, 1.8),   # 0
            (1.0, 2.0, 0.0, 0.5),   # 1 swing low = 0
            (2.0, 4.0, 2.0, 3.5),   # 2
            (3.5, 5.0, 3.0, 4.5),   # 3 swing high = 5
            (3.0, 4.0, 2.0, 2.5),   # 4
            (2.5, 3.0, 1.5, 2.0),   # 5 swing low = 1.5 (higher low)
            (4.0, 6.0, 4.0, 5.5),   # 6
            (5.0, 8.0, 5.0, 7.5),   # 7 swing high = 8 (higher high)
            (5.0, 6.0, 4.0, 4.5),   # 8
        ]
    )


@pytest.fixture
def downtrend_df() -> pd.DataFrame:
    """Explicit LH/LL downtrend: swing highs at idx 1 (10) & 5 (8.5), lows at idx 3 (5) & 7 (3)."""
    return make_df(
        [
            (8.5, 9.0, 8.0, 8.8),   # 0
            (9.0, 10.0, 8.0, 9.5),  # 1 swing high = 10
            (8.0, 8.0, 6.0, 6.5),   # 2
            (6.0, 7.0, 5.0, 5.5),   # 3 swing low = 5
            (6.0, 8.0, 6.0, 7.5),   # 4
            (7.5, 8.5, 6.5, 8.0),   # 5 swing high = 8.5 (lower high)
            (6.0, 6.0, 4.0, 4.5),   # 6
            (4.0, 5.0, 3.0, 3.5),   # 7 swing low = 3 (lower low)
            (4.0, 6.0, 4.0, 5.0),   # 8
        ]
    )
