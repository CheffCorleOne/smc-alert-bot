"""Tests for env-based secrets and config.from_dict behaviour."""

from __future__ import annotations

from config import BotConfig
from utils.settings import load_secrets


class TestSecrets:
    def test_reads_from_env(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # avoid picking up a real .env
        monkeypatch.setenv("TELEGRAM_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        s = load_secrets()
        assert s.telegram_token == "abc123"
        assert s.telegram_chat_id == "999"

    def test_defaults_empty(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        s = load_secrets()
        assert s.telegram_token == ""
        assert s.telegram_chat_id == ""


class TestFromDict:
    def test_no_silent_min_bias_override(self):
        # Previously forced to >=3; must now be respected.
        cfg = BotConfig.from_dict({"min_bias_score": 1})
        assert cfg.min_bias_score == 1

    def test_no_silent_max_intents_override(self):
        cfg = BotConfig.from_dict({"max_pending_intents": 2})
        assert cfg.max_pending_intents == 2

    def test_unknown_keys_ignored(self):
        cfg = BotConfig.from_dict({"totally_unknown": 5, "risk_per_trade_pct": 2.0})
        assert cfg.risk_per_trade_pct == 2.0
        assert not hasattr(cfg, "totally_unknown")

    def test_roundtrip(self):
        cfg = BotConfig(risk_per_trade_pct=1.5, min_setup_score=70.0)
        restored = BotConfig.from_dict(cfg.to_dict())
        assert restored.risk_per_trade_pct == 1.5
        assert restored.min_setup_score == 70.0
