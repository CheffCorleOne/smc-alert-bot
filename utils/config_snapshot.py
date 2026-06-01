"""Runtime configuration snapshot formatting for logs."""

from config import BotConfig


def format_config_snapshot(config: BotConfig) -> str:
    """Compact runtime settings snapshot for logs."""
    enabled_kzs = []
    if config.trade_asian_kz:
        enabled_kzs.append("Asian")
    if config.trade_london_kz:
        enabled_kzs.append("London")
    if config.trade_ny_kz:
        enabled_kzs.append("NY")
    if config.trade_london_close:
        enabled_kzs.append("LondonClose")

    return (
        f"symbols={','.join(config.active_symbols) or 'none'} | "
        f"risk={config.risk_per_trade_pct}% | maxTrades={config.max_trades_per_day} | "
        f"dailyDD={config.daily_drawdown_pct}% | minRR={config.min_rr} | "
        f"minSlATR={getattr(config, 'min_sl_atr', 1.5)} | "
        f"minSetup={config.min_setup_score} | minBias={config.min_bias_score} | "
        f"strictBias={config.strict_pivot_bias} | spreadMax={config.max_spread_pips} | "
        f"killzones={','.join(enabled_kzs) or 'none'} | "
        f"outsideKZ={getattr(config, 'allow_outside_killzone', False)} "
        f"contextScan={getattr(config, 'scan_outside_killzone_for_intents', True)} | "
        f"newsSnipe={config.news_snipe_mode}/{config.news_snipe_minutes}m | "
        f"scan={getattr(config, 'scan_interval_seconds', 300)}s | "
        f"telegram={'on' if getattr(config, 'telegram_token', '') and getattr(config, 'telegram_chat_id', '') else 'off'} | "
        f"lateEntry={getattr(config, 'enable_late_entry', False)}"
        f"/{getattr(config, 'late_entry_max_m5_bars', 0)}xM5 | "
        f"lateMaxATR={getattr(config, 'late_entry_max_atr_distance', 0.5)} | "
        f"lateMaxTP={getattr(config, 'late_entry_max_tp_progress', 0.5)} | "
        f"latePenalty={getattr(config, 'late_entry_score_penalty', 10.0)}"
    )
