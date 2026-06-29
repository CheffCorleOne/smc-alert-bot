"""
Risk Manager -- position sizing and daily limits.

The notifier bot does not manage live trades. This module keeps the sizing
and statistics helpers used to build advisory signals and protect scans when
daily limits are reached.
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import BotConfig, get_pip_size
from utils.logger import get_logger

logger = get_logger("risk")

from broker.mt5_client import get_mt5 as _get_mt5  # noqa: E402  (kept local alias)


class RiskManager:
    """Calculates lot size and checks account-level daily limits."""

    def __init__(self, config: BotConfig, connector: Any = None) -> None:
        self.config = config
        self.connector = connector
        self._daily_start_balance: Optional[float] = None
        self._current_day_str: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def calculate_lot_size(
        self,
        symbol: str,
        sl_pips: float,
        risk_pct: Optional[float] = None,
    ) -> float:
        """
        Calculate lot size from account balance, risk percent, and stop distance.
        """
        if risk_pct is None:
            risk_pct = self.config.risk_per_trade_pct

        if sl_pips <= 0:
            logger.error("SL pips must be positive")
            return 0.0

        try:
            mt5 = _get_mt5()
            account = mt5.account_info()
            if account is None:
                logger.error("Cannot get account info for lot calculation")
                return 0.0

            sym_info = mt5.symbol_info(symbol)
            if sym_info is None:
                logger.error(f"Symbol info not available for {symbol}")
                return 0.0

            pip_size = get_pip_size(symbol)
            tick_value = sym_info.trade_tick_value
            tick_size = sym_info.trade_tick_size

            if tick_size <= 0 or tick_value <= 0:
                logger.error(f"Invalid tick info for {symbol}")
                return 0.0

            logger.debug(
                f"[{symbol}] tick_value={tick_value}, "
                f"tick_size={tick_size}, "
                f"contract_size={sym_info.trade_contract_size}, "
                f"currency_profit={getattr(sym_info, 'currency_profit', 'n/a')}, "
                f"account_currency={getattr(account, 'currency', 'n/a')}"
            )

            pip_value = (pip_size / tick_size) * tick_value
            risk_amount = account.balance * risk_pct / 100.0
            lot_raw = risk_amount / (sl_pips * pip_value)

            vol_step = sym_info.volume_step
            vol_min = sym_info.volume_min
            vol_max = sym_info.volume_max

            if vol_step <= 0:
                logger.error(f"Invalid volume step for {symbol}: {vol_step}")
                return 0.0

            steps = math.floor(lot_raw / vol_step)
            lot = steps * vol_step
            lot = max(vol_min, min(lot, vol_max))
            lot = round(lot, 8)

            logger.info(
                f"Lot calc: {symbol} | Balance={account.balance} | "
                f"Risk={risk_pct}% | SL={sl_pips}pips | Lot={lot}"
            )
            return lot

        except Exception as exc:
            logger.error(f"Lot calculation error: {exc}")
            return 0.0

    def is_daily_limit_reached(self, db: Any = None) -> bool:
        """
        Check if daily trade count, loss streak, or drawdown limits are hit.
        """
        try:
            mt5 = _get_mt5()
            account = mt5.account_info()
            if account is None:
                return True

            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if now_str != getattr(self, "_current_day_str", ""):
                self._current_day_str = now_str
                self._daily_start_balance = account.balance
                logger.info(f"Day rollover detected. New start balance: {account.balance}")

            if getattr(self, "_daily_start_balance", None) is None:
                if db:
                    today_trades = db.get_today_trades()
                    today_realized_pnl = sum(
                        float(t.get("pnl", 0.0))
                        for t in today_trades
                        if t.get("status") == "closed"
                    )
                    self._daily_start_balance = account.balance - today_realized_pnl
                    logger.info(
                        f"Reconstructed start balance: {self._daily_start_balance} "
                        f"(Balance: {account.balance}, Today PnL: {today_realized_pnl})"
                    )
                else:
                    self._daily_start_balance = account.balance

            current_equity = account.equity
            drawdown = (
                (self._daily_start_balance - current_equity)
                / self._daily_start_balance
                * 100
            )

            max_drawdown = getattr(self.config, "daily_drawdown_pct", 3.0)
            if max_drawdown > 0 and drawdown >= max_drawdown:
                logger.warning(
                    f"Daily drawdown limit hit: {drawdown:.1f}% >= "
                    f"{max_drawdown}%"
                )
                return True

            if db:
                today_trades = db.get_today_trades()
                consecutive_losses = 0
                for trade in today_trades:
                    if trade.get("status") == "closed":
                        pnl = float(trade.get("pnl", 0.0))
                        if pnl < 0:
                            consecutive_losses += 1
                        else:
                            break

                max_losses = getattr(self.config, "max_consecutive_losses", 3)
                if max_losses > 0 and consecutive_losses >= max_losses:
                    logger.warning(
                        f"Consecutive losses limit hit: "
                        f"{consecutive_losses} >= {max_losses}"
                    )
                    return True

                max_trades = getattr(self.config, "max_trades_per_day", 0)
                if max_trades > 0 and len(today_trades) >= max_trades:
                    logger.info(
                        f"Daily trade limit reached: "
                        f"{len(today_trades)}/{max_trades}"
                    )
                    return True

            return False

        except Exception as exc:
            logger.error(f"Error checking daily limits: {exc}")
            return True

    def get_daily_stats(self, db: Any = None) -> Dict:
        """
        Get today's trading statistics for the dashboard.
        """
        stats = {
            "trades_today": 0,
            "wins": 0,
            "losses": 0,
            "pnl_today": 0.0,
            "drawdown": 0.0,
        }

        try:
            mt5 = _get_mt5()
            account = mt5.account_info()
            if account and self._daily_start_balance:
                stats["drawdown"] = round(
                    (
                        (self._daily_start_balance - account.equity)
                        / self._daily_start_balance
                        * 100
                    ),
                    2,
                )

            if db:
                db_stats = db.get_daily_stats()
                stats.update({
                    "trades_today": db_stats.get("total_trades", 0),
                    "wins": db_stats.get("wins", 0),
                    "losses": db_stats.get("losses", 0),
                    "pnl_today": db_stats.get("total_pnl", 0.0),
                })

        except Exception as exc:
            logger.error(f"Error getting daily stats: {exc}")

        return stats

    def reset_daily(self) -> None:
        """Reset daily tracking for dashboard statistics."""
        try:
            mt5 = _get_mt5()
            account = mt5.account_info()
            if account:
                self._daily_start_balance = account.balance
                self._current_day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                logger.info(f"Daily reset -- Start balance: {account.balance}")
        except Exception as exc:
            logger.error(f"Error during daily reset: {exc}")
