"""
Single source of truth for symbol normalisation and pip sizing.

Previously this logic was duplicated (and subtly divergent) across
``config.get_pip_size``, ``broker.connector._strip_broker_suffix``,
``core.sessions``, ``utils.news_filter`` and ``data.fetcher``. Centralising it
removes that drift. This module has NO project dependencies, so it is safe to
import from ``config`` without a circular import.
"""

from __future__ import annotations

# ── Pip sizes ────────────────────────────────────────────────
PIP_VALUES: dict[str, float] = {
    # Forex — standard 4-digit pairs
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001, "EURGBP": 0.0001, "EURAUD": 0.0001,
    "EURNZD": 0.0001, "EURCHF": 0.0001, "EURCAD": 0.0001, "GBPAUD": 0.0001,
    "GBPNZD": 0.0001, "GBPCAD": 0.0001, "GBPCHF": 0.0001, "AUDCAD": 0.0001,
    "AUDNZD": 0.0001, "AUDCHF": 0.0001, "NZDCAD": 0.0001, "NZDCHF": 0.0001,
    "CADCHF": 0.0001,
    # Forex — JPY pairs (2-digit)
    "USDJPY": 0.01, "GBPJPY": 0.01, "EURJPY": 0.01, "AUDJPY": 0.01,
    "NZDJPY": 0.01, "CADJPY": 0.01, "CHFJPY": 0.01,
    # Metals
    "XAUUSD": 0.01, "XAGUSD": 0.001,
    # Indices
    "NAS100": 0.25, "NQ": 0.25, "USTEC": 0.25,
    "US500": 0.25, "SP500": 0.25, "SPX500": 0.25,
    "US30": 1.0, "DOW": 1.0, "DJ30": 1.0,
    "DAX": 0.1, "GER40": 0.1, "GER30": 0.1,
    "UK100": 0.1, "FTSE": 0.1,
}

# Broker suffixes, longest first so e.g. ".raw" is stripped before "r"/"a".
_SUFFIXES = (
    ".raw", ".pro", ".std", ".ecn", "_sb", ".a", ".e", ".i", ".b", ".r", "#",
)

# Canonical aliases (broker-specific name -> our canonical base name).
_ALIASES = {
    "NQ": "NAS100", "NSDQ": "NAS100", "USTEC": "NAS100", "NDX": "NAS100",
    "DJI": "US30", "DJ30": "US30", "DOW": "US30",
    "GER40": "DAX", "GER30": "DAX", "DE40": "DAX", "DE30": "DAX",
    "FTSE": "UK100", "FTSE100": "UK100",
    "SPX500": "US500", "S&P500": "US500",
}

_MAJORS = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}


def strip_suffix(name: str) -> str:
    """Strip a broker suffix (incl. trailing 'm' on Forex pairs) — no aliasing."""
    clean = name
    lower = clean.lower()
    for suffix in _SUFFIXES:
        if lower.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    # Single-char 'm' (e.g. Libertex) only when it leaves a plausible Forex pair.
    if clean.endswith("m") and len(clean) >= 7 and clean[:-1].upper()[:3] in _MAJORS:
        clean = clean[:-1]
    return clean


def normalize_symbol(name: str) -> str:
    """
    Resolve any broker symbol name to our canonical base name.

    Examples: ``EURUSDm`` -> ``EURUSD``, ``USTEC.raw`` -> ``NAS100``,
    ``NQ`` -> ``NAS100``, ``XAUUSD.pro`` -> ``XAUUSD``.
    """
    if not name:
        return ""
    clean = strip_suffix(name)
    return _ALIASES.get(clean.upper(), clean.upper())


def get_pip_size(symbol: str) -> float:
    """Return pip size for a symbol. Uses known values, then heuristics."""
    upper = symbol.upper()

    # Strip a broker suffix for the lookup (keep legacy guard on length).
    clean = upper
    for suffix in ("M", ".RAW", ".PRO", ".A", ".E", "#", ".I", "_SB", ".STD", ".ECN"):
        if clean.endswith(suffix) and len(clean) > len(suffix) + 3:
            clean = clean[: -len(suffix)]
            break

    if clean in PIP_VALUES:
        return PIP_VALUES[clean]

    # Partial match (e.g. "EURUSD" inside "EURUSDm")
    for key, val in PIP_VALUES.items():
        if key in upper:
            return val

    if "JPY" in upper:
        return 0.01
    if "XAU" in upper or "GOLD" in upper:
        return 0.01
    if "XAG" in upper or "SILVER" in upper:
        return 0.001
    for idx_key in ("NAS", "USTEC", "NDX", "US500", "SP500", "SPX"):
        if idx_key in upper:
            return 0.25
    for idx_key in ("US30", "DOW", "DJ30"):
        if idx_key in upper:
            return 1.0
    for idx_key in ("DAX", "GER"):
        if idx_key in upper:
            return 0.1
    for idx_key in ("UK100", "FTSE"):
        if idx_key in upper:
            return 0.1

    return 0.0001
