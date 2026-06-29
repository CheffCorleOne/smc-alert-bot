"""
Characterization tests for core.market_structure.

These lock in the CURRENT behaviour of the swing / BOS / CHoCH / bias
detectors so that the upcoming refactor can be proven behaviour-preserving
(or, where behaviour is intentionally changed, the diff is explicit).
"""

from __future__ import annotations

from conftest import candles_from_closes, make_df

from core.market_structure import (
    detect_bos,
    detect_bos_after_index,
    detect_choch,
    detect_swing_highs,
    detect_swing_lows,
    get_market_bias,
    is_higher_high_higher_low,
    is_lower_high_lower_low,
    recent_dealing_range,
)


class TestSwingDetection:
    def test_single_swing_high(self):
        # highs: 1,2,3,2,1 -> pivot at index 2
        df = make_df([(1, 1, 0, 1), (2, 2, 1, 2), (3, 3, 2, 3), (2, 2, 1, 2), (1, 1, 0, 1)])
        highs = detect_swing_highs(df, left_bars=1, right_bars=1)
        assert [s.index for s in highs] == [2]
        assert highs[0].price == 3
        assert highs[0].type == "high"

    def test_single_swing_low(self):
        df = make_df([(3, 3, 3, 3), (2, 2, 2, 2), (1, 1, 1, 1), (2, 2, 2, 2), (3, 3, 3, 3)])
        lows = detect_swing_lows(df, left_bars=1, right_bars=1)
        assert [s.index for s in lows] == [2]
        assert lows[0].price == 1

    def test_no_swing_when_flat(self):
        df = make_df([(1, 1, 1, 1)] * 6)
        assert detect_swing_highs(df, 1, 1) == []
        assert detect_swing_lows(df, 1, 1) == []

    def test_right_bars_required_for_confirmation(self):
        # A rising tail has no confirmed pivot high (needs right_bars lower bars).
        df = candles_from_closes([1, 2, 3, 4, 5], wick=0.0)
        assert detect_swing_highs(df, 1, 1) == []


class TestStructureFlags:
    def test_hh_hl_detected(self, uptrend_df):
        sh = detect_swing_highs(uptrend_df, 1, 1)
        sl = detect_swing_lows(uptrend_df, 1, 1)
        assert is_higher_high_higher_low(sh, sl)
        assert not is_lower_high_lower_low(sh, sl)

    def test_lh_ll_detected(self, downtrend_df):
        sh = detect_swing_highs(downtrend_df, 1, 1)
        sl = detect_swing_lows(downtrend_df, 1, 1)
        assert is_lower_high_lower_low(sh, sl)
        assert not is_higher_high_higher_low(sh, sl)


class TestBOS:
    def test_bullish_bos_breaks_prior_swing_high(self):
        # Make a swing high at idx 2 (price 3), then close above it later.
        df = make_df(
            [
                (1, 1, 0, 1),
                (2, 2, 1, 2),
                (3, 3, 2, 3),   # swing high = 3
                (2, 2, 1, 2),
                (2, 2, 1, 2),
                (3, 5, 2, 4),   # close 4 > 3 -> bullish BOS
            ]
        )
        sh = detect_swing_highs(df, 1, 1)
        sl = detect_swing_lows(df, 1, 1)
        bos = detect_bos(df, sh, sl)
        assert any(b.direction == "bullish" and b.swing_price == 3 for b in bos)

    def test_bos_after_index_filters_earlier_breaks(self, uptrend_df):
        sh = detect_swing_highs(uptrend_df, 1, 1)
        sl = detect_swing_lows(uptrend_df, 1, 1)
        all_bos = detect_bos(uptrend_df, sh, sl)
        if all_bos:
            last_idx = all_bos[-1].index
            assert detect_bos_after_index(uptrend_df, last_idx, "bullish", 1, 1) is None


class TestDealingRange:
    def test_uses_recent_leg_not_global_extremes(self, uptrend_df):
        sh = detect_swing_highs(uptrend_df, 1, 1)
        sl = detect_swing_lows(uptrend_df, 1, 1)
        rng = recent_dealing_range(sh, sl)
        assert rng is not None
        hi, lo = rng
        # Most recent swing high (8 @ idx7) and swing low (1.5 @ idx5).
        assert hi == 8.0
        assert lo == 1.5

    def test_none_without_both_sides(self):
        assert recent_dealing_range([], []) is None


