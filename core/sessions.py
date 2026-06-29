"""
Session Manager — DST-aware ICT killzone detection.

ICT killzones are defined in *New York local time* (America/New_York) and
therefore shift relative to UTC across daylight-saving changes. The previous
implementation hard-coded UTC windows, so for ~half the year every killzone was
off by an hour. This module computes membership in NY local time so the windows
are always correct regardless of DST.

Killzone windows (NY local, ICT canon):
  Asian KZ          20:00–00:00   (wraps midnight)
  London KZ         02:00–05:00
  London Silver Bul 03:00–04:00
  NY AM KZ          07:00–10:00
  NY Equities Open  09:30–11:00
  NY Silver Bullet  10:00–11:00
  London Close      10:00–12:00
  NY PM KZ          13:30–16:00
  NY lunch (block)  12:00–13:00
"""

from datetime import datetime, time as dt_time, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from utils.logger import get_logger
from utils.symbols import normalize_symbol

logger = get_logger("sessions")

NY_TZ = ZoneInfo("America/New_York")


# ── Killzone windows (New York local time) ───────────────────
SESSIONS: Dict[str, Dict[str, dt_time]] = {
    # Display sessions (NY local, approximate — used for labels only)
    "asian": {"start": dt_time(19, 0), "end": dt_time(0, 0)},      # wraps midnight
    "london": {"start": dt_time(2, 0), "end": dt_time(7, 0)},
    "ny": {"start": dt_time(7, 0), "end": dt_time(12, 0)},

    # Killzones (entry windows)
    "asian_killzone": {"start": dt_time(20, 0), "end": dt_time(0, 0)},  # wraps midnight
    "london_killzone": {"start": dt_time(2, 0), "end": dt_time(5, 0)},
    "ny_killzone": {"start": dt_time(7, 0), "end": dt_time(10, 0)},
    "ny_equities_open": {"start": dt_time(9, 30), "end": dt_time(11, 0)},
    "london_close": {"start": dt_time(10, 0), "end": dt_time(12, 0)},
    "ny_pm_killzone": {"start": dt_time(13, 30), "end": dt_time(16, 0)},

    # Special windows
    "ny_lunch": {"start": dt_time(12, 0), "end": dt_time(13, 0)},
    "silver_bullet_london": {"start": dt_time(3, 0), "end": dt_time(4, 0)},
    "silver_bullet_ny": {"start": dt_time(10, 0), "end": dt_time(11, 0)},
}

KEY_TIMES = {
    "midnight_open": dt_time(0, 0),     # 00:00 NY — daily anchor
    "asian_range_start": dt_time(19, 0),
    "asian_range_end": dt_time(0, 0),
    "london_open": dt_time(2, 0),
    "ny_open": dt_time(7, 0),
    "ny_equities_open": dt_time(9, 30),
}

# Which killzones each instrument should be traded in (canonical base names).
SYMBOL_KILLZONES = {
    "EURUSD": ["london_killzone", "ny_killzone", "london_close"],
    "GBPUSD": ["london_killzone", "ny_killzone", "london_close"],
    "GBPJPY": ["london_killzone", "ny_killzone"],
    "USDJPY": ["asian_killzone", "london_killzone", "ny_killzone"],
    "XAUUSD": ["asian_killzone", "london_killzone", "ny_killzone", "ny_pm_killzone"],
    "NAS100": ["london_killzone", "ny_equities_open", "ny_killzone", "ny_pm_killzone"],
}

_KILLZONE_NAMES = {
    "asian_killzone", "london_killzone", "ny_killzone", "ny_equities_open",
    "london_close", "ny_pm_killzone",
}


def killzone_window_utc(name: str, on_date: Optional[datetime] = None) -> Optional[Dict[str, dt_time]]:
    """
    Return today's UTC start/end times for a killzone, accounting for DST.

    Useful for display/sorting where a UTC time is expected. Returns None for
    unknown names. For midnight-wrapping windows the times are still returned as
    plain ``time`` objects (caller handles the wrap).
    """
    win = SESSIONS.get(name)
    if win is None:
        return None
    base = (on_date or datetime.now(NY_TZ)).date()

    def to_utc(t: dt_time) -> dt_time:
        ny_dt = datetime.combine(base, t, tzinfo=NY_TZ)
        return ny_dt.astimezone(timezone.utc).time()

    return {"start": to_utc(win["start"]), "end": to_utc(win["end"])}


