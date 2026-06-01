"""
Premium / Discount Zone Calculator.

Uses Fibonacci retracement between swing high and swing low
to determine whether price is in a premium or discount zone.
"""

from typing import Dict, Optional

from utils.logger import get_logger

logger = get_logger("pd_zones")


# Standard Fibonacci levels
FIB_LEVELS = {
    0.0: "Swing Low (Deep Discount)",
    0.236: "First Pullback",
    0.5: "Equilibrium",
    0.62: "OTE Start",
    0.705: "OTE Sweet Spot",
    0.79: "OTE End",
    1.0: "Swing High (Deep Premium)",
}


def get_premium_discount_zones(
    swing_high: float, swing_low: float
) -> Dict:
    """
    Calculate Fibonacci-based premium/discount zones.

    - Below 0.5 (equilibrium) = Discount zone → bullish entries
    - Above 0.5 = Premium zone → bearish entries
    - 0.618–0.786 = Optimal Trade Entry (OTE) zone

    Args:
        swing_high: The higher price boundary.
        swing_low: The lower price boundary.

    Returns:
        Dict with keys:
          'levels': dict of fib_level → price
          'equilibrium': price at 50%
          'discount_zone': (low, eq) range
          'premium_zone': (eq, high) range
          'ote_zone': (0.618, 0.786) price range
    """
    price_range = swing_high - swing_low

    if price_range <= 0:
        return {}

    levels = {}
    for fib, label in FIB_LEVELS.items():
        price = swing_low + (price_range * fib)
        levels[fib] = round(price, 5)

    eq = levels[0.5]
    ote_low = levels[0.62]
    ote_sweet = levels[0.705]
    ote_high = levels[0.79]

    return {
        "levels": levels,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "equilibrium": eq,
        "discount_zone": (swing_low, eq),
        "premium_zone": (eq, swing_high),
        "ote_zone": (ote_low, ote_high),
        "ote_sweet_spot": ote_sweet,
    }


def is_in_discount(price: float, zones: Dict) -> bool:
    """
    Check if price is in the discount zone (below equilibrium).

    Bullish entries should come from the discount zone.

    Args:
        price: Current price.
        zones: Output from get_premium_discount_zones().

    Returns:
        True if price is below the 50% equilibrium level.
    """
    if not zones:
        return False
    low, eq = zones["discount_zone"]
    return low <= price <= eq


def is_in_premium(price: float, zones: Dict) -> bool:
    """
    Check if price is in the premium zone (above equilibrium).

    Bearish entries should come from the premium zone.

    Args:
        price: Current price.
        zones: Output from get_premium_discount_zones().

    Returns:
        True if price is above the 50% equilibrium level.
    """
    if not zones:
        return False
    eq, high = zones["premium_zone"]
    return eq <= price <= high


def is_in_ote(price: float, zones: Dict) -> bool:
    """
    Check if price is in the Optimal Trade Entry zone (0.618–0.786).

    This is the highest-probability entry zone in SMC methodology.

    Args:
        price: Current price.
        zones: Output from get_premium_discount_zones().

    Returns:
        True if price is between the 0.618 and 0.786 fib levels.
    """
    if not zones:
        return False
    low, high = zones["ote_zone"]
    return low <= price <= high


def get_zone_label(price: float, zones: Dict) -> str:
    """
    Get a human-readable label for the current price position.

    Args:
        price: Current price.
        zones: Output from get_premium_discount_zones().

    Returns:
        String like 'Deep Discount', 'Discount', 'Equilibrium',
        'Premium', 'Deep Premium'.
    """
    if not zones:
        return "Unknown"

    levels = zones["levels"]
    eq = zones["equilibrium"]

    if price < levels[0.236]:
        return "Deep Discount"
    if price < eq:
        return "Discount"
    elif abs(price - eq) < (zones["swing_high"] - zones["swing_low"]) * 0.02:
        return "Equilibrium"
    elif price < levels[0.79]:
        return "Premium"
    else:
        return "Deep Premium"
