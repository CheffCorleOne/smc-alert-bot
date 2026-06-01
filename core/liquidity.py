"""
Liquidity Engine — EQH, EQL, BSL, SSL, Sweeps, Asian Range.

Detects where retail stop-losses cluster (equal highs/lows)
and identifies liquidity sweeps by institutional players.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, time as dt_time
from typing import List, Optional, Dict

import pandas as pd
import numpy as np

from config import get_pip_size
from utils.logger import get_logger

logger = get_logger("liquidity")


# ── Data Structures ──────────────────────────────────────────

@dataclass
class EqualLevel:
    """Equal Highs or Equal Lows cluster — retail liquidity zone."""
    type: str               # 'eqh' or 'eql'
    price: float            # Average price of the cluster
    count: int              # Number of touches
    indices: list           # Bar indices of the swing points
    timestamps: list        # Timestamps of each touch
    is_swept: bool = False  # Whether the level has been swept


@dataclass
class SweepEvent:
    """Liquidity sweep — smart money collecting retail stops."""
    index: int              # Bar index where sweep occurred
    level: float            # The liquidity level that was swept
    direction: str          # 'above' (swept highs) or 'below' (swept lows)
    wick_high: float        # High of the sweep candle
    wick_low: float         # Low of the sweep candle
    close: float            # Close of the sweep candle
    timestamp: datetime
    penetration: float      # How far price went beyond the level


class LiquidityEngine:
    """
    Detects liquidity pools and sweep events.

    Liquidity sits at:
    - Equal highs (EQH): retail buy stops clustered above
    - Equal lows (EQL): retail sell stops clustered below
    - Previous swing highs: buy-side liquidity (BSL)
    - Previous swing lows: sell-side liquidity (SSL)

    Smart money sweeps these levels to fill orders before reversing.
    """

    def __init__(self, symbol: str = "EURUSD") -> None:
        self.symbol = symbol
        self.pip_size = get_pip_size(symbol)

    def find_eqh(
        self,
        df: pd.DataFrame,
        swing_highs: list,
        tolerance_pips: int = 3,
    ) -> List[EqualLevel]:
        """
        Find Equal Highs — 2+ swing highs within pip tolerance.

        These represent retail stop-loss clusters above the market
        that smart money targets for sweeps.

        Args:
            df: OHLCV DataFrame.
            swing_highs: List of SwingPoint (type='high').
            tolerance_pips: How close highs must be to count as equal.

        Returns:
            List of EqualLevel with type='eqh'.
        """
        if len(swing_highs) < 2:
            return []

        tolerance = tolerance_pips * self.pip_size
        clusters = []
        used = set()

        for i, sh1 in enumerate(swing_highs):
            if i in used:
                continue
            cluster_indices = [sh1.index]
            cluster_prices = [sh1.price]
            cluster_times = [sh1.timestamp]

            for j, sh2 in enumerate(swing_highs):
                if j <= i or j in used:
                    continue
                if abs(sh1.price - sh2.price) <= tolerance:
                    cluster_indices.append(sh2.index)
                    cluster_prices.append(sh2.price)
                    cluster_times.append(sh2.timestamp)
                    used.add(j)

            if len(cluster_indices) >= 2:
                used.add(i)
                avg_price = sum(cluster_prices) / len(cluster_prices)

                # Check if level has been swept
                is_swept = False
                last_idx = max(cluster_indices)
                if last_idx < len(df) - 1:
                    future_highs = df["high"].values[last_idx + 1:]
                    future_closes = df["close"].values[last_idx + 1:]
                    for k in range(len(future_highs)):
                        if future_highs[k] > avg_price and future_closes[k] < avg_price:
                            is_swept = True
                            break

                clusters.append(EqualLevel(
                    type="eqh",
                    price=round(avg_price, 5),
                    count=len(cluster_indices),
                    indices=cluster_indices,
                    timestamps=cluster_times,
                    is_swept=is_swept,
                ))

        logger.debug(f"Found {len(clusters)} EQH clusters for {self.symbol}")
        return clusters

    def find_eql(
        self,
        df: pd.DataFrame,
        swing_lows: list,
        tolerance_pips: int = 3,
    ) -> List[EqualLevel]:
        """
        Find Equal Lows — 2+ swing lows within pip tolerance.

        These represent retail stop-loss clusters below the market.

        Args:
            df: OHLCV DataFrame.
            swing_lows: List of SwingPoint (type='low').
            tolerance_pips: How close lows must be to count as equal.

        Returns:
            List of EqualLevel with type='eql'.
        """
        if len(swing_lows) < 2:
            return []

        tolerance = tolerance_pips * self.pip_size
        clusters = []
        used = set()

        for i, sl1 in enumerate(swing_lows):
            if i in used:
                continue
            cluster_indices = [sl1.index]
            cluster_prices = [sl1.price]
            cluster_times = [sl1.timestamp]

            for j, sl2 in enumerate(swing_lows):
                if j <= i or j in used:
                    continue
                if abs(sl1.price - sl2.price) <= tolerance:
                    cluster_indices.append(sl2.index)
                    cluster_prices.append(sl2.price)
                    cluster_times.append(sl2.timestamp)
                    used.add(j)

            if len(cluster_indices) >= 2:
                used.add(i)
                avg_price = sum(cluster_prices) / len(cluster_prices)

                is_swept = False
                last_idx = max(cluster_indices)
                if last_idx < len(df) - 1:
                    future_lows = df["low"].values[last_idx + 1:]
                    future_closes = df["close"].values[last_idx + 1:]
                    for k in range(len(future_lows)):
                        if future_lows[k] < avg_price and future_closes[k] > avg_price:
                            is_swept = True
                            break

                clusters.append(EqualLevel(
                    type="eql",
                    price=round(avg_price, 5),
                    count=len(cluster_indices),
                    indices=cluster_indices,
                    timestamps=cluster_times,
                    is_swept=is_swept,
                ))

        logger.debug(f"Found {len(clusters)} EQL clusters for {self.symbol}")
        return clusters

    def find_bsl(
        self, df: pd.DataFrame, swing_highs: list
    ) -> List[float]:
        """
        Find Buy-Side Liquidity levels — swing highs above current price.

        Args:
            df: OHLCV DataFrame.
            swing_highs: List of SwingPoint.

        Returns:
            List of price levels sorted ascending (nearest first).
        """
        current_price = df["close"].iloc[-1]
        levels = sorted(
            set(sh.price for sh in swing_highs if sh.price > current_price)
        )
        return levels

    def find_ssl(
        self, df: pd.DataFrame, swing_lows: list
    ) -> List[float]:
        """
        Find Sell-Side Liquidity levels — swing lows below current price.

        Args:
            df: OHLCV DataFrame.
            swing_lows: List of SwingPoint.

        Returns:
            List of price levels sorted descending (nearest first).
        """
        current_price = df["close"].iloc[-1]
        levels = sorted(
            set(sl.price for sl in swing_lows if sl.price < current_price),
            reverse=True,
        )
        return levels

    def detect_sweep(
        self,
        df: pd.DataFrame,
        level: float,
        direction: str,
        lookback: int = 10,
        require_displacement: bool = True,
        displacement_atr_multiple: float = 1.5,
        displacement_window: int = 3,
    ) -> Optional[SweepEvent]:
        """
        Detect if a liquidity level was swept.

        A sweep occurs when price momentarily breaks a level (via wick)
        but closes back inside — indicating smart money grabbed liquidity
        and will reverse.

        Args:
            df: OHLCV DataFrame.
            level: The price level to check.
            direction: 'above' (check for sweep above) or 'below'.
            lookback: Number of recent bars to check.

        Returns:
            SweepEvent if detected, None otherwise.
        """
        if level is None:
            return None

        start = max(0, len(df) - lookback)
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        times = df["time"].values
        atr = self._calculate_atr(df)

        def has_displacement(sweep_index: int) -> bool:
            if not require_displacement or atr <= 0:
                return True

            end = min(len(df), sweep_index + displacement_window + 1)
            for j in range(sweep_index + 1, end):
                candle_range = highs[j] - lows[j]
                if candle_range < displacement_atr_multiple * atr:
                    continue
                if direction == "above" and closes[j] < level:
                    return True
                if direction == "below" and closes[j] > level:
                    return True
            return False

        for i in range(start, len(df)):
            if direction == "above":
                # Standard single-candle sweep: Wick went above level, but close came back below
                if highs[i] > level and closes[i] < level and has_displacement(i):
                    penetration = highs[i] - level
                    return SweepEvent(
                        index=i,
                        level=level,
                        direction="above",
                        wick_high=highs[i],
                        wick_low=lows[i],
                        close=closes[i],
                        timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                        penetration=round(penetration, 5),
                    )
                # Two-candle sweep: previous candle broke level, current candle closed below it
                elif (
                    i > 0
                    and highs[i - 1] > level
                    and closes[i] < level
                    and highs[i] <= highs[i - 1]
                    and has_displacement(i)
                ):
                    penetration = highs[i-1] - level
                    return SweepEvent(
                        index=i,
                        level=level,
                        direction="above",
                        wick_high=highs[i-1],
                        wick_low=lows[i-1],
                        close=closes[i],
                        timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                        penetration=round(penetration, 5),
                    )
            elif direction == "below":
                # Standard single-candle sweep
                if lows[i] < level and closes[i] > level and has_displacement(i):
                    penetration = level - lows[i]
                    return SweepEvent(
                        index=i,
                        level=level,
                        direction="below",
                        wick_high=highs[i],
                        wick_low=lows[i],
                        close=closes[i],
                        timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                        penetration=round(penetration, 5),
                    )
                # Two-candle sweep
                elif (
                    i > 0
                    and lows[i - 1] < level
                    and closes[i] > level
                    and lows[i] >= lows[i - 1]
                    and has_displacement(i)
                ):
                    penetration = level - lows[i-1]
                    return SweepEvent(
                        index=i,
                        level=level,
                        direction="below",
                        wick_high=highs[i-1],
                        wick_low=lows[i-1],
                        close=closes[i],
                        timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                        penetration=round(penetration, 5),
                    )

        return None

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        if df is None or len(df) < period + 1:
            return 0.0
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0

    def is_liquidity_swept(
        self, df: pd.DataFrame, level: float, lookback: int = 5
    ) -> bool:
        """
        Quick check if a level was swept in the last N candles.

        Args:
            df: OHLCV DataFrame.
            level: Price level.
            lookback: Number of candles to check.

        Returns:
            True if level was swept.
        """
        sweep_above = self.detect_sweep(df, level, "above", lookback)
        sweep_below = self.detect_sweep(df, level, "below", lookback)
        return (sweep_above is not None) or (sweep_below is not None)

    @staticmethod
    def get_asian_range(df_h1: pd.DataFrame) -> Optional[Dict]:
        """
        Calculate the Asian session range using ICT methodology.

        ICT defines the Asian Range as 19:00–00:00 EST = 00:00–05:00 UTC.
        The Midnight Open (00:00 EST = 05:00 UTC) is the daily price anchor.

        Also detects whether each level has been swept post-accumulation
        (i.e., during London or NY sessions).

        Args:
            df_h1: H1 OHLCV DataFrame with UTC timestamps.

        Returns:
            Dict with high, low, mid, midnight_open, sweep status, or None.
        """
        if df_h1 is None or df_h1.empty:
            return None

        try:
            df = df_h1.copy()
            df["hour"] = df["time"].dt.hour
            df["date"] = df["time"].dt.date

            latest_date = df["date"].iloc[-1]

            # ICT Asian Range: 00:00–05:00 UTC (19:00–00:00 EST)
            asian = df[
                (df["date"] == latest_date) &
                (df["hour"] >= 0) &
                (df["hour"] < 5)
            ]

            # Fallback to previous day if today's range hasn't formed
            if asian.empty or len(asian) < 2:
                prev_dates = df["date"].unique()
                if len(prev_dates) >= 2:
                    prev = prev_dates[-2]
                    asian = df[
                        (df["date"] == prev) &
                        (df["hour"] >= 0) &
                        (df["hour"] < 5)
                    ]
                    latest_date = prev

            if asian.empty:
                return None

            high = float(asian["high"].max())
            low = float(asian["low"].min())
            mid = round((high + low) / 2, 5)

            # Midnight Open: the open of the 05:00 UTC candle (00:00 EST)
            midnight_candle = df[
                (df["date"] == latest_date) &
                (df["hour"] == 5)
            ]
            if midnight_candle.empty:
                # Fallback: use the close of the last Asian candle
                midnight_open = float(asian["close"].iloc[-1])
            else:
                midnight_open = float(midnight_candle["open"].iloc[0])

            # Detect sweeps: check candles AFTER 05:00 UTC on the same date
            post_asian = df[
                (df["date"] == latest_date) &
                (df["hour"] >= 5)
            ]

            high_swept = False
            low_swept = False

            if not post_asian.empty:
                pa_highs = post_asian["high"].values
                pa_lows = post_asian["low"].values
                pa_closes = post_asian["close"].values

                for i in range(len(post_asian)):
                    # Asian High swept: wick above + close below
                    if pa_highs[i] > high and pa_closes[i] < high:
                        high_swept = True
                    # Asian Low swept: wick below + close above
                    if pa_lows[i] < low and pa_closes[i] > low:
                        low_swept = True

            return {
                "high": high,
                "low": low,
                "mid": mid,
                "midnight_open": midnight_open,
                "high_swept": high_swept,
                "low_swept": low_swept,
                "date": latest_date,
            }

        except Exception as exc:
            logger.error(f"Error calculating Asian range: {exc}")
            return None
