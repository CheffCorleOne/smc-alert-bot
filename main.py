"""
SMC Bot — Main Entry Point.

Orchestrates all components:
1. mt5.initialize() → verify connection (no credentials needed)
2. Auto-detect broker from server name
3. Auto-load symbols from MT5 (if enabled)
4. Check all symbols exist in MT5 MarketWatch
5. Load settings from SQLite (or defaults if first run)
6. Start Dash server in background thread
7. Auto-open browser: http://localhost:8050
8. Start APScheduler

Scheduler jobs:
- Every configured scan interval: Scanner (multi-TF analysis → Telegram signal)
"""

import sys
import os
import signal
import webbrowser
import threading
import time
import concurrent.futures
from datetime import datetime, timezone, timedelta

# Ensure smc_bot is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apscheduler.schedulers.background import BackgroundScheduler

from config import BotConfig, get_pip_size
from broker.connector import MT5Connector
from broker.order_manager import OrderManager
from broker.risk_manager import RiskManager
from core.entry_engine import SMCEntryEngine
from core.sessions import SessionManager
from data.database import Database
from data.fetcher import MarketDataFetcher
from dashboard.app import create_app, start_server
from telegram_notifier import TelegramNotifier
from utils.logger import get_logger
from utils.news_filter import NewsFilter
from utils.config_snapshot import format_config_snapshot

logger = get_logger("main")


# ── Shared state between bot and dashboard ───────────────────
bot_state: dict = {
    "connector": None,
    "fetcher": None,
    "config": None,
    "db": None,
    "entry_engine": None,
    "order_manager": None,
    "risk_manager": None,
    "bot_running": False,
    "signals": {},
    "analysis_status": {},    # Per-symbol: bias, step, status
    "intent_data": {},        # PendingIntent dashboard data
    "news_filter": None,
    "verified_symbols": {},   # base_name → True/False
    "active_symbols": [],     # For dashboard dynamic rendering
    "scheduler": None,
    "notifier": None,
    "notified_signals": {},
    "notified_signal_times": {},
    "acknowledged_signals": {},
}


def _get_next_allowed_session(base_symbol: str, config: BotConfig, session_mgr: SessionManager) -> str:
    """Helper to determine the next upcoming allowed and active session/killzone for a symbol."""
    from core.sessions import SESSIONS
    
    allowed_kzs = session_mgr.get_session_for_instrument(base_symbol)
    if not allowed_kzs:
        return "No sessions allowed"

    # Filter by user's configuration
    active_kzs = []
    for kz in allowed_kzs:
        if kz == "london_killzone" and not config.trade_london_kz:
            continue
        if kz in ("ny_killzone", "ny_equities_open", "ny_pm_killzone") and not config.trade_ny_kz:
            continue
        if kz == "asian_killzone" and not config.trade_asian_kz:
            continue
        if kz == "london_close" and not config.trade_london_close:
            continue
        active_kzs.append(kz)
        
    # Also add Silver Bullet if enabled and relevant
    if getattr(config, "use_silver_bullet", True):
        if "london_killzone" in allowed_kzs:
            active_kzs.append("silver_bullet_london")
        if "ny_killzone" in allowed_kzs:
            active_kzs.append("silver_bullet_ny")
            
    if not active_kzs:
        return "No sessions enabled"
        
    # Map to human-readable names
    session_names = {
        "asian_killzone": "Asian",
        "london_killzone": "London",
        "ny_killzone": "New York",
        "ny_equities_open": "NY Equities Open",
        "london_close": "London Close",
        "ny_pm_killzone": "NY PM",
        "silver_bullet_london": "Silver Bullet London",
        "silver_bullet_ny": "Silver Bullet NY",
    }

    now_time = datetime.now(timezone.utc).time()
    
    # Calculate time until each session starts (in minutes)
    def minutes_until(start_time, current_time):
        curr_mins = current_time.hour * 60 + current_time.minute
        start_mins = start_time.hour * 60 + start_time.minute
        diff = start_mins - curr_mins
        if diff <= 0:
            diff += 24 * 60  # Wraps to next day
        return diff
        
    # Sort active_kzs by closest start time
    try:
        sorted_kzs = sorted(
            active_kzs,
            key=lambda kz: minutes_until(SESSIONS[kz]["start"], now_time)
        )
        next_kz = sorted_kzs[0]
        return session_names.get(next_kz, next_kz.replace("_", " ").title())
    except Exception:
        return "Allowed Sessions"


