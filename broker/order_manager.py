"""
Order Manager -- read-only MT5 exposure helpers.

The notifier version of the bot does not place, modify, or close orders.
This class only exposes the MT5 position/history reads still used by the
scanner and dashboard.
"""

from datetime import datetime, timezone
from typing import Any, List

from config import BotConfig
from utils.logger import get_logger

logger = get_logger("orders")

_mt5 = None


def _get_mt5():
    global _mt5
    if _mt5 is None:
        import MetaTrader5 as mt5
        _mt5 = mt5
    return _mt5


class OrderManager:
    """Read open exposure and closed trade history from MT5."""

    def __init__(
        self,
        config: BotConfig,
        connector: Any = None,
        db: Any = None,
    ) -> None:
        self.config = config
        self.connector = connector
        self.db = db

    def _resolve(self, symbol: str) -> str:
        """Resolve a base symbol to the broker MT5 name."""
        return self.config.get_mt5_symbol(symbol)

    def _check_connection(self) -> bool:
        """Verify MT5 connection is active."""
        try:
            mt5 = _get_mt5()
            info = mt5.terminal_info()
            return info is not None
        except Exception:
            return False

    def has_open_exposure(self, symbol: str) -> bool:
        """
        True if the account already has an open position or bot pending order
        on this instrument.
        """
        names = self.config.symbol_exposure_names(symbol)
        resolved = self._resolve(symbol)

        for pos in self.get_open_positions():
            pos_symbol = getattr(pos, "symbol", "") or ""
            if pos_symbol.upper() in names:
                return True

        try:
            mt5 = _get_mt5()
            for pending in mt5.orders_get(symbol=resolved) or []:
                if getattr(pending, "magic", 0) == 202601:
                    return True
        except Exception as exc:
            logger.debug(f"Pending order check failed for {symbol}: {exc}")

        return False

    def get_open_positions(self) -> List:
        """Get all open positions created by this bot."""
        if not self._check_connection():
            return []

        try:
            mt5 = _get_mt5()
            positions = mt5.positions_get()
            if positions is None:
                return []
            return [p for p in positions if getattr(p, "magic", 0) == 202601]
        except Exception as exc:
            logger.error(f"Error getting positions: {exc}")
            return []

    def get_today_closed_trades(self) -> List:
        """Get all bot trades closed today."""
        if not self._check_connection():
            return []

        try:
            mt5 = _get_mt5()
            today = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            deals = mt5.history_deals_get(today, datetime.now(timezone.utc))
            if deals is None:
                return []
            return [d for d in deals if getattr(d, "magic", 0) == 202601]
        except Exception as exc:
            logger.error(f"Error getting closed trades: {exc}")
            return []
