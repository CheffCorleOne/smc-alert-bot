"""
Pending Intent System — Bot's Memory & Decision Queue.

When the 9-step analysis partially succeeds (e.g. HTF bias + liquidity
confirmed, but no sweep yet), the bot creates a PendingIntent instead of
discarding all progress.  Each subsequent scan cycle checks whether the
missing condition has materialised.

Think of it as an institutional trader writing on a whiteboard:
  "EURUSD — bullish bias, EQL at 1.0750 not yet swept.
   IF price sweeps 1.0750 AND CHoCH on H1 → BUY from OB in discount."
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
import threading

from broker.mt5_client import get_mt5
from utils.logger import get_logger

logger = get_logger("intent")


def _cancel_mt5_limit_order(ticket: int) -> bool:
    """Send trade request directly to MT5 to delete a pending limit order."""
    try:
        mt5 = get_mt5()
        # Check if connected/initialized
        if mt5.terminal_info() is None:
            if not mt5.initialize():
                logger.error(f"Failed to initialize MT5 to cancel limit order #{ticket}")
                return False
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Successfully cancelled MT5 limit order #{ticket}")
            return True
        else:
            retcode = result.retcode if result else "No response"
            logger.error(f"Failed to cancel MT5 limit order #{ticket}, retcode: {retcode}")
            return False
    except Exception as e:
        logger.error(f"Error cancelling MT5 limit order #{ticket}: {e}")
        return False



# ── Data Structures ──────────────────────────────────────────

@dataclass
class PendingIntent:
    """A remembered trade setup that the bot is waiting to trigger."""

    symbol: str
    direction: str                         # 'buy' or 'sell'
    htf_bias: str                          # 'bullish' or 'bearish'

    # Human-readable narrative (shown on dashboard)
    narrative: str = ""

    # ── What has been confirmed ──
    phase: str = "watching"                # watching → ready → armed → triggered → expired/invalid
    steps_confirmed: Dict[str, str] = field(default_factory=dict)

    # Key data from confirmed steps
    liquidity_target: Optional[float] = None
    liquidity_type: str = ""               # e.g. "EQL (sell-side)"
    sweep_level: Optional[float] = None
    sweep_has_displacement: bool = False
    choch_direction: str = ""              # 'bullish' or 'bearish'
    poi_top: Optional[float] = None
    poi_bottom: Optional[float] = None
    poi_type: str = ""                     # e.g. "Bullish OB on M15"

    # ── What we're waiting for ──
    waiting_for: str = ""                  # 'sweep' | 'choch' | 'poi_tap' | 'confirmation'
    waiting_detail: str = ""               # human description

    # ── Limit order (when armed) ──
    limit_entry: Optional[float] = None
    limit_sl: Optional[float] = None
    limit_tp: Optional[float] = None
    limit_ticket: Optional[int] = None     # MT5 ticket if placed

    # ── Structural RR analysis ──
    expected_rr: Optional[float] = None
    tp1_level: Optional[float] = None      # nearest structural TP
    tp2_level: Optional[float] = None      # full run TP

    # ── Lifecycle ──
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    last_checked: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    check_count: int = 0

    invalidated: bool = False
    invalidation_reason: str = ""

    def is_expired(self) -> bool:
        """Check if the intent has exceeded its max age."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def is_active(self) -> bool:
        """Check if the intent is still actionable."""
        return (
            not self.invalidated
            and not self.is_expired()
            and self.phase not in ("triggered", "expired", "invalid")
        )

    def invalidate(self, reason: str) -> None:
        """Mark intent as no longer valid."""
        self.invalidated = True
        self.invalidation_reason = reason
        self.phase = "invalid"
        logger.info(f"[{self.symbol}] Intent invalidated: {reason}")
        
        # Cancel any active MT5 limit order associated with this intent
        if self.limit_ticket is not None:
            _cancel_mt5_limit_order(self.limit_ticket)
            self.limit_ticket = None

    def advance_phase(self, new_phase: str, detail: str = "") -> None:
        """Move intent to the next phase."""
        old = self.phase
        self.phase = new_phase
        self.last_checked = datetime.now(timezone.utc)
        logger.info(
            f"[{self.symbol}] Intent {old} → {new_phase}"
            + (f" ({detail})" if detail else "")
        )

    def update_narrative(self) -> None:
        """Rebuild the human-readable narrative from current state."""
        parts = []
        parts.append(f"HTF: {self.htf_bias}")

        if self.liquidity_target:
            parts.append(f"Target: {self.liquidity_type} @ {self.liquidity_target}")

        if self.sweep_level:
            parts.append(f"Swept @ {self.sweep_level}")

        if self.choch_direction:
            parts.append(f"CHoCH: {self.choch_direction}")

        if self.poi_type:
            parts.append(
                f"POI: {self.poi_type} [{self.poi_bottom}–{self.poi_top}]"
            )

        if self.waiting_for:
            parts.append(f"⏳ Waiting: {self.waiting_detail or self.waiting_for}")

        if self.limit_ticket:
            parts.append(f"📍 Limit #{self.limit_ticket} @ {self.limit_entry}")

        self.narrative = " → ".join(parts)

    # ── Persistence ──────────────────────────────────────────
    _STATE_FIELDS = (
        "symbol", "direction", "htf_bias", "narrative", "phase",
        "waiting_for", "waiting_detail", "liquidity_target", "liquidity_type",
        "sweep_level", "sweep_has_displacement", "choch_direction", "poi_top", "poi_bottom", "poi_type",
        "limit_entry", "limit_sl", "limit_tp", "limit_ticket",
    )

    def to_state_dict(self) -> Dict[str, Any]:
        """Serialise the intent so it survives a process restart."""
        state = {f: getattr(self, f) for f in self._STATE_FIELDS}
        state["created_at"] = self.created_at.isoformat()
        state["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        return state

    @classmethod
    def from_state_dict(cls, state: Dict[str, Any]) -> "PendingIntent":
        """Rebuild an intent from ``to_state_dict`` output."""
        kwargs = {f: state.get(f) for f in cls._STATE_FIELDS if state.get(f) is not None}
        intent = cls(**kwargs)
        created = state.get("created_at")
        expires = state.get("expires_at")
        if created:
            intent.created_at = datetime.fromisoformat(created)
        intent.expires_at = datetime.fromisoformat(expires) if expires else None
        return intent

    def to_dashboard_dict(self) -> Dict[str, Any]:
        """Serialize for dashboard display."""
        age_minutes = (
            datetime.now(timezone.utc) - self.created_at
        ).total_seconds() / 60

        ttl_minutes = None
        if self.expires_at:
            ttl_minutes = max(
                0,
                (self.expires_at - datetime.now(timezone.utc)).total_seconds() / 60,
            )

        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "phase": self.phase,
            "narrative": self.narrative,
            "waiting_for": self.waiting_for,
            "waiting_detail": self.waiting_detail,
            "limit_entry": self.limit_entry,
            "limit_ticket": self.limit_ticket,
            "expected_rr": self.expected_rr,
            "age_minutes": round(age_minutes, 1),
            "ttl_minutes": round(ttl_minutes, 1) if ttl_minutes else None,
            "check_count": self.check_count,
            "is_active": self.is_active(),
        }


