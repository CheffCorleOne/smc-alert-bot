"""
OHLCV data fetcher from MetaTrader 5.

Provides cached multi-timeframe market data for the SMC analysis engine.
"""

import time
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger("fetcher")

# Lazy import MT5 — may not be available on all platforms
_mt5 = None


def _get_mt5():
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5 as mt5
            _mt5 = mt5
        except ImportError:
            logger.error("MetaTrader5 package not installed")
            raise
    return _mt5


# Timeframe name → MT5 constant mapping
TF_MAP = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
}

# Cache TTL per timeframe in seconds
CACHE_TTL = {
    "M1": 15,
    "M5": 30,
    "M15": 60,
    "M30": 120,
    "H1": 120,
    "H4": 600,
    "D1": 1800,
    "W1": 3600,
}

# Multi-TF analysis uses these frames
MULTI_TF_FRAMES = ["W1", "D1", "H4", "H1", "M15", "M5", "M1"]


class MarketDataFetcher:
    """
    Fetches OHLCV data from MetaTrader 5 with time-based caching.

    Each (symbol, timeframe) pair is cached independently and
    only re-fetched after its TTL expires.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Dict] = {}  # key → {data, timestamp}
        self._lock = threading.Lock()

    def _cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"

    def _is_cache_fresh(self, symbol: str, timeframe: str) -> bool:
        key = self._cache_key(symbol, timeframe)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            ttl = CACHE_TTL.get(timeframe, 60)
            return (time.time() - entry["timestamp"]) < ttl

    def get_ohlcv(
        self, symbol: str, timeframe: str, bars: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data for a symbol and timeframe.

        Args:
            symbol: MT5 symbol name (e.g. 'EURUSD', 'EURUSDm').
            timeframe: One of M1, M5, M15, M30, H1, H4, D1, W1.
            bars: Number of bars to fetch.

        Returns:
            DataFrame with columns: time, open, high, low, close, volume.
            None if fetch fails.
        """
        # Return cached data if fresh
        if self._is_cache_fresh(symbol, timeframe):
            key = self._cache_key(symbol, timeframe)
            with self._lock:
                return self._cache[key]["data"].copy()

        # Fetch from MT5
        try:
            mt5 = _get_mt5()
            tf_const = getattr(mt5, TF_MAP.get(timeframe, "TIMEFRAME_M15"))
            rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, bars)

            if rates is None or len(rates) == 0:
                err = mt5.last_error()
                logger.warning(
                    f"No data for {symbol} {timeframe}: {err}"
                )
                return None

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df = df[["time", "open", "high", "low", "close",
                      "tick_volume"]].copy()
            df.rename(columns={"tick_volume": "volume"}, inplace=True)
            df.reset_index(drop=True, inplace=True)

            # Cache it
            key = self._cache_key(symbol, timeframe)
            with self._lock:
                self._cache[key] = {
                    "data": df.copy(),
                    "timestamp": time.time(),
                }

            logger.debug(
                f"Fetched {len(df)} bars for {symbol} {timeframe}"
            )
            return df

        except Exception as exc:
            logger.error(
                f"Error fetching {symbol} {timeframe}: {exc}"
            )
            return None

    def get_multi_timeframe(
        self, symbol: str
    ) -> Dict[str, Optional[pd.DataFrame]]:
        """
        Fetch data for all analysis timeframes.

        Args:
            symbol: MT5 symbol name.

        Returns:
            Dict with keys W1, D1, H4, H1, M15, M5 → DataFrames.
        """
        result = {}
        for tf in MULTI_TF_FRAMES:
            result[tf] = self.get_ohlcv(symbol, tf)
        return result

    def clear_cache(self, symbol: Optional[str] = None) -> None:
        """Clear cached data, optionally for a specific symbol."""
        with self._lock:
            if symbol is None:
                self._cache.clear()
            else:
                keys_to_remove = [
                    k for k in self._cache if k.startswith(symbol)
                ]
                for k in keys_to_remove:
                    del self._cache[k]