def _analyze_single_symbol(base_symbol: str) -> None:
    """Helper function to analyze a single symbol, intended for concurrent execution."""
    config: BotConfig = bot_state["config"]
    fetcher: MarketDataFetcher = bot_state["fetcher"]
    engine: SMCEntryEngine = bot_state["entry_engine"]
    order_mgr: OrderManager = bot_state["order_manager"]
    risk_mgr: RiskManager = bot_state["risk_manager"]
    db: Database = bot_state["db"]
    news: NewsFilter = bot_state["news_filter"]
    verified = bot_state.get("verified_symbols", {})

    session_mgr = SessionManager()

    try:
        # Skip symbols not found in MarketWatch
        if not verified.get(base_symbol, False):
            bot_state["analysis_status"][base_symbol] = {
                "bias": "—", "step": 0, "status": "Symbol not found"
            }
            return

        # Check session filter. By default, still scan outside entry
        # windows so PendingIntent memory is ready when a killzone opens.
        session_allowed = session_mgr.should_trade_now(
            base_symbol,
            config.trade_london_kz,
            config.trade_ny_kz,
            config.trade_asian_kz,
            config.trade_london_close,
        )
        if not session_allowed and not (
            getattr(config, "scan_outside_killzone_for_intents", True)
            or getattr(config, "allow_outside_killzone", False)
        ):
            session = session_mgr.get_current_session()
            next_sess = _get_next_allowed_session(base_symbol, config, session_mgr)
            logger.info(f"[{base_symbol}] Skipped by session filter ({session})")
            bot_state["analysis_status"][base_symbol] = {
                "bias": "—", "step": 0,
                "status": f"Waiting ({next_sess})"
            }
            return
        if not session_allowed:
            session = session_mgr.get_current_session()
            next_sess = _get_next_allowed_session(base_symbol, config, session_mgr)
            logger.debug(
                f"[{base_symbol}] Outside entry session ({session}); "
                f"scanning context for {next_sess}"
            )

        # Check news filter
        if news:
            minutes_after = config.news_snipe_minutes if getattr(config, "news_snipe_mode", False) else 30
            active_news = news.is_news_time(base_symbol, 30, minutes_after)
            if active_news:
                bot_state["analysis_status"][base_symbol] = {
                    "bias": "—", "step": 0, "status": f"News Block: {active_news.title} ({active_news.currency})"
                }
                db.log_event(
                    "warning",
                    f"News filter active — skipping {base_symbol} ({active_news.title})",
                    category="filter", symbol=base_symbol,
                )
                return

        # Resolve symbol name for broker
        mt5_symbol = config.get_mt5_symbol(base_symbol)

        # Fetch multi-TF data
        data = fetcher.get_multi_timeframe(mt5_symbol)

        # Check we have minimum required data
        if data.get("H1") is None or data.get("M5") is None:
            bot_state["analysis_status"][base_symbol] = {
                "bias": "—", "step": 0, "status": "Waiting for data..."
            }
            return

        # Get daily trade count
        today_trades = db.get_today_trades()
        daily_count = len(today_trades)

        # Get daily drawdown
        daily_stats = risk_mgr.get_daily_stats(db)
        daily_dd = daily_stats.get("drawdown", 0)

        # Get open positions
        open_positions = order_mgr.get_open_positions()

        # ── Phase 1: Check existing PendingIntent ──
        active_intent = engine.intent_mgr.get(base_symbol)
        intent_signal = engine.check_intent(
            symbol=base_symbol,
            data=data,
            daily_trades=daily_count,
            daily_drawdown_pct=daily_dd,
            open_positions=open_positions,
        )

        signal = None
        step = 0
        reason = ""
        htf_bias = "unknown"

        if intent_signal is not None:
            signal = intent_signal
            step = 9
            htf_bias = signal.direction
            if signal.setup_type == "LIMIT_ORDER":
                logger.info(f"[{base_symbol}] Intent POI reached -- signal ready")
            else:
                logger.info(f"[{base_symbol}] Intent confirmed -- signal ready")
        elif active_intent is not None and active_intent.is_active():
            # We have an active intent. Skip fresh analysis to avoid overwriting it
            step = 8
            htf_bias = active_intent.htf_bias
            reason = f"Waiting: {active_intent.waiting_detail}"
            logger.info(f"[{base_symbol}] ⏳ Intent Active ({htf_bias.upper()}): {active_intent.waiting_detail}")
        else:
            # ── Phase 2: Fresh 9-step analysis ──
            signal, step, reason, htf_bias = engine.analyze(
                symbol=base_symbol,
                data=data,
                daily_trades=daily_count,
                daily_drawdown_pct=daily_dd,
                open_positions=open_positions,
            )

        # Update intent dashboard data
        bot_state["intent_data"] = engine.intent_mgr.get_dashboard_data()

        if signal is not None:
            # Update analysis status
            bot_state["analysis_status"][base_symbol] = {
                "bias": "bullish" if signal.direction == "buy" else "bearish",
                "step": 9,
                "status": f"Signal found! {signal.direction.upper()}",
            }

            # Log signal to database
            db.log_signal(
                symbol=signal.symbol,
                direction=signal.direction,
                entry_price=signal.entry_price,
                sl_price=signal.sl_price,
                tp1_price=0.0,
                tp2_price=signal.tp_price,
                rr_ratio=signal.rr_ratio,
                setup_score=signal.setup_score,
                setup_type=signal.setup_type,
                confidence=signal.confidence,
                session=signal.session,
                executed=False,
                steps_log=signal.steps_log,
            )

            signal_key = f"{signal.direction}_{float(signal.entry_price):.5f}"
            if bot_state["notified_signals"].get(base_symbol) != signal_key:
                bot_state["acknowledged_signals"].pop(base_symbol, None)
            already_acked = (
                bot_state["acknowledged_signals"].get(base_symbol) == signal_key
            )

            # Store for dashboard display
            bot_state["signals"][base_symbol] = {
                "direction": signal.direction,
                "entry_price": signal.entry_price,
                "sl_price": signal.sl_price,
                "tp_price": signal.tp_price,
                "setup_score": signal.setup_score,
                "setup_type": signal.setup_type,
                "confidence": signal.confidence,
                "rr_ratio": signal.rr_ratio,
                "signal_key": signal_key,
                "acknowledged": already_acked,
            }

            # Notify if quality is sufficient
            if signal.setup_score >= config.min_setup_score:
                pip_size = get_pip_size(base_symbol)
                sl_pips = abs(signal.entry_price - signal.sl_price) / pip_size

                lot = risk_mgr.calculate_lot_size(
                    mt5_symbol, sl_pips, config.risk_per_trade_pct
                )

                notifier: TelegramNotifier = bot_state.get("notifier")
                now = datetime.now(timezone.utc)
                last_sent = bot_state["notified_signal_times"].get(base_symbol)
                resend_due = (
                    last_sent is None
                    or now - last_sent > timedelta(minutes=5)
                )
                is_same_signal = bot_state["notified_signals"].get(base_symbol) == signal_key

                if already_acked:
                    bot_state["analysis_status"][base_symbol]["status"] = "Signal acknowledged"
                elif notifier is None:
                    bot_state["analysis_status"][base_symbol]["status"] = "Signal ready (Telegram off)"
                elif not is_same_signal or resend_due:
                    estimated_hours = notifier.estimate_hold_time(
                        signal.session, signal.rr_ratio
                    )
                    sent = notifier.send_signal(signal, lot, estimated_hours)
                    if sent:
                        bot_state["notified_signals"][base_symbol] = signal_key
                        bot_state["notified_signal_times"][base_symbol] = now
                        bot_state["analysis_status"][base_symbol]["status"] = "Signal notified"
                        db.log_event(
                            "signal",
                            f"SIGNAL: {signal.direction.upper()} {lot} {base_symbol} "
                            f"@ {signal.entry_price} | RR 1:{signal.rr_ratio:.1f} "
                            f"| Score {signal.setup_score:.0f}",
                            category="signal", symbol=base_symbol,
                        )
                    else:
                        bot_state["analysis_status"][base_symbol]["status"] = "Signal notify failed"
                        db.log_event(
                            "warning",
                            f"Telegram send failed for {base_symbol} {signal_key}",
                            category="signal", symbol=base_symbol,
                        )
                else:
                    bot_state["analysis_status"][base_symbol]["status"] = "Signal active (waiting ack)"
            else:
                db.log_event(
                    "warning",
                    f"Signal found but score too low: "
                    f"{base_symbol} {signal.direction} "
                    f"(score={signal.setup_score:.0f} < {config.min_setup_score})",
                    category="signal", symbol=base_symbol,
                )
        else:
            bot_state["notified_signals"].pop(base_symbol, None)
            bot_state["notified_signal_times"].pop(base_symbol, None)
            bot_state["acknowledged_signals"].pop(base_symbol, None)

            # ── Create PendingIntent for partial setups ──
            steps_data = {
                "liquidity_target": None,
                "liquidity_type": "",
                "sweep_level": None,
            }
            # Try to create an intent if bias + liquidity confirmed
            if htf_bias in ("bullish", "bearish") and step > 2:
                # Re-run early steps to retrieve the structural target and sweep info for the intent memory
                step2 = engine._step2_liquidity_target(data, base_symbol, htf_bias)
                if step2.passed:
                    steps_data["liquidity_target"] = step2.data.get("target_level")
                    steps_data["liquidity_type"] = step2.data.get("target_type", "")
                    
                    if step > 3:
                        step3 = engine._step3_sweep(data, base_symbol, step2.data)
                        if step3.passed:
                            steps_data["sweep_level"] = step3.data.get("sweep_level")
                            steps_data["sweep"] = step3.data.get("sweep")
                            steps_data["sweep_type"] = step3.data.get("sweep_type", "standard")
                            
                            if step > 4:
                                direction = "buy" if htf_bias == "bullish" else "sell"
                                step5 = engine._step5_poi(data, base_symbol, direction, htf_bias)
                                if step5.passed:
                                    steps_data["poi"] = step5.data.get("poi")
                                    steps_data["poi_type"] = step5.data.get("poi_type", "")

                engine.try_create_intent(
                    symbol=base_symbol,
                    data=data,
                    failed_step=step,
                    htf_bias=htf_bias,
                    steps_data=steps_data,
                )

            # Get intent info for status display
            active_intent = engine.intent_mgr.get(base_symbol)
            if active_intent and active_intent.is_active():
                bot_state["analysis_status"][base_symbol] = {
                    "bias": htf_bias,
                    "step": step - 1,
                    "status": f"🧠 {active_intent.waiting_detail}",
                    "intent": active_intent.to_dashboard_dict(),
                }
            else:
                bot_state["analysis_status"][base_symbol] = {
                    "bias": htf_bias,
                    "step": step - 1,
                    "status": f"Failed Step {step}: {reason}"
                }
            bot_state["signals"].pop(base_symbol, None)

    except Exception as exc:
        logger.error(f"Error scanning {base_symbol}: {exc}")
        bot_state["analysis_status"][base_symbol] = {
            "bias": "—", "step": 0, "status": f"Error: {str(exc)[:30]}"
        }
        db.log_event("error", f"Scan error: {exc}",
                     category="error", symbol=base_symbol)


