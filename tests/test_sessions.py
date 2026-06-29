"""
Tests for DST-aware ICT killzones.

The key regression these guard against: killzone windows are defined in New York
local time, so their UTC projection must shift by an hour across US daylight
saving. The old hard-coded-UTC implementation got this wrong half the year.
"""

from __future__ import annotations

from datetime import datetime, time as dt_time

from core.sessions import SessionManager, killzone_window_utc


class TestDST:
    def test_london_kz_winter_is_07utc(self):
        # Jan: NY is UTC-5, so 02:00 NY == 07:00 UTC.
        win = killzone_window_utc("london_killzone", on_date=datetime(2024, 1, 15))
        assert win["start"] == dt_time(7, 0)
        assert win["end"] == dt_time(10, 0)

    def test_london_kz_summer_is_06utc(self):
        # Jul: NY is UTC-4, so 02:00 NY == 06:00 UTC.
        win = killzone_window_utc("london_killzone", on_date=datetime(2024, 7, 15))
        assert win["start"] == dt_time(6, 0)
        assert win["end"] == dt_time(9, 0)

    def test_ny_kz_shifts_with_dst(self):
        winter = killzone_window_utc("ny_killzone", on_date=datetime(2024, 1, 15))
        summer = killzone_window_utc("ny_killzone", on_date=datetime(2024, 7, 15))
        assert winter["start"] == dt_time(12, 0)   # 07:00 NY winter
        assert summer["start"] == dt_time(11, 0)   # 07:00 NY summer

    def test_unknown_window(self):
        assert killzone_window_utc("does_not_exist") is None


class TestTimeInRange:
    def test_normal_range(self):
        assert SessionManager._time_in_range(dt_time(2), dt_time(5), dt_time(3))
        assert not SessionManager._time_in_range(dt_time(2), dt_time(5), dt_time(6))

    def test_midnight_wrap(self):
        # Asian KZ 20:00 -> 00:00 wraps midnight.
        assert SessionManager._time_in_range(dt_time(20), dt_time(0), dt_time(22))
        assert not SessionManager._time_in_range(dt_time(20), dt_time(0), dt_time(12))


class TestSymbolKillzones:
    def test_canonical_name_resolution(self):
        mgr = SessionManager()
        # Broker variants normalise to canonical killzone sets.
        assert mgr.get_session_for_instrument("NQ") == mgr.get_session_for_instrument("NAS100")
        assert mgr.get_session_for_instrument("EURUSDm") == mgr.get_session_for_instrument("EURUSD")

    def test_default_for_unknown(self):
        mgr = SessionManager()
        assert mgr.get_session_for_instrument("ZZZ999") == ["london_killzone", "ny_killzone"]
