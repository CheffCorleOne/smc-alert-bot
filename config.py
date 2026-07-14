"""
SMC Bot Configuration Module.

Central configuration for all trading parameters, SMC detection settings,
session times, risk management, and dashboard preferences.
Multi-broker support — auto-detects broker from MT5 server name.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict
import json

# Symbol normalisation / pip sizing live in utils.symbols (single source of
# truth). Re-exported here so the long-standing `from config import get_pip_size`
# imports across the codebase keep working unchanged.
from utils.symbols import (  # noqa: F401
    PIP_VALUES,
    get_pip_size,
    normalize_symbol,
)

INSTRUMENT_PROFILES: Dict[str, Dict] = {
    "XAUUSD": {
        "max_spread_pips": 50.0,
        "min_sl_atr_mult": 1.0,
        "min_rr": 2.5,
        "min_bias_score": 2,
        "prefer_asian_in_killzone": True,
        "position_max_age_hours": 24.0,
        "atr_timeframe": "H1",
        "swing_left_bars": 5,
        "swing_right_bars": 5,
        "structure_tf": ["H1", "M15", "M5"],
        "sweep_tf": ["H1", "M15", "M5"],
        "intraday_liquidity_tf": ["H1", "M15"],
        "poi_tf": ["M15", "M5"],
        "confirmation_tf": "M5",
        "late_entry_tf": "M5",
        "tp_timeframes": ["H4", "D1"],
        "tp1_timeframes": ["M15", "H1"],
        "use_m1_fractal": True,
    },
    "NAS100": {
        "max_spread_pips": 20.0,
        "min_sl_atr_mult": 1.2,
        "min_rr": 2.5,
        "min_bias_score": 2,
        "prefer_asian_in_killzone": False,
        "position_max_age_hours": 48.0,
        "atr_timeframe": "H4",
        "swing_left_bars": 5,
        "swing_right_bars": 5,
        "structure_tf": ["H4", "H1", "M15"],
        "sweep_tf": ["H4", "H1", "M15"],
        "intraday_liquidity_tf": ["H4", "H1"],
        "poi_tf": ["H1", "M15"],
        "confirmation_tf": "M15",
        "late_entry_tf": "M15",
        "tp_timeframes": ["H4", "D1"],
        "tp1_timeframes": ["H1", "H4"],
        "use_m1_fractal": False,
    },
    "NQ": {  # Alias
        "max_spread_pips": 20.0,
        "min_sl_atr_mult": 1.2,
        "min_rr": 2.5,
        "min_bias_score": 2,
        "prefer_asian_in_killzone": False,
        "position_max_age_hours": 48.0,
        "atr_timeframe": "H4",
        "swing_left_bars": 5,
        "swing_right_bars": 5,
        "structure_tf": ["H4", "H1", "M15"],
        "sweep_tf": ["H4", "H1", "M15"],
        "intraday_liquidity_tf": ["H4", "H1"],
        "poi_tf": ["H1", "M15"],
        "confirmation_tf": "M15",
        "late_entry_tf": "M15",
        "tp_timeframes": ["H4", "D1"],
        "tp1_timeframes": ["H1", "H4"],
        "use_m1_fractal": False,
    },
    "DEFAULT_FOREX": {
        "max_spread_pips": 5.0,
        "min_sl_atr_mult": 1.5,
        "min_rr": 2.0,
        "prefer_asian_in_killzone": True,
        "position_max_age_hours": 8.0,
        "atr_timeframe": "M15",
        "swing_left_bars": 3,
        "swing_right_bars": 3,
        "structure_tf": ["H1", "M15", "M5"],
        "sweep_tf": ["H1", "M15", "M5"],
        "intraday_liquidity_tf": ["H1", "M15"],
        "poi_tf": ["M15", "M5"],
        "confirmation_tf": "M5",
        "late_entry_tf": "M5",
        "tp_timeframes": ["H4", "D1"],
        "tp1_timeframes": ["M15", "H1"],
        "use_m1_fractal": True,
    },
}

def get_instrument_profile(symbol: str) -> Dict:
    """Return instrument profile or DEFAULT_FOREX."""
    upper = symbol.upper()
    if upper in INSTRUMENT_PROFILES:
        return INSTRUMENT_PROFILES[upper]
    for key, profile in INSTRUMENT_PROFILES.items():
        if key != "DEFAULT_FOREX" and key in upper:
            return profile
    return INSTRUMENT_PROFILES["DEFAULT_FOREX"]
@dataclass
class BotConfig:
    """Master configuration for the SMC trading bot."""

    # ── Broker ───────────────────────────────────────────────
    broker_name: str = "Auto"

    # ── Symbol mapping (base name → MT5 terminal name) ───────
    symbols: Dict[str, str] = field(default_factory=lambda: {
        "EURUSD": "EURUSD",
        "GBPUSD": "GBPUSD",
        "GBPJPY": "GBPJPY",
        "USDJPY": "USDJPY",
        "XAUUSD": "XAUUSD",
        "NAS100": "NAS100",
        "EURGBP": "EURGBP",
        "AUDUSD": "AUDUSD",
    })
    active_symbols: List[str] = field(default_factory=lambda: [
        "EURUSD", "GBPUSD", "GBPJPY", "USDJPY", "XAUUSD", "NAS100", "EURGBP", "AUDUSD"
    ])

    # ── Risk ─────────────────────────────────────────────────
    risk_per_trade_pct: float = 1.0      # % of balance per trade
    max_trades_per_day: int = 5          # was 2 — too restrictive for 6+ symbols
    daily_drawdown_pct: float = 3.0      # Pause trading if daily drawdown > 3%
    max_open_trades: int = 1             # across all symbols
    min_rr: float = 1.5                  # minimum Risk:Reward
    min_sl_atr: float = 1.0              # minimum SL distance = 1.0 * ATR

    # ── Portfolio Risk Guard ─────────────────────────────────
    max_consecutive_losses: int = 3      # Pause trading after 3 consecutive losses
    max_spread_pips: float = 3.0

    # ── Expert Modes ─────────────────────────────────────────
    news_snipe_mode: bool = False        # allow trading 2 mins after high-impact news
    news_snipe_minutes: int = 2          # minutes to wait after news
    atr_sl_multiplier: float = 0.5       # POI edge buffer beyond OB/FVG (M15 ATR)
    enable_late_entry: bool = True       # allow controlled continuation entries (was False)
    late_entry_max_m5_bars: int = 8      # max M5 bars after POI touch/confirmation (was 6)
    late_entry_max_atr_distance: float = 0.8  # max distance from POI in ATRs
    late_entry_max_tp_progress: float = 0.5   # max fraction of path to TP already traveled
    late_entry_score_penalty: float = 10.0    # setup score penalty for late entries

    # ── Pending Intent System (Bot Memory) ───────────────────
    intent_max_age_hours: float = 6.0        # auto-expire intents after N hours (was 4.0)
    intent_use_limit_orders: bool = True     # place limit orders at POI zones
    max_pending_intents: int = 15            # max active intents across all symbols (was 5)
    intent_limit_expiry_hours: float = 2.0   # auto-cancel limit orders after N hours
    max_limit_poi_width_atr: float = 1.2     # max width of POI in ATRs for limit entries
    min_limit_setup_score: float = 75.0      # min score required to approve limit entries

    # ── Entry Refinements ────────────────────────────────────
    require_inducement: bool = False         # require inducement before sweep
    require_sweep_displacement: bool = False # require displacement after sweep
    use_bos_as_entry: bool = True            # accept BOS (not only CHoCH) for entry
    multi_tf_choch: bool = True              # check M15+M5 for CHoCH cascade
    require_mss_displacement: bool = False   # require strong impulse for MSS (was True)
    mss_displacement_multiplier: float = 1.2 # candle body must be > 1.2x average for MSS
    use_consequent_encroachment: bool = True  # use 50% of POI (FVG/OB) as entry trigger
    confirmation_lookback_bars: int = 15     # M5 bars to check for confirmation
    use_breaker_blocks: bool = True          # accept breaker blocks as POI
    use_mitigation_blocks: bool = True       # accept mitigation blocks as POI
    min_sweep_displacement_atr: float = 0.5  # ATR multiple for sweep displacement
    allow_outside_killzone: bool = False     # if True, allow trades outside KZ with score penalty
    outside_kz_score_penalty: float = 15.0   # score deduction for outside-KZ trades
    scan_outside_killzone_for_intents: bool = True  # build context/memory even when entries are blocked

    # ── Smart Trade Management ───────────────────────────────
    # ── SMC Parameters ───────────────────────────────────────
    swing_left_bars: int = 3
    swing_right_bars: int = 3
    eqh_eql_tolerance_pips: int = 5      # was 3 — wider tolerance
    ob_strength_min: float = 1.2         # impulse / OB size ratio (was 1.5)
    fvg_min_size_pips: int = 2           # was 5 — accept smaller FVGs
    min_setup_score: float = 55.0        # minimum score to execute
    min_bias_score: int = 2              # D1+H4 alignment is sufficient
    strict_pivot_bias: bool = False      # if False, uses BOS as trend confirmation fallback
    require_aligned_htf_bias: bool = True  # ICT: stand aside when HTF bias is unclear
                                           # (disables the weak hierarchy fallback in step 1)

    # ── Sessions (UTC) ───────────────────────────────────────
    trade_london_kz: bool = True
    trade_ny_kz: bool = True
    trade_london_close: bool = True
    trade_asian_kz: bool = True
    use_silver_bullet: bool = True       # Enable Silver Bullet entry logic
    ignore_weekend_filter: bool = False  # backtest-only: ignore wall-clock weekend gate

    # ── Asian Range / ICT Power of 3 ─────────────────────────
    use_asian_range: bool = True          # Enable Asian Range as liquidity source
    asian_range_start_utc: int = 0        # 00:00 UTC (19:00 EST)
    asian_range_end_utc: int = 5          # 05:00 UTC (00:00 EST)
    midnight_open_utc: int = 5            # 05:00 UTC — daily anchor
    prefer_asian_in_killzone: bool = True # Prefer Asian targets during killzones

    # ── Auto-Load Symbols ────────────────────────────────────
    auto_load_symbols: bool = True        # auto-discover symbols from MT5
    auto_load_categories: List[str] = field(default_factory=lambda: [
        "Forex", "Metals", "Indices",
    ])
    auto_load_max_symbols: int = 20       # max symbols to auto-load

    # ── Dashboard ────────────────────────────────────────────
    dashboard_port: int = 8050
    auto_open_browser: bool = True
    scan_interval_seconds: int = 30

    # Telegram signal notifications
    telegram_token: str = ""
    telegram_chat_id: str = ""

    def to_dict(self) -> dict:
        """Serialize config to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize config to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "BotConfig":
        """Create config from dictionary, ignoring unknown keys."""
        # Only keep recognised fields; do NOT silently override user choices.
        # (Previous versions force-raised min_bias_score>=3 and
        # max_pending_intents>=15, which contradicted the dashboard and the
        # per-instrument profiles. Respect what the user/profile sets.)
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_str: str) -> "BotConfig":
        """Create config from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def get_mt5_symbol(self, base_symbol: str) -> str:
        """Resolve a base symbol name to the broker MT5 name."""
        return self.symbols.get(base_symbol, base_symbol)

    def symbol_exposure_names(self, symbol: str) -> set:
        """
        All symbol strings that refer to the same instrument (base + broker + aliases).
        Used to match open positions (e.g. NAS100 base vs NQ on Libertex).
        """
        names = {symbol.upper()}
        for base, mt5 in self.symbols.items():
            if base.upper() == symbol.upper() or mt5.upper() == symbol.upper():
                names.add(base.upper())
                names.add(mt5.upper())
        index_aliases = {"NAS100", "NQ", "USTEC", "NDX", "US100"}
        if names & index_aliases:
            names.update(index_aliases)
        else:
            names.add(self.get_mt5_symbol(symbol).upper())
        return names


# Pip-size helpers (PIP_VALUES, get_pip_size, normalize_symbol) now live in
# utils.symbols and are re-exported at the top of this module.
