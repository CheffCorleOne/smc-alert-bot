"""Tests for the unified symbol normalisation / pip-size module."""

from __future__ import annotations

import pytest

from config import get_pip_size as config_get_pip_size
from utils.symbols import get_pip_size, normalize_symbol


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("EURUSD", "EURUSD"),
            ("EURUSDm", "EURUSD"),     # Libertex 'm' suffix
            ("XAUUSD.pro", "XAUUSD"),  # Tickmill
            ("USTEC.raw", "NAS100"),   # ICMarkets alias
            ("NQ", "NAS100"),          # Libertex index alias
            ("GER40", "DAX"),
            ("", ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_symbol(raw) == expected


class TestPipSize:
    @pytest.mark.parametrize(
        ("symbol", "pip"),
        [
            ("EURUSD", 0.0001),
            ("USDJPY", 0.01),
            ("GBPJPY", 0.01),
            ("XAUUSD", 0.01),
            ("XAGUSD", 0.001),
            ("NAS100", 0.25),
            ("US30", 1.0),
            ("DAX", 0.1),
            ("UNKNOWNPAIR", 0.0001),  # default
        ],
    )
    def test_pip_size(self, symbol, pip):
        assert get_pip_size(symbol) == pip

    def test_config_reexport_is_same_function(self):
        # `from config import get_pip_size` must resolve to the shared impl.
        assert config_get_pip_size is get_pip_size

    def test_broker_suffix_pip(self):
        assert get_pip_size("EURUSDm") == 0.0001
        assert get_pip_size("XAUUSD.raw") == 0.01