class SessionManager:
    """DST-aware trading session and killzone awareness (New York local)."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def _now_ny() -> datetime:
        """Current time in New York (DST-aware)."""
        return datetime.now(NY_TZ)

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _time_in_range(start: dt_time, end: dt_time, current: dt_time) -> bool:
        """True if current is within [start, end). Handles midnight wrap."""
        if start <= end:
            return start <= current < end
        return current >= start or current < end

    def get_current_session(self) -> str:
        """Name of the current trading session (NY-local labels)."""
        now = self._now_ny().time()

        if self._time_in_range(SESSIONS["ny_lunch"]["start"], SESSIONS["ny_lunch"]["end"], now):
            return "ny_lunch"
        if self._time_in_range(SESSIONS["ny"]["start"], SESSIONS["ny"]["end"], now):
            return "ny"
        if self._time_in_range(SESSIONS["london"]["start"], SESSIONS["london"]["end"], now):
            return "london"
        if self._time_in_range(SESSIONS["asian"]["start"], SESSIONS["asian"]["end"], now):
            return "asian"
        return "inter_session"

    def is_in_killzone(self, symbol: Optional[str] = None) -> bool:
        """Whether we are in any (symbol-relevant) killzone right now."""
        now = self._now_ny().time()
        target_kzs = (
            self.get_session_for_instrument(symbol) if symbol else _KILLZONE_NAMES
        )
        for kz in target_kzs:
            if kz in SESSIONS and self._time_in_range(
                SESSIONS[kz]["start"], SESSIONS[kz]["end"], now
            ):
                return True
        return False

    def get_active_killzone(self, symbol: Optional[str] = None) -> Optional[str]:
        """Name of the currently active killzone (priority-ordered)."""
        now = self._now_ny().time()
        check_order = [
            "silver_bullet_ny", "silver_bullet_london",
            "ny_equities_open", "ny_killzone", "london_killzone",
            "asian_killzone", "london_close", "ny_pm_killzone",
        ]
        if symbol:
            relevant = (
                self.get_session_for_instrument(symbol)
                + self.get_silver_bullet_for_instrument(symbol)
            )
        else:
            relevant = check_order

        for kz in check_order:
            if kz in relevant and self._time_in_range(
                SESSIONS[kz]["start"], SESSIONS[kz]["end"], now
            ):
                return kz
        return None

    def get_silver_bullet_for_instrument(self, symbol: str) -> List[str]:
        """Silver Bullet windows that map to the symbol's normal sessions."""
        allowed = self.get_session_for_instrument(symbol)
        windows = []
        if "london_killzone" in allowed:
            windows.append("silver_bullet_london")
        if "ny_killzone" in allowed or "ny_equities_open" in allowed:
            windows.append("silver_bullet_ny")
        return windows

    def is_in_silver_bullet(self, symbol: Optional[str] = None) -> bool:
        """Whether we are inside a Silver Bullet window right now."""
        now = self._now_ny().time()
        windows = (
            self.get_silver_bullet_for_instrument(symbol)
            if symbol else ["silver_bullet_london", "silver_bullet_ny"]
        )
        return any(
            self._time_in_range(SESSIONS[kz]["start"], SESSIONS[kz]["end"], now)
            for kz in windows
        )

    def is_asian_session(self) -> bool:
        now = self._now_ny().time()
        return self._time_in_range(
            SESSIONS["asian"]["start"], SESSIONS["asian"]["end"], now
        )

    def get_session_for_instrument(self, symbol: str) -> List[str]:
        """Tradeable killzones for a specific symbol (canonical-name aware)."""
        if not symbol:
            return []
        base = normalize_symbol(symbol)
        if base in SYMBOL_KILLZONES:
            return SYMBOL_KILLZONES[base]
        # Partial fallback for index variants etc.
        for key, kzs in SYMBOL_KILLZONES.items():
            if key in base:
                return kzs
        return ["london_killzone", "ny_killzone"]

    def is_weekend(self) -> bool:
        """Forex market closed Fri 17:00 NY → Sun 17:00 NY."""
        now = self._now_ny()
        weekday = now.weekday()
        if weekday == 5:  # Saturday
            return True
        if weekday == 6:  # Sunday
            return now.hour < 17
        if weekday == 4:  # Friday evening
            return now.hour >= 17
        return False

    def should_trade_now(
        self,
        symbol: str,
        trade_london_kz: bool = True,
        trade_ny_kz: bool = True,
        trade_asian_kz: bool = True,
        trade_london_close: bool = True,
    ) -> bool:
        """Whether the bot should actively look for entries right now."""
        if self.is_weekend():
            return False

        now = self._now_ny().time()
        if self._time_in_range(SESSIONS["ny_lunch"]["start"], SESSIONS["ny_lunch"]["end"], now):
            return False

        for kz_name in self.get_session_for_instrument(symbol):
            if kz_name == "london_killzone" and not trade_london_kz:
                continue
            if kz_name in ("ny_killzone", "ny_equities_open", "ny_pm_killzone") and not trade_ny_kz:
                continue
            if kz_name == "asian_killzone" and not trade_asian_kz:
                continue
            if kz_name == "london_close" and not trade_london_close:
                continue
            if kz_name in SESSIONS and self._time_in_range(
                SESSIONS[kz_name]["start"], SESSIONS[kz_name]["end"], now
            ):
                return True
        return False

    def get_session_display(self) -> Dict:
        """Session info for dashboard display."""
        return {
            "current_session": self.get_current_session(),
            "is_killzone": self.is_in_killzone(),
            "active_killzone": self.get_active_killzone(),
            "is_weekend": self.is_weekend(),
            "utc_time": self._now_utc().strftime("%H:%M:%S UTC"),
            "ny_time": self._now_ny().strftime("%H:%M:%S %Z"),
        }
