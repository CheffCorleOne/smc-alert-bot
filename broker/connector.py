"""
MT5 Connector — multi-broker connection handler.

Auto-detects broker from MT5 server name and resolves symbol names.
MT5 terminal is always already open and logged in.
Connection is just mt5.initialize() — no server/login/password needed.
Handles auto-reconnection every 30 seconds.
"""

import re
import threading
from typing import Dict, List, Optional, Any

from utils.logger import get_logger

logger = get_logger("connector")

from broker.mt5_client import get_mt5 as _get_mt5  # noqa: E402  (kept local alias)


# ── Broker Detection Rules ──────────────────────────────────
BROKER_RULES = {
    "libertex":    {"suffix": "m",    "aliases": {"NAS100": "NQ"}},
    "icmarkets":   {"suffix": "",     "alt_suffixes": [".raw", ".pro"]},
    "ftmo":        {"suffix": "",     "aliases": {}},
    "tickmill":    {"suffix": ".pro", "aliases": {}},
    "pepperstone": {"suffix": "",     "aliases": {}},
    "roboforex":   {"suffix": "",     "aliases": {}},
    "xm":          {"suffix": "",     "aliases": {}},
    "exness":      {"suffix": "",     "aliases": {}},
}

# All known suffixes for stripping / brute-force
ALL_SUFFIXES = ["", "m", ".raw", ".pro", ".a", ".e", "#", ".i", "_SB",
                ".std", ".ecn", ".b", "c", ".r"]

# Major currencies for Forex classification
MAJOR_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}

# Patterns for symbol categorisation
FOREX_PAIR_RE = re.compile(
    r"^(?:.*?)?(AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD)"
    r"(AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD)",
    re.IGNORECASE,
)
METAL_KEYWORDS = ("XAU", "XAG", "GOLD", "SILVER")
INDEX_KEYWORDS = (
    "NAS", "USTEC", "NDX", "US500", "SP500", "SPX",
    "US30", "DOW", "DJ30", "DAX", "GER",
    "UK100", "FTSE", "JP225", "NIKKEI",
)


