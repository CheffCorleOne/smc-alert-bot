"""
Points of Interest Engine — Order Blocks, FVGs, Breaker Blocks.

Identifies institutional supply/demand zones where smart money
has left unfilled orders.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import pandas as pd
import numpy as np

from config import get_pip_size
from utils.logger import get_logger

logger = get_logger("poi")


# ── Data Structures ──────────────────────────────────────────

@dataclass
class OrderBlock:
    """Institutional Order Block — supply or demand zone."""
    type: str               # 'bullish' or 'bearish'
    top: float              # Upper boundary of the OB
    bottom: float           # Lower boundary of the OB
    index: int              # Bar index of the OB candle
    timestamp: datetime
    is_fresh: bool = True   # Price hasn't returned to OB yet
    is_breaker: bool = False  # OB violated → becomes breaker
    strength: float = 0.0   # impulse_size / ob_size ratio
    impulse_bars: int = 0   # How many bars the impulse lasted
    timeframe: str = ""     # Timeframe this OB was found on


@dataclass
class FVG:
    """Fair Value Gap (Imbalance) — 3-candle price gap."""
    type: str               # 'bullish' or 'bearish'
    top: float
    bottom: float
    index: int              # Index of the middle candle
    timestamp: datetime
    is_filled: bool = False
    timeframe: str = ""     # Timeframe this FVG was found on


@dataclass
class BreakerBlock:
    """Breaker Block — a violated Order Block that flips polarity."""
    type: str               # 'bullish' or 'bearish'
    top: float
    bottom: float
    original_ob_type: str   # What the OB was before it broke
    index: int
    timestamp: datetime


@dataclass
class MitigationBlock:
    """Mitigation Block — OB that was partially filled."""
    type: str
    top: float
    bottom: float
    index: int
    timestamp: datetime
    fill_percentage: float  # How much of the OB was filled (0-100)


class POIEngine:
    """
    Detects Points of Interest for SMC trading.

    POIs include:
    - Order Blocks: last opposite candle before an impulse move
    - Fair Value Gaps: 3-candle imbalances in price
    - Breaker Blocks: order blocks that failed and flipped
    - Mitigation Blocks: partially filled order blocks
    """

    def __init__(self, symbol: str = "EURUSD") -> None:
        self.symbol = symbol
        self.pip_size = get_pip_size(symbol)

    def find_bullish_ob(
        self,
        df: pd.DataFrame,
        min_strength: float = 1.5,
    ) -> List[OrderBlock]:
        """
        Find Bullish Order Blocks.

        A bullish OB is the last bearish candle BEFORE a strong bullish
        impulse that causes a Break of Structure or significant move up.

        The OB zone spans from the open to close of that bearish candle.
        It's marked 'fresh' if price hasn't returned to it.

        Args:
            df: OHLCV DataFrame.
            min_strength: Minimum impulse/OB size ratio.

        Returns:
            List of OrderBlock with type='bullish'.
        """
        obs = []
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        times = df["time"].values

        for i in range(2, len(df) - 3):
            # Look for a bearish candle (close < open)
            if closes[i] >= opens[i]:
                continue

            ob_top = max(opens[i], closes[i])
            ob_bottom = min(opens[i], closes[i])
            ob_size = ob_top - ob_bottom

            if ob_size <= 0:
                continue

            # Check for strong bullish impulse after the OB candle
            impulse_high = 0
            impulse_bars = 0
            for j in range(i + 1, min(i + 6, len(df))):
                if closes[j] > opens[j]:  # Bullish candle
                    impulse_high = max(impulse_high, highs[j])
                    impulse_bars += 1
                else:
                    break

            impulse_size = impulse_high - ob_bottom
            if impulse_size <= 0:
                continue

            strength = impulse_size / ob_size
            if strength < min_strength:
                continue

            # A tap into the block is mitigation; freshness is lost only when
            # price fully violates the block.
            is_fresh = True
            for j in range(i + impulse_bars + 1, len(df)):
                if lows[j] <= ob_bottom:
                    is_fresh = False
                    break

            # Check if OB is broken (price went through it = breaker)
            is_breaker = False
            for j in range(i + impulse_bars + 1, len(df)):
                if closes[j] < ob_bottom:
                    is_breaker = True
                    break

            obs.append(OrderBlock(
                type="bullish",
                top=round(ob_top, 5),
                bottom=round(ob_bottom, 5),
                index=i,
                timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                is_fresh=is_fresh,
                is_breaker=is_breaker,
                strength=round(strength, 2),
                impulse_bars=impulse_bars,
            ))

        logger.debug(
            f"Found {len(obs)} bullish OBs "
            f"({sum(1 for o in obs if o.is_fresh)} fresh)"
        )
        return obs

    def find_bearish_ob(
        self,
        df: pd.DataFrame,
        min_strength: float = 1.5,
    ) -> List[OrderBlock]:
        """
        Find Bearish Order Blocks.

        A bearish OB is the last bullish candle BEFORE a strong bearish
        impulse that causes a BOS or significant move down.

        Args:
            df: OHLCV DataFrame.
            min_strength: Minimum impulse/OB size ratio.

        Returns:
            List of OrderBlock with type='bearish'.
        """
        obs = []
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        times = df["time"].values

        for i in range(2, len(df) - 3):
            # Look for a bullish candle (close > open)
            if closes[i] <= opens[i]:
                continue

            ob_top = max(opens[i], closes[i])
            ob_bottom = min(opens[i], closes[i])
            ob_size = ob_top - ob_bottom

            if ob_size <= 0:
                continue

            # Check for strong bearish impulse after
            impulse_low = float("inf")
            impulse_bars = 0
            for j in range(i + 1, min(i + 6, len(df))):
                if closes[j] < opens[j]:  # Bearish candle
                    impulse_low = min(impulse_low, lows[j])
                    impulse_bars += 1
                else:
                    break

            if impulse_low == float("inf"):
                continue

            impulse_size = ob_top - impulse_low
            if impulse_size <= 0:
                continue

            strength = impulse_size / ob_size
            if strength < min_strength:
                continue

            is_fresh = True
            for j in range(i + impulse_bars + 1, len(df)):
                if highs[j] >= ob_top:
                    is_fresh = False
                    break

            is_breaker = False
            for j in range(i + impulse_bars + 1, len(df)):
                if closes[j] > ob_top:
                    is_breaker = True
                    break

            obs.append(OrderBlock(
                type="bearish",
                top=round(ob_top, 5),
                bottom=round(ob_bottom, 5),
                index=i,
                timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                is_fresh=is_fresh,
                is_breaker=is_breaker,
                strength=round(strength, 2),
                impulse_bars=impulse_bars,
            ))

        logger.debug(
            f"Found {len(obs)} bearish OBs "
            f"({sum(1 for o in obs if o.is_fresh)} fresh)"
        )
        return obs

    def find_fvg(
        self,
        df: pd.DataFrame,
        min_size_pips: int = 5,
    ) -> List[FVG]:
        """
        Find Fair Value Gaps (Imbalances).

        Bullish FVG: candle[i-1].high < candle[i+1].low
          (gap between candle 1 high and candle 3 low)
        Bearish FVG: candle[i-1].low > candle[i+1].high
          (gap between candle 1 low and candle 3 high)

        Args:
            df: OHLCV DataFrame.
            min_size_pips: Minimum gap size in pips to qualify.

        Returns:
            List of FVG objects.
        """
        fvgs = []
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        times = df["time"].values
        min_size = min_size_pips * self.pip_size

        for i in range(1, len(df) - 1):
            # Bullish FVG: gap up
            if lows[i + 1] > highs[i - 1]:
                gap_size = lows[i + 1] - highs[i - 1]
                if gap_size >= min_size:
                    fvg_top = lows[i + 1]
                    fvg_bottom = highs[i - 1]

                    # Check if gap has been filled
                    is_filled = False
                    for j in range(i + 2, len(df)):
                        if closes[j] <= fvg_bottom:
                            is_filled = True
                            break

                    fvgs.append(FVG(
                        type="bullish",
                        top=round(fvg_top, 5),
                        bottom=round(fvg_bottom, 5),
                        index=i,
                        timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                        is_filled=is_filled,
                    ))

            # Bearish FVG: gap down
            if highs[i + 1] < lows[i - 1]:
                gap_size = lows[i - 1] - highs[i + 1]
                if gap_size >= min_size:
                    fvg_top = lows[i - 1]
                    fvg_bottom = highs[i + 1]

                    is_filled = False
                    for j in range(i + 2, len(df)):
                        if closes[j] >= fvg_top:
                            is_filled = True
                            break

                    fvgs.append(FVG(
                        type="bearish",
                        top=round(fvg_top, 5),
                        bottom=round(fvg_bottom, 5),
                        index=i,
                        timestamp=pd.Timestamp(times[i]).to_pydatetime(),
                        is_filled=is_filled,
                    ))

        logger.debug(
            f"Found {len(fvgs)} FVGs "
            f"({sum(1 for f in fvgs if not f.is_filled)} unfilled)"
        )
        return fvgs

    def find_breaker_blocks(
        self,
        df: pd.DataFrame,
        order_blocks: List[OrderBlock],
    ) -> List[BreakerBlock]:
        """
        Find Breaker Blocks — Order Blocks that were violated.

        When price breaks through an OB, the OB flips polarity:
        - Bullish OB broken to downside → Bearish Breaker
        - Bearish OB broken to upside → Bullish Breaker

        Breakers can act as new support/resistance.

        Args:
            df: OHLCV DataFrame.
            order_blocks: Previously detected Order Blocks.

        Returns:
            List of BreakerBlock.
        """
        breakers = []
        closes = df["close"].values

        for ob in order_blocks:
            if not ob.is_breaker:
                continue

            if ob.type == "bullish":
                # Bullish OB broken downside = Bearish Breaker
                breakers.append(BreakerBlock(
                    type="bearish",
                    top=ob.top,
                    bottom=ob.bottom,
                    original_ob_type="bullish",
                    index=ob.index,
                    timestamp=ob.timestamp,
                ))
            else:
                # Bearish OB broken upside = Bullish Breaker
                breakers.append(BreakerBlock(
                    type="bullish",
                    top=ob.top,
                    bottom=ob.bottom,
                    original_ob_type="bearish",
                    index=ob.index,
                    timestamp=ob.timestamp,
                ))

        return breakers

    def find_mitigation_blocks(
        self,
        df: pd.DataFrame,
        order_blocks: List[OrderBlock],
    ) -> List[MitigationBlock]:
        """
        Find Mitigation Blocks — OBs that were partially filled.

        When price enters an OB zone but doesn't fully break through,
        the remaining unfilled portion can act as a reaction zone.

        Args:
            df: OHLCV DataFrame.
            order_blocks: Previously detected Order Blocks.

        Returns:
            List of MitigationBlock.
        """
        mitigations = []
        highs = df["high"].values
        lows = df["low"].values

        for ob in order_blocks:
            if ob.is_breaker or ob.is_fresh:
                continue  # Skip broken or untouched OBs

            ob_range = ob.top - ob.bottom
            if ob_range <= 0:
                continue

            # Calculate how deep price penetrated
            max_penetration = 0
            for j in range(ob.index + 1, len(df)):
                if ob.type == "bullish":
                    if lows[j] < ob.top:
                        pen = ob.top - lows[j]
                        max_penetration = max(max_penetration, pen)
                else:
                    if highs[j] > ob.bottom:
                        pen = highs[j] - ob.bottom
                        max_penetration = max(max_penetration, pen)

            fill_pct = min(100, (max_penetration / ob_range) * 100)

            if 10 < fill_pct < 100:
                mitigations.append(MitigationBlock(
                    type=ob.type,
                    top=ob.top,
                    bottom=ob.bottom,
                    index=ob.index,
                    timestamp=ob.timestamp,
                    fill_percentage=round(fill_pct, 1),
                ))

        return mitigations
