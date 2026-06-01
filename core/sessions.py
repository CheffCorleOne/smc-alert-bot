"""
Session Manager — Trading session and killzone detection.

Identifies the current trading session (Asian, London, NY) and
determines whether conditions are suitable for trade entry.
"""

from datetime import datetime, timezone, time as dt_time
from typing import List, Optional, Dict

from utils.logger import get_logger

logger = get_logger("sessions")


# ── Session Definitions (UTC) ────────────────────────────────
# Full sessions (Petran) — used for context & display
# Killzones (SMC) — used for trade entry decisions

SESSIONS: Dict[str, Dict] = {
    # Full sessions (Petran's timings — UTC+0)
    "asian": {"start": dt_time(20, 0), "end": dt_time(6, 0)},       # Wraps midnight
    "frankfurt": {"start": dt_time(6, 0), "end": dt_time(7, 0)},     # Pre-London impulse
    "london": {"start": dt_time(7, 0), "end": dt_time(12, 0)},
    "ny": {"start": dt_time(12, 0), "end": dt_time(19, 0)},

    # Killzones (SMC Specific — UTC)
    "asian_killzone": {"start": dt_time(0, 0), "end": dt_time(4, 0)},
    "london_killzone": {"start": dt_time(7, 0), "end": dt_time(10, 0)},
    "ny_killzone": {"start": dt_time(12, 0), "end": dt_time(15, 0)},    # NY AM
    "london_close": {"start": dt_time(15, 0), "end": dt_time(17, 0)},
    "ny_pm_killzone": {"start": dt_time(19, 0), "end": dt_time(21, 0)},
    "ny_equities_open": {"start": dt_time(13, 30), "end": dt_time(15, 0)}, # 08:30-10:00 EST

    # Special windows
    "ny_lunch": {"start": dt_time(17, 0), "end": dt_time(19, 0)},    # Adjusted based on common SMC logic (12:00-14:00 EST)
    "silver_bullet_london": {"start": dt_time(8, 0), "end": dt_time(9, 0)},
    "silver_bullet_ny": {"start": dt_time(14, 0), "end": dt_time(15, 0)},
}

KEY_TIMES = {
    "midnight_open": dt_time(5, 0),    # 00:00 EST = 05:00 UTC — daily anchor
    "asian_range_start": dt_time(0, 0), # ICT Asian Range start (for liquidity calc)
    "asian_range_end": dt_time(5, 0),   # ICT Asian Range end (for liquidity calc)
    "frankfurt_open": dt_time(6, 0),
    "london_open": dt_time(7, 0),
    "ny_open": dt_time(13, 30),
    "ny_equities_open": dt_time(13, 30), # 09:30 EST
}

# Mapping symbols to their specific tradeable killzones
SYMBOL_KILLZONES = {
    "EURUSD": ["london_killzone", "ny_killzone", "london_close"],
    "GBPUSD": ["london_killzone", "ny_killzone", "london_close"],
    "GBPJPY": ["london_killzone", "ny_killzone"],
    "USDJPY": ["asian_killzone", "london_killzone", "ny_killzone"],
    "XAUUSD": ["asian_killzone", "london_killzone", "ny_killzone", "ny_pm_killzone"],
    "NAS100": ["london_killzone", "ny_equities_open", "ny_killzone", "ny_pm_killzone"],
    "NQ": ["london_killzone", "ny_equities_open", "ny_killzone", "ny_pm_killzone"], # Alias for Libertex
}


