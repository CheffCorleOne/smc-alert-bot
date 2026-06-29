"""
Tests for the anti-repaint behaviour of MarketDataFetcher.

Uses a fake MetaTrader5 module injected into broker.mt5_client so the fetcher
can run with no live terminal.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import broker.mt5_client as mt5_client
from data.fetcher import MarketDataFetcher

_DTYPE = [
    ("time", "i8"), ("open", "f8"), ("high", "f8"),
    ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
]


def _fake_rates(n_total: int = 20):
    rows = []
    for i in range(n_total):
        close = float(i + 1)
        rows.append((1_700_000_000 + i * 300, close, close + 0.5, close - 0.5, close, 100))
    arr = np.array(rows, dtype=_DTYPE)
    # Mark the last (forming) bar with a distinctive close.
    arr[-1]["close"] = 999.0
    return arr


@pytest.fixture
def fake_mt5(monkeypatch):
    arr = _fake_rates()

    def copy_rates_from_pos(symbol, tf, start, count):
        return arr[-count:]

    fake = SimpleNamespace(
        TIMEFRAME_M5=5,
        copy_rates_from_pos=copy_rates_from_pos,
        last_error=lambda: (0, "ok"),
    )
    monkeypatch.setattr(mt5_client, "_mt5", fake)
    return fake


def test_forming_candle_dropped(fake_mt5):
    f = MarketDataFetcher()
    df = f.get_ohlcv("EURUSD", "M5", bars=5, drop_forming=True)
    assert df is not None
    # The forming bar (close 999) must be gone; last close is the prior bar.
    assert 999.0 not in df["close"].values
    assert df["close"].iloc[-1] == 19.0
    assert len(df) == 5


def test_keep_forming_when_disabled(fake_mt5):
    f = MarketDataFetcher()
    df = f.get_ohlcv("EURUSD", "M5", bars=5, drop_forming=False)
    assert df is not None
    assert df["close"].iloc[-1] == 999.0
