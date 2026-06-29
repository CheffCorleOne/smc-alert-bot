"""Tests for PendingIntent serialisation and IntentManager state restore."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.pending_intent import IntentManager, PendingIntent


def _make_manager_with_intent():
    mgr = IntentManager(max_age_hours=6.0, max_total=15)
    mgr.create(
        symbol="EURUSD",
        direction="buy",
        htf_bias="bullish",
        waiting_for="sweep",
        liquidity_target=1.0750,
        liquidity_type="EQL (sell-side)",
    )
    return mgr


class TestIntentPersistence:
    def test_roundtrip(self):
        mgr = _make_manager_with_intent()
        state = mgr.export_state()
        assert len(state) == 1

        restored = IntentManager(max_age_hours=6.0, max_total=15)
        n = restored.import_state(state)
        assert n == 1
        intent = restored.get("EURUSD")
        assert intent is not None
        assert intent.direction == "buy"
        assert intent.htf_bias == "bullish"
        assert intent.liquidity_target == 1.0750
        assert intent.waiting_for == "sweep"

    def test_expired_intent_skipped_on_import(self):
        mgr = _make_manager_with_intent()
        state = mgr.export_state()
        state[0]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()

        restored = IntentManager()
        assert restored.import_state(state) == 0

    def test_state_dict_is_json_safe(self):
        import json

        mgr = _make_manager_with_intent()
        # Must serialise without custom encoders (floats/str/None/iso strings).
        dumped = json.dumps(mgr.export_state())
        assert "EURUSD" in dumped

    def test_to_from_state_dict_direct(self):
        intent = PendingIntent(symbol="XAUUSD", direction="sell", htf_bias="bearish")
        intent.poi_top = 2400.5
        intent.poi_bottom = 2398.0
        restored = PendingIntent.from_state_dict(intent.to_state_dict())
        assert restored.symbol == "XAUUSD"
        assert restored.direction == "sell"
        assert restored.poi_top == 2400.5
