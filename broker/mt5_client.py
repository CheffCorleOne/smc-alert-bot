"""
Single lazy accessor for the MetaTrader5 module.

Previously every module that touched MT5 (`connector`, `fetcher`,
`entry_engine`, `risk_manager`, `order_manager`, `pending_intent`) defined its
own private ``_get_mt5()`` with an inline import. This centralises that so the
import happens once and can be mocked in tests from one place.

MetaTrader5 is Windows-only and imported lazily, so importing this module on
Linux/CI is safe — the import only fires when ``get_mt5()`` is first called.
"""

from __future__ import annotations

from typing import Any

_mt5: Any | None = None


def get_mt5() -> Any:
    """Return the imported MetaTrader5 module, importing it on first use."""
    global _mt5
    if _mt5 is None:
        import MetaTrader5 as mt5  # noqa: N813  (third-party name)

        _mt5 = mt5
    return _mt5


def is_terminal_up() -> bool:
    """True if the MT5 terminal is reachable (initialised)."""
    try:
        return get_mt5().terminal_info() is not None
    except Exception:
        return False
