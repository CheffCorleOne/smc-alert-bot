"""
Characterization tests for core.liquidity, core.poi and core.premium_discount.
"""

from __future__ import annotations

from datetime import datetime

from conftest import make_df

from core.liquidity import LiquidityEngine
from core.market_structure import detect_swing_highs, detect_swing_lows
from core.poi import POIEngine
from core.premium_discount import (
    get_premium_discount_zones,
    is_in_discount,
    is_in_ote,
    is_in_premium,
)


class TestEqualLevels:
    def test_equal_highs_clustered(self):
        # Two swing highs at ~the same price -> one EQH cluster.
        df = make_df(
            [
                (1, 1, 0, 1),
                (2, 2, 1, 2),
                (3, 3.0000, 2, 3),   # swing high 1
                (2, 2, 1, 2),
                (2, 2, 1, 2),
                (2, 3.0001, 1, 2),   # swing high 2 (near-equal)
                (1, 1, 0, 1),
            ]
        )
        sh = detect_swing_highs(df, 1, 1)
        liq = LiquidityEngine("EURUSD")
        eqh = liq.find_eqh(df, sh, tolerance_pips=5)
        assert len(eqh) >= 1
        assert eqh[0].count >= 2
        assert eqh[0].type == "eqh"


class TestSweep:
    def test_wick_sweep_below_then_close_above(self):
        level = 1.0
        # Last candle wicks below level but closes back above -> sweep "below".
        df = make_df(
            [
                (1.05, 1.06, 1.02, 1.04),
                (1.04, 1.05, 1.01, 1.03),
                (1.03, 1.04, 0.95, 1.02),  # wick to 0.95 < 1.0, close 1.02 > 1.0
            ]
        )
        liq = LiquidityEngine("EURUSD")
        sweep = liq.detect_sweep(df, level, "below", lookback=5, require_displacement=False)
        assert sweep is not None
        assert sweep.direction == "below"

    def test_no_sweep_when_level_untouched(self):
        df = make_df([(1.05, 1.06, 1.04, 1.05)] * 4)
        liq = LiquidityEngine("EURUSD")
        assert liq.detect_sweep(df, 1.0, "below", require_displacement=False) is None


class TestAsianRange:
    def test_winter_range_and_sweep(self):
        # Jan 2 2024: NY midnight = 05:00 UTC, so Asian range = 00:00–05:00 UTC.
        df = make_df(
            [
                (1.06, 1.07, 1.05, 1.06),  # 00:00 UTC
                (1.06, 1.08, 1.05, 1.07),  # 01:00
                (1.07, 1.10, 1.06, 1.08),  # 02:00 -> Asian HIGH 1.10
                (1.08, 1.09, 1.04, 1.05),  # 03:00 -> Asian LOW 1.04
                (1.05, 1.07, 1.05, 1.06),  # 04:00
                (1.06, 1.07, 1.05, 1.06),  # 05:00 -> Midnight Open candle (open 1.06)
                (1.06, 1.12, 1.05, 1.08),  # 06:00 -> wick 1.12 > 1.10, close 1.08 < high => swept
                (1.08, 1.09, 1.07, 1.08),  # 07:00
                (1.08, 1.09, 1.07, 1.08),  # 08:00
                (1.08, 1.09, 1.07, 1.08),  # 09:00
            ],
            start=datetime(2024, 1, 2),
            freq_minutes=60,
        )
        rng = LiquidityEngine.get_asian_range(df)
        assert rng is not None
        assert abs(rng["high"] - 1.10) < 1e-9
        assert abs(rng["low"] - 1.04) < 1e-9
        assert abs(rng["midnight_open"] - 1.06) < 1e-9
        assert rng["high_swept"] is True
        assert rng["low_swept"] is False


class TestFVG:
    def test_bullish_fvg_detected(self):
        # candle[i-1].high < candle[i+1].low -> bullish gap at middle index 1.
        df = make_df(
            [
                (1.00, 1.01, 0.99, 1.005),   # high 1.01
                (1.02, 1.10, 1.02, 1.08),    # displacement up
                (1.09, 1.12, 1.05, 1.10),    # low 1.05 > 1.01 -> gap
                (1.10, 1.11, 1.08, 1.09),
            ]
        )
        poi = POIEngine("EURUSD")
        fvgs = poi.find_fvg(df, min_size_pips=2)
        assert any(f.type == "bullish" for f in fvgs)


class TestPremiumDiscount:
    def test_zone_boundaries(self):
        zones = get_premium_discount_zones(swing_high=100.0, swing_low=0.0)
        assert zones["equilibrium"] == 50.0
        assert is_in_discount(25.0, zones) is True
        assert is_in_discount(75.0, zones) is False
        assert is_in_premium(75.0, zones) is True
        assert is_in_ote(70.0, zones) is True   # 0.62*100=62 .. 0.79*100=79
        assert is_in_ote(50.0, zones) is False

    def test_empty_for_invalid_range(self):
        assert get_premium_discount_zones(10.0, 20.0) == {}
