"""
Smoke test: every module must import cleanly.

MetaTrader5 is imported lazily everywhere, so these imports must succeed even
without a live terminal (and on Linux CI). This catches refactor breakage like
a removed ``_get_mt5`` or a bad import path.
"""

from __future__ import annotations

import importlib

import pytest

MODULES = [
    "config",
    "utils.indicators",
    "utils.logger",
    "broker.mt5_client",
    "broker.connector",
    "broker.risk_manager",
    "broker.order_manager",
    "core.market_structure",
    "core.liquidity",
    "core.poi",
    "core.premium_discount",
    "core.sessions",
    "core.pending_intent",
    "core.entry_engine",
    "data.fetcher",
    "data.database",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str):
    importlib.import_module(module)


def test_shared_atr_is_used_everywhere():
    """entry_engine.calculate_atr and LiquidityEngine._calculate_atr must be the shared impl."""
    from core.entry_engine import calculate_atr
    from core.liquidity import LiquidityEngine
    from utils.indicators import atr

    assert calculate_atr is atr

    from conftest import make_df

    df = make_df([(1.0, 1.5, 0.5, 1.2)] * 20)
    assert LiquidityEngine("EURUSD")._calculate_atr(df) == atr(df)