class MT5Connector:
    """
    Manages connection to MetaTrader 5 terminal.

    MT5 is assumed to be already running and logged in by the user.
    Connection is established via mt5.initialize() with no credentials.

    Features:
    - Simple connect via mt5.initialize()
    - Auto broker detection from server name
    - Symbol name resolution for any broker
    - Auto-reconnection every 30 seconds
    - Symbol verification with automatic fallback alternatives
    - Auto-load symbols from MarketWatch
    """

    def __init__(self) -> None:
        self._connected = False
        self._account_info: Dict[str, Any] = {}
        self._reconnect_thread: Optional[threading.Thread] = None
        self._stop_reconnect = threading.Event()
        self._lock = threading.Lock()
        self._broker_key: str = "unknown"

    @property
    def connected(self) -> bool:
        """Check if currently connected to MT5."""
        with self._lock:
            if self._connected:
                try:
                    mt5 = _get_mt5()
                    info = mt5.terminal_info()
                    if info is None:
                        self._connected = False
                except Exception:
                    self._connected = False
            return self._connected

    @property
    def broker_key(self) -> str:
        """Return detected broker key (e.g. 'libertex', 'ftmo', 'unknown')."""
        return self._broker_key

    @property
    def algo_trading_allowed(self) -> bool:
        """Check if Algo Trading is enabled in MT5 terminal."""
        if not self.connected:
            return False
        try:
            mt5 = _get_mt5()
            t_info = mt5.terminal_info()
            a_info = mt5.account_info()

            terminal_allowed = t_info is not None and t_info.trade_allowed
            account_allowed = a_info is not None and a_info.trade_allowed

            if not terminal_allowed:
                logger.warning("MT5 Button 'Algo Trading' is OFF (Red). Please click it to enable trading.")

            if not account_allowed:
                logger.error("BROKER/ACCOUNT restriction: Trading is disabled for this account. Check if you used an Investor Password or if the Demo account expired.")

            return terminal_allowed and account_allowed
        except Exception as exc:
            logger.error(f"Error checking algo trading status: {exc}")
            return False

    # ── Connection ───────────────────────────────────────────

    def connect(self) -> bool:
        """
        Connect to MT5 terminal.

        Simply calls mt5.initialize() — the terminal must already
        be open and logged into an account by the user.

        Returns:
            True if connection successful.
        """
        try:
            mt5 = _get_mt5()

            if not mt5.initialize():
                error = mt5.last_error()
                logger.error(f"MT5 initialize failed: {error}")
                return False

            account = mt5.account_info()
            if account is None:
                logger.error("Failed to get account info after initialize")
                return False

            with self._lock:
                self._connected = True
                self._account_info = {
                    "login": account.login,
                    "name": account.name,
                    "server": account.server,
                    "balance": account.balance,
                    "equity": account.equity,
                    "margin": account.margin,
                    "free_margin": account.margin_free,
                    "leverage": account.leverage,
                    "currency": account.currency,
                }

            # Auto-detect broker from server name
            self._broker_key = self._detect_broker(account.server)

            logger.info(
                f"Connected to MT5 — Account #{account.login} | "
                f"Server: {account.server} | "
                f"Broker: {self._broker_key.upper()} | "
                f"Balance: {account.balance} {account.currency} | "
                f"Leverage: 1:{account.leverage}"
            )

            # Start reconnection thread
            self._start_reconnect_loop()

            return True

        except Exception as exc:
            logger.error(f"MT5 connection error: {exc}")
            return False

    def disconnect(self) -> None:
        """Disconnect from MT5 and stop reconnection thread."""
        self._stop_reconnect.set()
        try:
            mt5 = _get_mt5()
            mt5.shutdown()
        except Exception:
            pass
        with self._lock:
            self._connected = False
        logger.info("Disconnected from MT5")

    def is_connected(self) -> bool:
        """Check connection status (alias for connected property)."""
        return self.connected

    def get_account_info(self) -> Dict[str, Any]:
        """Get current account information with live balance/equity."""
        if not self.connected:
            return {}
        try:
            mt5 = _get_mt5()
            account = mt5.account_info()
            if account:
                with self._lock:
                    self._account_info.update({
                        "balance": account.balance,
                        "equity": account.equity,
                        "margin": account.margin,
                        "free_margin": account.margin_free,
                    })
        except Exception as exc:
            logger.error(f"Error getting account info: {exc}")

        return self._account_info.copy()

    # ── Broker Detection ─────────────────────────────────────

    def _detect_broker(self, server_name: str) -> str:
        """
        Detect broker from MT5 account server name.

        Args:
            server_name: e.g. 'Libertex-Demo', 'ICMarketsSC-Demo', 'FTMO-Server'.

        Returns:
            Broker key string (lowercase).
        """
        if not server_name:
            return "unknown"

        server_lower = server_name.lower()

        for broker_key in BROKER_RULES:
            if broker_key in server_lower:
                logger.info(f"Broker detected: {broker_key} (from server '{server_name}')")
                return broker_key

        logger.info(f"Unknown broker (server: '{server_name}') — will try all symbol variations")
        return "unknown"

    def _get_name_alternatives(self, base_name: str) -> List[str]:
        """
        Generate list of possible MT5 symbol names for a base name.

        Tries broker-specific names first, then common variations.

        Args:
            base_name: Canonical name like 'EURUSD', 'NAS100', 'XAUUSD'.

        Returns:
            List of possible MT5 names to try, ordered by likelihood.
        """
        broker = self._broker_key
        rules = BROKER_RULES.get(broker, {})
        alternatives = []

        # Check broker-specific aliases first (e.g. NAS100 → NQ for Libertex)
        aliases = rules.get("aliases", {})
        if base_name in aliases:
            alternatives.append(aliases[base_name])

        # Primary: base + broker suffix
        primary_suffix = rules.get("suffix", "")
        if primary_suffix:
            alternatives.append(base_name + primary_suffix)

        # Base name itself
        if base_name not in alternatives:
            alternatives.append(base_name)

        # Broker-specific alternative suffixes
        for alt_suffix in rules.get("alt_suffixes", []):
            name = base_name + alt_suffix
            if name not in alternatives:
                alternatives.append(name)

        # For unknown broker, try all known suffixes
        if broker == "unknown":
            for suffix in ALL_SUFFIXES:
                name = base_name + suffix
                if name not in alternatives:
                    alternatives.append(name)

            # Also try common index aliases
            index_aliases = {
                "NAS100": ["NQ", "USTEC", "USTEC.r", "NDX", "NAS100m", "NSDQ"],
                "US500": ["SP500", "SPX500", "S&P500", "US500m"],
                "US30": ["DOW", "DJ30", "DJI", "US30m"],
                "DAX": ["GER40", "GER30", "DE40", "DE30", "DAXm"],
                "UK100": ["FTSE", "FTSE100", "UK100m"],
            }
            for alias in index_aliases.get(base_name, []):
                if alias not in alternatives:
                    alternatives.append(alias)

        return alternatives

    # ── Symbol Verification ──────────────────────────────────

    def verify_symbols(self, symbol_map: Dict[str, str]) -> Dict[str, bool]:
        """
        Verify which symbols exist in MT5 MarketWatch.

        If the configured name is not found, tries alternative names
        based on the detected broker.

        Args:
            symbol_map: Dict of base_name → MT5 name (e.g. {"NAS100": "NQ"}).

        Returns:
            Dict of base_name → True/False (exists in MT5).
        """
        if not self.connected:
            return {k: False for k in symbol_map}

        mt5 = _get_mt5()
        results = {}

        for base_name, mt5_name in list(symbol_map.items()):
            # Try the configured name first
            info = mt5.symbol_info(mt5_name)
            if info is not None:
                mt5.symbol_select(mt5_name, True)
                results[base_name] = True
                logger.info(f"Symbol verified: {base_name} -> {mt5_name}")
                continue

            # Configured name failed — try alternatives
            found = False
            alternatives = self._get_name_alternatives(base_name)

            for alt_name in alternatives:
                if alt_name == mt5_name:
                    continue  # Already tried

                info = mt5.symbol_info(alt_name)
                if info is not None:
                    mt5.symbol_select(alt_name, True)
                    # Update the symbol map with the working name
                    symbol_map[base_name] = alt_name
                    results[base_name] = True
                    logger.info(
                        f"Symbol resolved: {base_name} -> {alt_name} "
                        f"(original '{mt5_name}' not found)"
                    )
                    found = True
                    break

            if not found:
                results[base_name] = False
                logger.warning(
                    f"Symbol NOT FOUND: {base_name} "
                    f"(tried: {mt5_name}, {', '.join(alternatives[:5])}...) "
                    f"-- will be skipped"
                )

        return results

    def get_symbol_info(self, mt5_symbol: str) -> Optional[Dict]:
        """Get full symbol info from MT5."""
        if not self.connected:
            return None
        try:
            mt5 = _get_mt5()
            info = mt5.symbol_info(mt5_symbol)
            if info is None:
                return None
            return {
                "name": info.name,
                "point": info.point,
                "digits": info.digits,
                "spread": info.spread,
                "trade_contract_size": info.trade_contract_size,
                "volume_min": info.volume_min,
                "volume_max": info.volume_max,
                "volume_step": info.volume_step,
            }
        except Exception as exc:
            logger.error(f"Error getting symbol info for {mt5_symbol}: {exc}")
            return None

    # ── Auto-Load Symbols ────────────────────────────────────

    def auto_load_symbols(
        self,
        categories: Optional[List[str]] = None,
        max_symbols: int = 20,
    ) -> Dict[str, str]:
        """
        Auto-discover tradeable symbols from MT5 MarketWatch.

        Scans all available symbols, categorises them, filters by spread,
        and returns a mapping of base_name → mt5_name.

        Args:
            categories: List of categories to include: 'Forex', 'Metals', 'Indices'.
            max_symbols: Maximum number of symbols to return.

        Returns:
            Dict of {base_name: mt5_name} sorted by spread (most liquid first).
        """
        if not self.connected:
            logger.warning("Cannot auto-load symbols: not connected to MT5")
            return {}

        if categories is None:
            categories = ["Forex", "Metals", "Indices"]

        mt5 = _get_mt5()

        try:
            all_symbols = mt5.symbols_get()
        except Exception as exc:
            logger.error(f"Failed to get symbols from MT5: {exc}")
            return {}

        if all_symbols is None or len(all_symbols) == 0:
            logger.warning("No symbols returned from MT5")
            return {}

        logger.info(f"Auto-load: scanning {len(all_symbols)} symbols from MT5...")

        candidates = []

        for sym in all_symbols:
            name = sym.name
            spread = sym.spread

            # Skip symbols with excessive spread (illiquid)
            if spread >= 200:
                continue

            # Skip symbols not visible or not available for trading
            if not sym.visible and not sym.select:
                continue

            # Categorise
            category = self._categorize_symbol(name)
            if category not in categories:
                continue

            base_name = self._strip_broker_suffix(name)
            candidates.append({
                "base_name": base_name,
                "mt5_name": name,
                "spread": spread,
                "category": category,
            })

        # Sort by spread (lower = more liquid)
        candidates.sort(key=lambda x: x["spread"])

        # Deduplicate by base_name (keep the one with lowest spread)
        seen_bases = set()
        result: Dict[str, str] = {}

        for c in candidates:
            base = c["base_name"]
            if base in seen_bases:
                continue
            seen_bases.add(base)
            result[base] = c["mt5_name"]

            if len(result) >= max_symbols:
                break

        categories_found = {}
        for c in candidates:
            cat = c["category"]
            categories_found[cat] = categories_found.get(cat, 0) + 1

        logger.info(
            f"Auto-load complete: {len(result)} symbols "
            f"(categories: {categories_found})"
        )

        for base, mt5_name in list(result.items())[:10]:
            logger.info(f"  {base} -> {mt5_name}")
        if len(result) > 10:
            logger.info(f"  ... and {len(result) - 10} more")

        return result

    def _categorize_symbol(self, name: str) -> str:
        """Categorise a symbol name into Forex, Metals, or Indices."""
        upper = name.upper()

        # Check metals first (more specific)
        for keyword in METAL_KEYWORDS:
            if keyword in upper:
                return "Metals"

        # Check indices
        for keyword in INDEX_KEYWORDS:
            if keyword in upper:
                return "Indices"

        # Check Forex (must contain two major currencies)
        clean = self._strip_broker_suffix(name).upper()
        if FOREX_PAIR_RE.match(clean):
            return "Forex"

        # Also accept if the raw name matches a Forex pair pattern
        if len(clean) >= 6:
            first3 = clean[:3]
            second3 = clean[3:6]
            if first3 in MAJOR_CURRENCIES and second3 in MAJOR_CURRENCIES:
                return "Forex"

        return "Other"

    def _strip_broker_suffix(self, mt5_name: str) -> str:
        """
        Strip broker-specific suffix to get the canonical base name.

        Examples:
            EURUSDm   -> EURUSD
            EURUSD.raw -> EURUSD
            EURUSD.pro -> EURUSD
            NQ        -> NAS100  (known alias)
            US30      -> US30
        """
        # Known aliases (reverse mapping)
        known_aliases = {
            "NQ": "NAS100",
            "NSDQ": "NAS100",
            "USTEC": "NAS100",
            "NDX": "NAS100",
            "DJI": "US30",
            "DJ30": "US30",
            "DOW": "US30",
            "GER40": "DAX",
            "GER30": "DAX",
            "DE40": "DAX",
            "DE30": "DAX",
            "FTSE": "UK100",
            "FTSE100": "UK100",
            "SPX500": "US500",
            "S&P500": "US500",
        }

        # Check known aliases first (exact match after removing suffixes)
        clean = mt5_name
        clean_lower = clean.lower()
        for suffix in (".raw", ".pro", ".a", ".e", ".i", "_sb", ".std", ".ecn",
                        ".b", ".r", "#"):
            if clean_lower.endswith(suffix):
                clean = clean[:-len(suffix)]
                break

        # Single-char suffix 'm' (Libertex) — only strip if it looks like a Forex pair
        if (clean.endswith("m") and len(clean) >= 7
                and clean[:-1].upper()[:3] in MAJOR_CURRENCIES):
            clean = clean[:-1]

        upper_clean = clean.upper()
        if upper_clean in known_aliases:
            return known_aliases[upper_clean]

        return clean

    # ── Reconnection ─────────────────────────────────────────

    def _start_reconnect_loop(self) -> None:
        """Start background reconnection thread (every 30s)."""
        self._stop_reconnect.clear()

        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return

        def reconnect_worker():
            while not self._stop_reconnect.is_set():
                self._stop_reconnect.wait(30)
                if self._stop_reconnect.is_set():
                    break

                if not self.connected:
                    logger.info("Attempting MT5 reconnection...")
                    try:
                        mt5 = _get_mt5()
                        if mt5.initialize():
                            with self._lock:
                                self._connected = True
                            logger.info("Reconnected to MT5 successfully")
                        else:
                            logger.warning(
                                f"Reconnect failed: {mt5.last_error()}"
                            )
                    except Exception as exc:
                        logger.error(f"Reconnect error: {exc}")

        self._reconnect_thread = threading.Thread(
            target=reconnect_worker, daemon=True, name="mt5-reconnect"
        )
        self._reconnect_thread.start()
