"""Entry-engine sweep gating tests."""

from __future__ import annotations

from conftest import make_df

from config import BotConfig
from core.entry_engine import SMCEntryEngine


def _wick_close_sweep_without_displacement():
    rows = [(1.05, 1.06, 1.04, 1.05)] * 16
    rows.append((1.04, 1.05, 0.98, 1.03))
    return make_df(rows, freq_minutes=60)


def test_sweep_without_displacement_passes_by_default():
    engine = SMCEntryEngine(BotConfig())
    step = engine._step3_sweep(
        {"H1": _wick_close_sweep_without_displacement()},
        "EURUSD",
        {"target_level": 1.0, "target_type": "EQL (sell-side)"},
    )

    assert step.passed is True
    assert step.data["sweep"].direction == "below"
    assert step.data["sweep_has_displacement"] is False
    assert step.data["sweep"].has_displacement is False


def test_sweep_without_displacement_fails_when_required():
    engine = SMCEntryEngine(BotConfig(require_sweep_displacement=True))
    step = engine._step3_sweep(
        {"H1": _wick_close_sweep_without_displacement()},
        "EURUSD",
        {"target_level": 1.0, "target_type": "EQL (sell-side)"},
    )

    assert step.passed is False