def scan_markets() -> None:
    """
    Main scanning job — runs at the configured scan interval.

    For each active symbol (processed concurrently):
    1. Fetch fresh OHLCV for all timeframes
    2. Run SMCEntryEngine.analyze()
    3. If signal found with sufficient quality → notify Telegram
    4. Update dashboard analysis_status for each symbol
    """
    if not bot_state["bot_running"]:
        return

    connector = bot_state["connector"]
    if connector is None or not connector.connected:
        return

    config: BotConfig = bot_state["config"]
    risk_mgr: RiskManager = bot_state["risk_manager"]
    db: Database = bot_state["db"]

    session_mgr = SessionManager()

    if session_mgr.is_weekend():
        for sym in config.active_symbols:
            bot_state["analysis_status"][sym] = {
                "bias": "unknown", "step": 0, "status": "Weekend"
            }
        return

    # Check daily limits
    if risk_mgr.is_daily_limit_reached(db):
        for sym in config.active_symbols:
            bot_state["analysis_status"][sym] = {
                "bias": "—", "step": 9, "status": "Daily limit reached"
            }
        return

    # Log current status for transparency
    logger.info("═══ Market Signal Scan Start ═══")
    logger.info(f"Runtime settings | {format_config_snapshot(config)}")

    # Execute scanning concurrently for all symbols
    max_workers = min(10, len(config.active_symbols)) if config.active_symbols else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(_analyze_single_symbol, config.active_symbols)

    # End-of-scan summary (all pairs are scanned; logs above may show only one symbol)
    statuses = bot_state.get("analysis_status", {})
    in_trade = [s for s, st in statuses.items() if "trade" in str(st.get("status", "")).lower()]
    waiting = [s for s, st in statuses.items() if "Waiting" in str(st.get("status", ""))]
    signaled = [s for s, st in statuses.items() if "Signal" in str(st.get("status", ""))]
    step_fail = [
        s for s, st in statuses.items()
        if 0 < st.get("step", 0) < 9 and "Signal" not in str(st.get("status", ""))
    ]
    logger.info(
        f"Scan complete: {len(config.active_symbols)} symbols | "
        f"in trade: {len(in_trade)} | intents/wait: {len(waiting)} | "
        f"signals: {len(signaled)} | partial steps: {len(step_fail)}"
    )
    if waiting:
        logger.info(f"  Waiting: {', '.join(waiting)}")
    if in_trade:
        logger.info(f"  In trade: {', '.join(in_trade)}")