# ── Intent Manager ───────────────────────────────────────────

class IntentManager:
    """
    Manages PendingIntents across all symbols.

    Rules:
    - Maximum 1 active intent per symbol.
    - Maximum ``max_total`` active intents across all symbols.
    - Intents auto-expire after ``max_age_hours``.
    - When a new intent replaces an old one for the same symbol,
      any associated limit order is cancelled.
    """

    def __init__(
        self,
        max_age_hours: float = 4.0,
        max_total: int = 3,
    ) -> None:
        self.max_age_hours = max_age_hours
        self.max_total = max_total
        self._intents: Dict[str, PendingIntent] = {}
        self._lock = threading.Lock()
        self._history: List[Dict[str, Any]] = []   # last N completed intents

    # ── CRUD ─────────────────────────────────────────────────

    def get(self, symbol: str) -> Optional[PendingIntent]:
        """Get the active intent for a symbol, or None."""
        with self._lock:
            intent = self._intents.get(symbol)
            if intent and not intent.is_active():
                self._archive(symbol)
                return None
            return intent

    def get_all_active(self) -> Dict[str, PendingIntent]:
        """Return all active intents."""
        with self._lock:
            active = {}
            expired_keys = []
            for sym, intent in self._intents.items():
                if intent.is_active():
                    active[sym] = intent
                else:
                    expired_keys.append(sym)
            for k in expired_keys:
                self._archive(k)
            return active

    def create(
        self,
        symbol: str,
        direction: str,
        htf_bias: str,
        waiting_for: str,
        waiting_detail: str = "",
        **kwargs,
    ) -> PendingIntent:
        """
        Create or replace the intent for a symbol.

        Returns the new PendingIntent.
        """
        with self._lock:
            # Update existing if same setup, else archive old
            if symbol in self._intents:
                old = self._intents[symbol]
                if old.direction == direction and old.htf_bias == htf_bias:
                    # Setup is the same, just update what we are waiting for
                    old.waiting_for = waiting_for
                    old.waiting_detail = waiting_detail
                    old.liquidity_target = kwargs.get("liquidity_target", old.liquidity_target)
                    old.liquidity_type = kwargs.get("liquidity_type", old.liquidity_type)
                    old.sweep_level = kwargs.get("sweep_level", old.sweep_level)
                    old.update_narrative()
                    return old
                else:
                    old.invalidate("Replaced by newer analysis")
                    self._archive(symbol)

            # Enforce global cap
            active_count = sum(
                1 for i in self._intents.values() if i.is_active()
            )
            if active_count >= self.max_total:
                # Evict oldest that doesn't have an active limit order in MT5
                candidates = [s for s, i in self._intents.items() if i.is_active() and i.limit_ticket is None]
                if candidates:
                    oldest_sym = min(
                        candidates,
                        key=lambda s: self._intents[s].created_at,
                    )
                    self._intents[oldest_sym].invalidate("Evicted for capacity")
                    self._archive(oldest_sym)
                else:
                    logger.warning(
                        f"Intent capacity full ({active_count}/{self.max_total}), "
                        f"but all active intents have placed limit orders. Cannot evict any intents."
                    )

            expires = datetime.now(timezone.utc) + timedelta(
                hours=self.max_age_hours
            )

            intent = PendingIntent(
                symbol=symbol,
                direction=direction,
                htf_bias=htf_bias,
                waiting_for=waiting_for,
                waiting_detail=waiting_detail,
                expires_at=expires,
                **kwargs,
            )
            intent.update_narrative()
            self._intents[symbol] = intent

            logger.info(
                f"[{symbol}] New intent: {direction} | "
                f"waiting for {waiting_for} | expires {expires:%H:%M}"
            )
            return intent

    def remove(self, symbol: str, reason: str = "Completed") -> None:
        """Remove (archive) intent for a symbol."""
        with self._lock:
            if symbol in self._intents:
                intent = self._intents[symbol]
                intent.invalidation_reason = reason
                if intent.limit_ticket is not None:
                    _cancel_mt5_limit_order(intent.limit_ticket)
                    intent.limit_ticket = None
                self._archive(symbol)

    def mark_triggered(self, symbol: str) -> None:
        """Mark intent as successfully triggered (trade placed)."""
        with self._lock:
            intent = self._intents.get(symbol)
            if intent:
                intent.phase = "triggered"
                self._archive(symbol)

    # ── Validation ───────────────────────────────────────────

    def validate_intent(
        self,
        symbol: str,
        current_bias: str,
    ) -> bool:
        """
        Check if intent is still structurally valid.

        Invalidation conditions:
        - HTF bias has reversed (bullish → bearish or vice versa)
        - Intent has expired
        - Associated limit order has been filled (mark triggered) or cancelled (invalidate) in MT5
        """
        intent = self.get(symbol)
        if intent is None:
            return False

        # Check MT5 pending order status if limit ticket exists
        if intent.limit_ticket is not None:
            try:
                mt5 = get_mt5()
                # Ensure mt5 is initialized
                if mt5.terminal_info() is not None or mt5.initialize():
                    # Check if the pending order still exists in MT5
                    pending_orders = mt5.orders_get(ticket=intent.limit_ticket)
                    if not pending_orders:
                        # Pending order is gone. Was it filled or cancelled?
                        # Check if we have an active position matching this ticket/symbol
                        positions = mt5.positions_get(symbol=intent.symbol)
                        position_found = False
                        if positions:
                            for pos in positions:
                                if pos.ticket == intent.limit_ticket or getattr(pos, "identifier", None) == intent.limit_ticket:
                                    position_found = True
                                    break
                        
                        if position_found:
                            logger.info(f"[{intent.symbol}] Pending limit order #{intent.limit_ticket} has been filled. Marking intent as triggered.")
                            self.mark_triggered(intent.symbol)
                            return False
                        else:
                            # It was cancelled/expired in MT5
                            logger.info(f"[{intent.symbol}] Pending limit order #{intent.limit_ticket} is no longer active and no position found. Invalidating intent.")
                            intent.invalidate("Limit order cancelled in MT5")
                            with self._lock:
                                self._archive(symbol)
                            return False
            except Exception as e:
                logger.error(f"Error validating MT5 limit order #{intent.limit_ticket}: {e}")

        # Bias reversal invalidation
        if current_bias != intent.htf_bias and current_bias != "ranging":
            intent.invalidate(
                f"HTF bias changed: {intent.htf_bias} → {current_bias}"
            )
            return False

        # Expiry
        if intent.is_expired():
            intent.invalidate("Expired (max age reached)")
            with self._lock:
                self._archive(symbol)
            return False

        intent.check_count += 1
        intent.last_checked = datetime.now(timezone.utc)
        return True

    # ── Dashboard ────────────────────────────────────────────

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get all intent data for dashboard display."""
        active = self.get_all_active()
        return {
            "active_intents": {
                sym: intent.to_dashboard_dict()
                for sym, intent in active.items()
            },
            "total_active": len(active),
            "recent_history": self._history[-10:],
        }

    # ── Persistence ──────────────────────────────────────────

    def export_state(self) -> List[Dict[str, Any]]:
        """Snapshot all active intents for saving to disk."""
        with self._lock:
            return [
                intent.to_state_dict()
                for intent in self._intents.values()
                if intent.is_active()
            ]

    def import_state(self, states: List[Dict[str, Any]]) -> int:
        """Restore intents from a snapshot. Skips expired ones. Returns count."""
        if not states:
            return 0
        restored = 0
        with self._lock:
            for state in states:
                try:
                    intent = PendingIntent.from_state_dict(state)
                except Exception as exc:
                    logger.warning(f"Skipping unrestorable intent: {exc}")
                    continue
                if not intent.is_active():
                    continue
                if len(self._intents) >= self.max_total:
                    break
                self._intents[intent.symbol] = intent
                restored += 1
        if restored:
            logger.info(f"Restored {restored} pending intent(s) from disk")
        return restored

    # ── Internal ─────────────────────────────────────────────

    def _archive(self, symbol: str) -> None:
        """Move intent to history (must be called under lock)."""
        intent = self._intents.pop(symbol, None)
        if intent:
            self._history.append({
                "symbol": intent.symbol,
                "direction": intent.direction,
                "phase": intent.phase,
                "narrative": intent.narrative,
                "created_at": intent.created_at.isoformat(),
                "reason": intent.invalidation_reason,
                "limit_ticket": intent.limit_ticket,
            })
            # Keep history bounded
            if len(self._history) > 50:
                self._history = self._history[-50:]
