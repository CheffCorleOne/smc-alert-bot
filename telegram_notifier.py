"""Telegram signal notifier for the SMC advisory bot."""

import asyncio
import math
import threading
from typing import Any, Dict, Optional

from config import get_pip_size
from utils.logger import get_logger

logger = get_logger("telegram")


class TelegramNotifier:
    """Send SMC trade signals to Telegram and track user acknowledgements."""

    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.active_signals: Dict[str, Dict[str, Any]] = {}
        self.notified_signals: Dict[str, str] = {}
        self.acknowledged_signals: Dict[str, str] = {}
        self._application: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._polling_thread: Optional[threading.Thread] = None

    def bind_state(
        self,
        active_signals: Dict[str, Dict[str, Any]],
        notified_signals: Dict[str, str],
        acknowledged_signals: Dict[str, str],
    ) -> None:
        """Bind notifier dictionaries to bot_state so callbacks update the dashboard."""
        self.active_signals = active_signals
        self.notified_signals = notified_signals
        self.acknowledged_signals = acknowledged_signals

    @staticmethod
    def signal_key(signal: Any) -> str:
        """Stable signal dedupe key: direction + entry rounded to 5 decimals."""
        return f"{signal.direction}_{float(signal.entry_price):.5f}"

    @staticmethod
    def estimate_hold_time(session: str, rr_ratio: float) -> str:
        """Estimate hold time from session speed and RR target."""
        session_name = (session or "").lower().replace("_", " ")
        if "asian" in session_name:
            multiplier = 3.0
        elif "london close" in session_name:
            multiplier = 1.0
        elif "london" in session_name:
            multiplier = 1.5
        elif "new york" in session_name or session_name.startswith("ny") or " ny" in session_name:
            multiplier = 2.0
        else:
            multiplier = 2.0

        estimate = max(0.5, float(rr_ratio or 0) * multiplier)
        lower = max(0.5, math.floor(estimate * 2) / 2)
        upper = math.ceil(estimate * 2) / 2
        if upper <= lower:
            upper = lower + 0.5

        def fmt(hours: float) -> str:
            return str(int(hours)) if hours.is_integer() else f"{hours:.1f}"

        return f"~{fmt(lower)}-{fmt(upper)} hours"

    def send_signal(self, signal: Any, lot: float, estimated_hours: str) -> bool:
        """Send a formatted signal message with an acknowledgement button."""
        if not self.token or not self.chat_id:
            return False

        try:
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._send_signal_async(signal, lot, estimated_hours),
                    self._loop,
                )
                future.add_done_callback(self._log_future_error)
                return True

            asyncio.run(self._send_signal_async(signal, lot, estimated_hours))
            return True
        except Exception as exc:
            logger.error(f"Telegram signal send failed: {exc}")
            return False

    async def _send_signal_async(self, signal: Any, lot: float, estimated_hours: str) -> None:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

        bot = self._application.bot if self._application else Bot(self.token)
        key = self.signal_key(signal)
        callback_data = f"ack|{signal.symbol}|{key}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Order Placed", callback_data=callback_data)
        ]])

        await bot.send_message(
            chat_id=self.chat_id,
            text=self._format_signal_message(signal, lot, estimated_hours),
            reply_markup=keyboard,
        )
        logger.info(f"Telegram signal sent: {signal.symbol} {key}")

    def _format_signal_message(self, signal: Any, lot: float, estimated_hours: str) -> str:
        icon = "🟢" if signal.direction == "buy" else "🔴"
        direction = signal.direction.upper()
        separator = "━━━━━━━━━━━━━━"
        pip_size = get_pip_size(signal.symbol)
        sl_pips = abs(signal.entry_price - signal.sl_price) / pip_size
        tp_pips = abs(signal.tp_price - signal.entry_price) / pip_size

        return (
            f"{icon} {signal.symbol} {direction}\n"
            f"{separator}\n"
            f"📦 Lot Size:  {lot:.2f}\n"
            f"🎯 Entry:     {signal.entry_price:.5f}\n"
            f"🛑 Stop Loss: {signal.sl_price:.5f}  (-{sl_pips:.0f} pips)\n"
            f"✅ Take Profit: {signal.tp_price:.5f}  (+{tp_pips:.0f} pips)\n"
            f"📊 RR Ratio:  1:{signal.rr_ratio:.1f}\n"
            f"⭐ Score:      {signal.setup_score:.0f}/100\n"
            f"⏱ Est. hold:  {estimated_hours}\n"
            f"📍 Session:   {signal.session}\n"
            f"{separator}"
        )

    def start_polling(self) -> None:
        """Start async Telegram polling in a background thread."""
        if not self.token or not self.chat_id:
            return
        if self._polling_thread and self._polling_thread.is_alive():
            return

        self._polling_thread = threading.Thread(
            target=self._run_polling_thread,
            daemon=True,
            name="telegram-polling",
        )
        self._polling_thread.start()

    def _run_polling_thread(self) -> None:
        try:
            from telegram.ext import Application, CallbackQueryHandler, CommandHandler
        except Exception as exc:
            logger.error(f"Telegram library not available: {exc}")
            return

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            self._application = Application.builder().token(self.token).build()
            self._application.add_handler(CommandHandler("status", self._status_command))
            self._application.add_handler(CallbackQueryHandler(self._callback_update))

            async def start() -> None:
                await self._application.initialize()
                await self._application.start()
                if self._application.updater:
                    await self._application.updater.start_polling()
                logger.info("Telegram polling started")

            loop.run_until_complete(start())
            loop.run_forever()
        except Exception as exc:
            logger.error(f"Telegram polling failed: {exc}")

    async def _callback_update(self, update: Any, _context: Any) -> None:
        query = update.callback_query
        if not query:
            return
        if not self._is_allowed_chat(getattr(query.message, "chat_id", None)):
            await query.answer("Unauthorized chat", show_alert=True)
            return
        await self.handle_callback(query)

    async def handle_callback(self, callback_query: Any) -> None:
        """Process an 'Order Placed' callback and mark the signal acknowledged."""
        try:
            data = callback_query.data or ""
            parts = data.split("|", 2)
            if len(parts) != 3 or parts[0] != "ack":
                await callback_query.answer("Unknown action", show_alert=True)
                return

            symbol, key = parts[1], parts[2]
            self.acknowledged_signals[symbol] = key
            if symbol in self.active_signals:
                self.active_signals[symbol]["acknowledged"] = True
            await callback_query.answer(f"{symbol} marked as order placed")
            try:
                await callback_query.edit_message_reply_markup(reply_markup=None)
            except Exception as exc:
                logger.debug(f"Telegram callback markup edit skipped: {exc}")
            logger.info(f"Telegram acknowledgement received: {symbol} {key}")
        except Exception as exc:
            logger.error(f"Telegram callback error: {exc}")
            try:
                await callback_query.answer("Could not mark signal", show_alert=True)
            except Exception:
                pass

    async def _status_command(self, update: Any, _context: Any) -> None:
        if not self._is_allowed_chat(getattr(update.effective_chat, "id", None)):
            return

        if not self.active_signals:
            await update.message.reply_text("No active signals.")
            return

        lines = ["Active signals:"]
        for symbol, signal in sorted(self.active_signals.items()):
            key = signal.get("signal_key") or (
                f"{signal.get('direction', '')}_{float(signal.get('entry_price', 0.0)):.5f}"
            )
            acked = self.acknowledged_signals.get(symbol) == key
            icon = "✅" if acked else "⏳"
            direction = str(signal.get("direction", "")).upper()
            entry = float(signal.get("entry_price", 0.0))
            rr = float(signal.get("rr_ratio", 0.0))
            score = float(signal.get("setup_score", 0.0))
            lines.append(
                f"{icon} {symbol} {direction} @ {entry:.5f} | RR 1:{rr:.1f} | Score {score:.0f}"
            )

        await update.message.reply_text("\n".join(lines))

    def _is_allowed_chat(self, chat_id: Any) -> bool:
        return str(chat_id) == self.chat_id

    @staticmethod
    def _log_future_error(future: Any) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.error(f"Telegram async send failed: {exc}")
