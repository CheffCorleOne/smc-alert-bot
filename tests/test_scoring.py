"""Tests for the weighted setup-quality score."""

from __future__ import annotations

from types import SimpleNamespace

from config import BotConfig
from core.entry_engine import SMCEntryEngine


def _engine():
    return SMCEntryEngine(BotConfig())


def _poi(top, bottom):
    return SimpleNamespace(top=top, bottom=bottom)


class TestQualityScore:
    def test_a_plus_setup_scores_high(self):
        e = _engine()
        score = e.score_setup(
            is_killzone=True,
            bias_score=6,
            shift_type="choch",
            has_displacement=True,
            sweep_has_displacement=True,
            poi=_poi(1.0100, 1.0050),   # 0.5 ATR wide
            atr_value=0.01,
            in_ote=True,
            rr=3.0,
            is_silver_bullet=True,
        )
        assert score >= 85

    def test_weak_setup_scores_low(self):
        e = _engine()
        score = e.score_setup(
            is_killzone=False,           # outside KZ penalty
            bias_score=1,                # weak bias
            shift_type="bos",            # no MSS
            has_displacement=False,
            sweep_has_displacement=False,
            poi=_poi(1.0500, 1.0000),    # 5 ATR wide -> 0 quality
            atr_value=0.01,
            in_ote=False,
            rr=1.0,
        )
        assert score < 55

    def test_strong_beats_weak(self):
        e = _engine()
        strong = e.score_setup(
            is_killzone=True, bias_score=6, shift_type="choch",
            has_displacement=True, poi=_poi(1.01, 1.005), atr_value=0.01,
            in_ote=True, rr=3.0,
        )
        weak = e.score_setup(
            is_killzone=True, bias_score=2, shift_type="bos",
            has_displacement=False, poi=_poi(1.05, 1.0), atr_value=0.01,
            in_ote=False, rr=1.5,
        )
        assert strong > weak

    def test_killzone_gate_costs_points(self):
        e = _engine()
        kwargs = dict(
            bias_score=4, shift_type="choch", has_displacement=True,
            poi=_poi(1.01, 1.005), atr_value=0.01, in_ote=True, rr=2.5,
        )
        in_kz = e.score_setup(is_killzone=True, **kwargs)
        out_kz = e.score_setup(is_killzone=False, **kwargs)
        assert in_kz - out_kz == e.config.outside_kz_score_penalty

    def test_score_bounded(self):
        e = _engine()
        score = e.score_setup(
            is_killzone=True, bias_score=99, shift_type="choch",
            has_displacement=True, poi=_poi(1.01, 1.0099), atr_value=0.01,
            in_ote=True, rr=99.0, is_silver_bullet=True,
        )
        assert 0.0 <= score <= 100.0