class TestChronologicalFilter:
    """The structure shift must occur AFTER the reference point (sweep), by time."""

    def _bullish_bos_df(self):
        # Bullish BOS lands on the bar at index 5 -> timestamp 05:00 UTC.
        # >=7 bars required by detect_bos_after_index's data-sufficiency guard.
        return make_df(
            [
                (1, 1, 0, 1),
                (2, 2, 1, 2),
                (3, 3, 2, 3),   # swing high = 3
                (2, 2, 1, 2),
                (2, 2, 1, 2),
                (3, 5, 2, 4),   # close 4 > 3 -> bullish BOS @ 05:00
                (4, 4, 3, 3.5),
                (3.5, 4, 3, 3.8),
            ]
        )

    def test_bos_found_when_after_time_precedes_it(self):
        # Event timestamps are naive UTC (numpy datetime64), matching what a
        # real sweep_event.timestamp carries in production.
        from datetime import datetime

        df = self._bullish_bos_df()
        ref = datetime(2024, 1, 1, 4, 0)
        bos = detect_bos_after_index(df, 0, "bullish", 1, 1, after_time=ref)
        assert bos is not None and bos.direction == "bullish"

    def test_bos_rejected_when_after_time_follows_it(self):
        from datetime import datetime

        df = self._bullish_bos_df()
        ref = datetime(2024, 1, 1, 6, 0)
        assert detect_bos_after_index(df, 0, "bullish", 1, 1, after_time=ref) is None


class TestCHoCH:
    def test_bearish_choch_in_uptrend(self):
        # Uptrend (swing lows at idx 1=0 and idx 5=1.5) then a decisive
        # close below the most recent swing low -> bearish CHoCH.
        df = make_df(
            [
                (1.5, 2.0, 1.0, 1.8),   # 0
                (1.0, 2.0, 0.0, 0.5),   # 1 swing low = 0
                (2.0, 4.0, 2.0, 3.5),   # 2
                (3.5, 5.0, 3.0, 4.5),   # 3 swing high = 5
                (3.0, 4.0, 2.0, 2.5),   # 4
                (2.5, 3.0, 1.5, 2.0),   # 5 swing low = 1.5
                (4.0, 7.0, 4.0, 6.5),   # 6
                (6.5, 7.0, 5.0, 5.5),   # 7 swing high = 7
                (2.0, 2.0, 0.5, 0.5),   # 8 close 0.5 < last swing low 1.5
            ]
        )
        sh = detect_swing_highs(df, 1, 1)
        sl = detect_swing_lows(df, 1, 1)
        choch = detect_choch(df, sh, sl, current_trend="bullish")
        assert any(c.direction == "bearish" for c in choch)


class TestSwingCache:
    def test_cached_matches_uncached(self, uptrend_df):
        from core.market_structure import _compute_swing_highs

        cached = detect_swing_highs(uptrend_df, 1, 1)
        direct = _compute_swing_highs(uptrend_df, 1, 1)
        assert [(s.index, s.price) for s in cached] == [(s.index, s.price) for s in direct]

    def test_repeated_call_hits_cache(self, uptrend_df):
        first = detect_swing_highs(uptrend_df, 1, 1)
        second = detect_swing_highs(uptrend_df, 1, 1)
        assert first is second  # same object returned from cache

    def test_different_params_not_confused(self, uptrend_df):
        a = detect_swing_highs(uptrend_df, 1, 1)
        b = detect_swing_highs(uptrend_df, 2, 2)
        # Different sensitivity may yield different swing sets; must not collide.
        assert a is not b or [s.index for s in a] == [s.index for s in b]


class TestMarketBias:
    def test_aligned_uptrend_is_bullish(self):
        df = candles_from_closes([10, 12, 11, 14, 13, 16, 15, 18, 17, 20, 19, 22])
        bias, confidence, score = get_market_bias(df, df, df, 1, 1, min_bias_score=3)
        assert bias == "bullish"
        assert score > 0

    def test_aligned_downtrend_is_bearish(self):
        df = candles_from_closes([22, 20, 21, 18, 19, 16, 17, 14, 15, 12, 13, 10])
        bias, confidence, score = get_market_bias(df, df, df, 1, 1, min_bias_score=3)
        assert bias == "bearish"
        assert score < 0

    def test_missing_data_returns_ranging(self):
        bias, confidence, score = get_market_bias(None, None, None, 1, 1)
        assert bias == "ranging"
        assert score == 0

    def test_weak_bias_allowed_by_default(self):
        # W1 bullish (+3), D1 bearish (-2) -> score +1 < min_bias_score.
        up = candles_from_closes([10, 12, 11, 14, 13, 16, 15, 18, 17, 20, 19, 22])
        down = candles_from_closes([22, 20, 21, 18, 19, 16, 17, 14, 15, 12, 13, 10])
        bias, _, score = get_market_bias(up, down, None, 1, 1, min_bias_score=3)
        assert bias == "bullish"  # hierarchy fallback: W1 dominates

    def test_strict_mode_stands_aside_on_weak_bias(self):
        up = candles_from_closes([10, 12, 11, 14, 13, 16, 15, 18, 17, 20, 19, 22])
        down = candles_from_closes([22, 20, 21, 18, 19, 16, 17, 14, 15, 12, 13, 10])
        bias, _, score = get_market_bias(
            up, down, None, 1, 1, min_bias_score=3, allow_weak_bias=False
        )
        assert bias == "ranging"