class SessionManager:
    """
    Manages trading session awareness.

    Determines the current market session, whether the bot is in
    a killzone (optimal entry window), and whether trading should
    be active for a given instrument.
    """

    def __init__(self) -> None:
        pass

    @staticmethod
    def _now_utc() -> datetime:
        """Get current UTC time."""
        return datetime.now(timezone.utc)

    @staticmethod
    def _time_in_range(
        start: dt_time, end: dt_time, current: dt_time
    ) -> bool:
        """Check if current time is within [start, end) range."""
        if start <= end:
            return start <= current < end
        else:
            # Handles wrap-around midnight
            return current >= start or current < end

    def get_current_session(self) -> str:
        """
        Get the name of the current trading session.

        Uses Petran's session layout:
        - Asian: 20:00–06:00 UTC (wraps midnight)
        - Frankfurt: 06:00–07:00 UTC
        - London: 07:00–12:00 UTC
        - NY: 12:00–19:00 UTC

        Returns:
            Session name string.
        """
        now = self._now_utc().time()

        # Check in priority order (specific → general)
        # NY Lunch (low volume — avoid trading)
        if self._time_in_range(SESSIONS["ny_lunch"]["start"],
                               SESSIONS["ny_lunch"]["end"], now):
            return "ny_lunch"

        # NY session (12:00–19:00)
        if self._time_in_range(SESSIONS["ny"]["start"],
                               SESSIONS["ny"]["end"], now):
            return "ny"

        # London session (07:00–12:00)
        if self._time_in_range(SESSIONS["london"]["start"],
                               SESSIONS["london"]["end"], now):
            return "london"

        # Frankfurt (06:00–07:00)
        if self._time_in_range(SESSIONS["frankfurt"]["start"],
                               SESSIONS["frankfurt"]["end"], now):
            return "frankfurt"

        # Asian session (20:00–06:00, wraps midnight)
        if self._time_in_range(SESSIONS["asian"]["start"],
                               SESSIONS["asian"]["end"], now):
            return "asian"

        # After NY close (19:00–20:00)
        if self._time_in_range(dt_time(19, 0), dt_time(20, 0), now):
            return "after_hours"

        return "inter_session"

    def is_in_killzone(self, symbol: Optional[str] = None) -> bool:
        """
        Check if currently in any killzone.
        If symbol is provided, only checks killzones relevant to that symbol.
        """
        now = self._now_utc().time()
        
        target_kzs = SESSIONS.keys()
        if symbol:
            target_kzs = self.get_session_for_instrument(symbol)
            
        for kz in target_kzs:
            if kz in SESSIONS and "killzone" in kz or kz in ["london_close", "ny_equities_open"]:
                if self._time_in_range(SESSIONS[kz]["start"], SESSIONS[kz]["end"], now):
                    return True
        return False

    def get_active_killzone(self, symbol: Optional[str] = None) -> Optional[str]:
        """
        Get the name of the currently active killzone.
        """
        now = self._now_utc().time()
        
        # Priority order for detection
        check_order = [
            "silver_bullet_ny", "silver_bullet_london",
            "ny_equities_open", "ny_killzone", "london_killzone", 
            "asian_killzone", "london_close", "ny_pm_killzone"
        ]
        
        if symbol:
            relevant_kzs = (
                self.get_session_for_instrument(symbol)
                + self.get_silver_bullet_for_instrument(symbol)
            )
        else:
            relevant_kzs = check_order

        for kz in check_order:
            if kz in relevant_kzs and self._time_in_range(SESSIONS[kz]["start"], SESSIONS[kz]["end"], now):
                return kz

        return None

    def get_silver_bullet_for_instrument(self, symbol: str) -> List[str]:
        """Return Silver Bullet windows that map to the symbol's normal sessions."""
        allowed_kzs = self.get_session_for_instrument(symbol)
        windows = []
        if "london_killzone" in allowed_kzs:
            windows.append("silver_bullet_london")
        if "ny_killzone" in allowed_kzs or "ny_equities_open" in allowed_kzs:
            windows.append("silver_bullet_ny")
        return windows

    def is_in_silver_bullet(self, symbol: Optional[str] = None) -> bool:
        """Check if currently in a Silver Bullet time window."""
        now = self._now_utc().time()
        windows = (
            self.get_silver_bullet_for_instrument(symbol)
            if symbol else ["silver_bullet_london", "silver_bullet_ny"]
        )
        for kz in windows:
            if self._time_in_range(SESSIONS[kz]["start"], SESSIONS[kz]["end"], now):
                return True
        return False

    def is_asian_session(self) -> bool:
        """Check if currently in the Asian session."""
        now = self._now_utc().time()
        return self._time_in_range(
            SESSIONS["asian"]["start"],
            SESSIONS["asian"]["end"],
            now,
        )

    def get_session_for_instrument(self, symbol: str) -> List[str]:
        """
        Get the list of tradeable killzones for a specific symbol.
        """
        if not symbol:
            return []
            
        base = symbol.upper().replace(".RAW", "")
        if base.endswith("M"):
            base = base[:-1]
        
        # Direct match or partial match in SYMBOL_KILLZONES
        for key, kzs in SYMBOL_KILLZONES.items():
            if key == base or key in base:
                return kzs

        return ["london_killzone", "ny_killzone"]

    def is_weekend(self) -> bool:
        """Check if it's currently the weekend (no trading)."""
        now = self._now_utc()
        # Forex market: closed from Friday 22:00 UTC to Sunday 22:00 UTC
        weekday = now.weekday()

        if weekday == 5:  # Saturday
            return True
        if weekday == 6:  # Sunday
            if now.hour < 22:
                return True
        if weekday == 4 and now.hour >= 22:  # Friday evening
            return True

        return False

    def should_trade_now(
        self,
        symbol: str,
        trade_london_kz: bool = True,
        trade_ny_kz: bool = True,
        trade_asian_kz: bool = True,
        trade_london_close: bool = True,
    ) -> bool:
        """
        Determine if the bot should be actively looking for trades.
        """
        if self.is_weekend():
            return False

        now = self._now_utc().time()

        # Block NY lunch
        if self._time_in_range(SESSIONS["ny_lunch"]["start"], SESSIONS["ny_lunch"]["end"], now):
            return False

        # Get tradeable windows for this symbol
        allowed_kzs = self.get_session_for_instrument(symbol)
        
        for kz_name in allowed_kzs:
            # Check global toggles for main killzones
            if kz_name == "london_killzone" and not trade_london_kz:
                continue
            if kz_name in ("ny_killzone", "ny_equities_open", "ny_pm_killzone") and not trade_ny_kz:
                continue
            if kz_name == "asian_killzone" and not trade_asian_kz:
                continue
            if kz_name == "london_close" and not trade_london_close:
                continue
                
            if kz_name in SESSIONS:
                if self._time_in_range(SESSIONS[kz_name]["start"], SESSIONS[kz_name]["end"], now):
                    return True

        return False

    def get_session_display(self) -> Dict:
        """
        Get session info for dashboard display.

        Returns:
            Dict with current session name, killzone status,
            and formatted times.
        """
        return {
            "current_session": self.get_current_session(),
            "is_killzone": self.is_in_killzone(),
            "active_killzone": self.get_active_killzone(),
            "is_weekend": self.is_weekend(),
            "utc_time": self._now_utc().strftime("%H:%M:%S UTC"),
        }
