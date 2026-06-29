"""
Secrets / environment configuration via pydantic-settings.

Telegram credentials must NOT live in the SQLite settings store long-term — a
secret in the DB is easy to leak. This module reads them from the environment
(or a local, git-ignored ``.env`` file), which is the standard 12-factor
approach. Env values take precedence over anything saved in the DB.

Degrades gracefully if pydantic-settings is not installed (falls back to
os.getenv) so the bot never hard-crashes on a missing dev dependency.
"""

from __future__ import annotations

import os

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Secrets(BaseSettings):
        """Reads TELEGRAM_TOKEN / TELEGRAM_CHAT_ID from env or .env."""

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )

        telegram_token: str = ""
        telegram_chat_id: str = ""

    def load_secrets() -> "Secrets":
        return Secrets()

except Exception:  # pragma: no cover - exercised only without pydantic-settings
    try:
        from dotenv import load_dotenv as _load_dotenv
    except Exception:
        _load_dotenv = None

    class Secrets:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
            self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    def load_secrets() -> "Secrets":
        if _load_dotenv is not None:
            _load_dotenv()
        return Secrets()
