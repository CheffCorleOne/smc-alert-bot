"""
High-impact news filter for the SMC trading bot.

Fetches economic calendar data and blocks trading around
high-impact events to avoid slippage and unpredictable moves.
"""

import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
from utils.logger import get_logger

logger = get_logger("news_filter")

@dataclass
class NewsEvent:
    """Represents a single economic calendar event."""
    title: str
    currency: str
    impact: str            # 'high', 'medium', 'low'
    datetime_utc: datetime
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None

# Currency mapping: symbol → currencies affected
SYMBOL_CURRENCIES = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "GBPJPY": ["GBP", "JPY"],
    "XAUUSD": ["XAU", "USD"],
    "NAS100": ["USD"],
    "SPX500": ["USD"],
}

class NewsFilter:
    """
    Filters trading around high-impact news events.
    Uses a class-level cache to avoid rate limiting across multiple instances.
    """

    CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    
    # Class-level cache shared across all instances/threads
    _cache_events: List[NewsEvent] = []
    _last_fetch_time: float = 0
    _global_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ensure_fresh()

    def _ensure_fresh(self) -> None:
        """Fetch news if cache is empty or older than 1 hour."""
        now = time.time()
        
        with NewsFilter._global_lock:
            # If cache is fresh (less than 1 hour old), do nothing
            if NewsFilter._cache_events and (now - NewsFilter._last_fetch_time) < 3600:
                return

            try:
                logger.info("Fetching fresh economic calendar...")
                resp = requests.get(self.CALENDAR_URL, timeout=10)
                resp.raise_for_status()
                raw = resp.json()

                events = []
                for item in raw:
                    try:
                        impact = item.get("impact", "").lower()
                        if impact not in ("high", "medium", "low"):
                            if "high" in str(item.get("impact", "")).lower():
                                impact = "high"
                            else:
                                continue

                        dt_str = item.get("date", "")
                        if not dt_str:
                            continue
                        
                        event_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        if event_dt.tzinfo is None:
                            event_dt = event_dt.replace(tzinfo=timezone.utc)

                        events.append(NewsEvent(
                            title=item.get("title", "Unknown"),
                            currency=item.get("country", "").upper(),
                            impact=impact,
                            datetime_utc=event_dt,
                            actual=item.get("actual"),
                            forecast=item.get("forecast"),
                            previous=item.get("previous"),
                        ))
                    except Exception:
                        continue

                NewsFilter._cache_events = events
                NewsFilter._last_fetch_time = now
                logger.info(f"Loaded {len(events)} news events for this week")

            except Exception as exc:
                logger.warning(f"Failed to fetch news calendar: {exc}")

    def get_upcoming_events(
        self,
        symbol: str,
        hours_ahead: int = 24,
        impact_filter: str = "high",
    ) -> List[NewsEvent]:
        self._ensure_fresh()
        
        impact_levels = {"high": 3, "medium": 2, "low": 1}
        min_level = impact_levels.get(impact_filter, 3)

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)

        # Resolve which currencies affect this symbol
        base_symbol = symbol.upper().replace(".RAW", "")
        if base_symbol.endswith("M"):
            base_symbol = base_symbol[:-1]
        currencies = set()
        for key, curs in SYMBOL_CURRENCIES.items():
            if key in base_symbol:
                currencies.update(curs)
                break
        if not currencies:
            currencies = {"USD"}

        filtered = []
        with NewsFilter._global_lock:
            for ev in NewsFilter._cache_events:
                ev_level = impact_levels.get(ev.impact, 0)
                if ev_level < min_level:
                    continue
                if ev.currency not in currencies:
                    continue
                if ev.datetime_utc < now or ev.datetime_utc > cutoff:
                    continue
                filtered.append(ev)

        return sorted(filtered, key=lambda e: e.datetime_utc)

    def is_news_time(
        self,
        symbol: str,
        minutes_before: int = 30,
        minutes_after: int = 30,
    ) -> Optional[NewsEvent]:
        self._ensure_fresh()
        now = datetime.now(timezone.utc)

        base_symbol = symbol.upper().replace(".RAW", "")
        if base_symbol.endswith("M"):
            base_symbol = base_symbol[:-1]
        currencies = set()
        for key, curs in SYMBOL_CURRENCIES.items():
            if key in base_symbol:
                currencies.update(curs)
                break
        if not currencies:
            currencies = {"USD"}

        with NewsFilter._global_lock:
            for ev in NewsFilter._cache_events:
                if ev.impact != "high":
                    continue
                if ev.currency not in currencies:
                    continue
                window_start = ev.datetime_utc - timedelta(minutes=minutes_before)
                window_end = ev.datetime_utc + timedelta(minutes=minutes_after)
                if window_start <= now <= window_end:
                    logger.warning(
                        f"NEWS BLOCK: {ev.title} ({ev.currency}) at "
                        f"{ev.datetime_utc.strftime('%H:%M')} UTC — blocking {symbol}"
                    )
                    return ev
        return None