def main() -> None:
    """Main entry point for the SMC trading bot."""
    logger.info("=" * 60)
    logger.info("  SMC Bot — Smart Money Concept Signal Notifier")
    logger.info("  Multi-Broker | Auto-Detect | Telegram Alerts")
    logger.info("=" * 60)

    # ── Initialize components ────────────────────────────────
    config = BotConfig()
    db = Database()

    # Load saved settings from database
    saved = db.load_settings()
    if saved:
        try:
            config = BotConfig.from_dict(saved)
            logger.info("Settings loaded from database")
        except Exception as exc:
            logger.warning(f"Could not load saved settings: {exc}")

    connector = MT5Connector()
    fetcher = MarketDataFetcher()
    order_mgr = OrderManager(config, connector, db)
    risk_mgr = RiskManager(config, connector)
    entry_engine = SMCEntryEngine(config)
    news = NewsFilter()

    # Populate shared state
    bot_state["connector"] = connector
    bot_state["fetcher"] = fetcher
    bot_state["config"] = config
    bot_state["db"] = db
    bot_state["entry_engine"] = entry_engine
    bot_state["order_manager"] = order_mgr
    bot_state["risk_manager"] = risk_mgr
    bot_state["news_filter"] = news

    if config.telegram_token and config.telegram_chat_id:
        try:
            notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
            notifier.bind_state(
                bot_state["signals"],
                bot_state["notified_signals"],
                bot_state["acknowledged_signals"],
            )
            notifier.start_polling()
            bot_state["notifier"] = notifier
            logger.info("Telegram notifier initialized")
        except Exception as exc:
            bot_state["notifier"] = None
            logger.error(f"Telegram notifier initialization failed: {exc}")

    db.log_event("info", "SMC Bot initialized", category="system")

    # ── Connect to MT5 (no credentials) ──────────────────────
    logger.info("Connecting to MT5 terminal...")
    if connector.connect():
        logger.info(f"✓ MT5 connection established (Broker: {connector.broker_key.upper()})")
        config.broker_name = connector.broker_key

        # ── Auto-load symbols from MT5 ───────────────────────
        if getattr(config, 'auto_load_symbols', True):
            logger.info("Auto-loading symbols from MT5...")
            auto_symbols = connector.auto_load_symbols(
                categories=config.auto_load_categories,
                max_symbols=config.auto_load_max_symbols,
            )
            if auto_symbols:
                # Merge into config.symbols without overwriting existing entries
                new_count = 0
                for base_name, mt5_name in auto_symbols.items():
                    if base_name not in config.symbols:
                        config.symbols[base_name] = mt5_name
                        new_count += 1
                    # Also resolve existing entries that might have wrong MT5 names
                    elif config.symbols[base_name] != mt5_name:
                        # Check if the configured name actually works
                        existing_info = None
                        try:
                            mt5_mod = connector._get_mt5() if hasattr(connector, '_get_mt5') else _get_mt5_module()
                        except Exception:
                            mt5_mod = None
                        # Keep existing mapping if it's already verified

                logger.info(
                    f"Auto-load: {new_count} new symbols added, "
                    f"{len(config.active_symbols)} active from saved settings"
                )

        # Verify symbols exist in MarketWatch
        verified = connector.verify_symbols(config.symbols)
        bot_state["verified_symbols"] = verified

        available = [s for s, ok in verified.items() if ok]
        missing = [s for s, ok in verified.items() if not ok]

        logger.info(f"Available symbols: {', '.join(available)}")
        if missing:
            logger.warning(f"Missing symbols (skipped): {', '.join(missing)}")

        # Filter active symbols to only verified ones
        config.active_symbols = [s for s in config.active_symbols if verified.get(s, False)]

        # Update shared state for dashboard
        bot_state["active_symbols"] = config.active_symbols.copy()

        db.log_event("info",
                     f"Connected to MT5 ({connector.broker_key}). "
                     f"Symbols: {', '.join(available)}",
                     category="system")
    else:
        logger.warning("⚠ MT5 not connected — start MT5 terminal and log in")
        logger.info("Dashboard will show status. Bot will auto-connect when MT5 is ready.")
        bot_state["active_symbols"] = config.active_symbols.copy()
        db.log_event("warning", "MT5 not connected on startup", category="system")

    # ── Start Dashboard ──────────────────────────────────────
    logger.info("Starting dashboard...")
    app = create_app(bot_state)
    dash_thread = start_server(app, port=config.dashboard_port)

    # ── Start APScheduler ────────────────────────────────────
    scheduler = BackgroundScheduler()
    bot_state["scheduler"] = scheduler
    scheduler.add_job(
        scan_markets, "interval",
        seconds=config.scan_interval_seconds,
        id="scanner", name="Market Scanner",
        max_instances=1, coalesce=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started (scanner: {config.scan_interval_seconds}s)"
    )

    # ── Open browser ─────────────────────────────────────────
    if config.auto_open_browser:
        time.sleep(2)  # Wait for server to start
        url = f"http://localhost:{config.dashboard_port}"
        webbrowser.open(url)
        logger.info(f"Browser opened: {url}")

    # ── Keep alive ───────────────────────────────────────────
    logger.info("")
    logger.info("Bot is running. Press Ctrl+C to stop.")
    logger.info(f"Dashboard: http://localhost:{config.dashboard_port}")
    logger.info("")

    def shutdown(sig=None, frame=None):
        logger.info("Shutting down...")
        bot_state["bot_running"] = False
        scheduler.shutdown(wait=False)
        connector.disconnect()
        db.log_event("info", "SMC Bot shutdown", category="system")
        logger.info("Goodbye! 👋")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    bot_state["bot_running"] = True

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
