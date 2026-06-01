"""
Dashboard Callbacks -- all Dash interactivity.

Handles:
- Theme toggle (clientside: swap dark/light Bootstrap CSS)
- Auto-connect status display
- Chart updates (auto-adapts to dark/light theme)
- Bot start/stop
- Symbol status sidebar (dynamic)
- Settings save/load (with new params)
- Positions, log, statistics
- UTC clock
- Refresh symbols from MT5
"""

from datetime import datetime, timezone
from typing import Any, List

from dash import Input, Output, State, callback_context, html, no_update
from dash import clientside_callback, ClientsideFunction
import dash_bootstrap_components as dbc

from dashboard.charts import SMCChartBuilder, build_empty_chart
from utils.config_snapshot import format_config_snapshot
from utils.logger import get_logger

logger = get_logger("callbacks")

DEFAULT_INSTRUMENTS = ["EURUSD", "GBPUSD", "GBPJPY", "USDJPY", "XAUUSD", "NAS100", "EURGBP", "AUDUSD"]


def _get_active_symbols(bot_state: dict) -> List[str]:
    """Read active symbols from bot_state with fallback."""
    config = bot_state.get("config")
    if config and hasattr(config, "active_symbols") and config.active_symbols:
        return list(config.active_symbols)
    return bot_state.get("active_symbols", DEFAULT_INSTRUMENTS)


def register_callbacks(app: Any, bot_state: dict) -> None:
    """Register all Dash callbacks."""

    # -- Theme Toggle (clientside) --------------------------------
    app.clientside_callback(
        """
        function(toggleOn, storedTheme) {
            // toggleOn = true means "Light", false means "Dark"
            var darkLink = document.getElementById('theme-dark');
            var lightLink = document.getElementById('theme-light');

            if (!darkLink || !lightLink) {
                return window.dash_clientside.no_update;
            }

            if (toggleOn) {
                darkLink.disabled = true;
                lightLink.disabled = false;
                return 'light';
            } else {
                darkLink.disabled = false;
                lightLink.disabled = true;
                return 'dark';
            }
        }
        """,
        Output("store-theme", "data"),
        Input("theme-toggle", "value"),
        State("store-theme", "data"),
    )

    # On page load, restore theme from localStorage
    app.clientside_callback(
        """
        function(storedTheme) {
            var darkLink = document.getElementById('theme-dark');
            var lightLink = document.getElementById('theme-light');
            if (!darkLink || !lightLink) return window.dash_clientside.no_update;

            if (storedTheme === 'light') {
                darkLink.disabled = true;
                lightLink.disabled = false;
                return true;
            } else {
                darkLink.disabled = false;
                lightLink.disabled = true;
                return false;
            }
        }
        """,
        Output("theme-toggle", "value"),
        Input("store-theme", "data"),
    )

    # -- UTC Clock ------------------------------------------------
    @app.callback(
        Output("utc-clock", "children"),
        Input("interval-clock", "n_intervals"),
    )
    def update_clock(_n: int) -> str:
        return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    # -- Session Indicator ----------------------------------------
    @app.callback(
        Output("session-text", "children"),
        Input("interval-fast", "n_intervals"),
    )
    def update_session(_n: int) -> str:
        from core.sessions import SessionManager
        mgr = SessionManager()
        info = mgr.get_session_display()
        session = info["current_session"]
        if info["is_weekend"]:
            return "Weekend -- Market Closed"
        if info["is_killzone"]:
            kz = info["active_killzone"] or ""
            if kz == "ny_equities_open":
                kz_display = "NY Equities Open (09:30 EST)"
            else:
                kz_display = kz.replace("_", " ").title()
            return f"KILLZONE: {kz_display}"
        names = {
            "asian": "Asian Session (20:00-06:00)",
            "frankfurt": "Frankfurt (06:00-07:00)",
            "london": "London Session (07:00-12:00)",
            "ny": "NY Session (12:00-19:00)",
            "ny_lunch": "NY Lunch (no trading)",
            "london_killzone": "London Killzone (07:00-10:00)",
            "ny_killzone": "NY Killzone (12:00-15:00)",
            "asian_killzone": "Asian Killzone (00:00-04:00)",
            "london_close": "London Close (15:00-17:00)",
            "ny_pm_killzone": "NY PM Killzone (19:00-21:00)",
            "after_hours": "After Hours",
            "inter_session": "Between Sessions",
        }
        return names.get(session, session.replace("_", " ").title())

    @app.callback(
        [Output("connection-status", "children"),
         Output("header-account", "children"),
         Output("broker-name-text", "children"),
         Output("symbols-count", "children"),
         Output("sidebar-active-symbols", "children")],
        Input("interval-fast", "n_intervals"),
    )
    def update_connection(_n):
        connector = bot_state.get("connector")
        symbols = _get_active_symbols(bot_state)
        sym_count = f"Symbols: {len(symbols)} active"
        
        # Build premium active symbols badge display with broker mapping!
        badges = []
        config = bot_state.get("config")
        for sym in symbols:
            mt5_sym = config.get_mt5_symbol(sym) if config else sym
            badge_text = sym if mt5_sym == sym else f"{sym} ➔ {mt5_sym}"
            badges.append(dbc.Badge(
                badge_text, 
                color="success", 
                className="me-1 mb-1", 
                style={"fontSize": "11px", "padding": "4px 8px", "borderRadius": "4px", "fontWeight": "600"}
            ))
        if not badges:
            badges = [html.Span("No active symbols", style={"fontSize": "11px", "opacity": "0.5", "fontStyle": "italic"})]

        if connector is None:
            return (
                html.Span("No connector", style={"color": "#ff6b6b", "fontSize": "13px"}),
                "", "--", sym_count, badges
            )
        broker = getattr(connector, 'broker_key', 'unknown').upper()
        if connector.connected:
            info = connector.get_account_info()
            status = html.Span("Connected", style={
                "color": "#51cf66", "fontSize": "13px", "fontWeight": "600",
            })
            account_text = (
                f"Balance: ${info.get('balance', 0):,.2f} | "
                f"Equity: ${info.get('equity', 0):,.2f} | "
                f"Free: ${info.get('free_margin', 0):,.2f}"
            )
            return status, account_text, broker, sym_count, badges
        else:
            return (
                html.Span("Disconnected", style={"color": "#ff6b6b", "fontSize": "13px"}),
                "MT5 not connected", broker, sym_count, badges
            )

    @app.callback(
        Output("store-bot-running", "data"),
        [Input("btn-start", "n_clicks"),
         Input("btn-stop", "n_clicks")],
    )
    def toggle_bot(start_clicks, stop_clicks):
        ctx = callback_context
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            # Initial load or refresh: return actual server state
            return bot_state.get("bot_running", False)

        trigger = ctx.triggered[0]["prop_id"].split(".")[0]
        if trigger == "btn-start":
            connector = bot_state.get("connector")
            if connector and not connector.connected:
                return False
            bot_state["bot_running"] = True
            logger.info("Bot STARTED by user")
            config = bot_state.get("config")
            if config:
                logger.info(f"Runtime settings | {format_config_snapshot(config)}")
            db = bot_state.get("db")
            if db:
                db.log_event("info", "Bot started", category="system")
            return True
        elif trigger == "btn-stop":
            bot_state["bot_running"] = False
            logger.info("Bot STOPPED by user")
            db = bot_state.get("db")
            if db:
                db.log_event("info", "Bot stopped", category="system")
            return False
        return no_update

    # -- Reset Daily Statistics ------------------------------------
    @app.callback(
        Output("reset-stats-status", "children"),
        Input("btn-reset-stats", "n_clicks"),
        prevent_initial_call=True,
    )
    def reset_stats(n_clicks):
        db = bot_state.get("db")
        risk_mgr = bot_state.get("risk_manager")
        
        try:
            if db:
                db.reset_today_trades()
            if risk_mgr:
                risk_mgr.reset_daily()
            if db:
                db.log_event("info", "Daily trading statistics reset by user from dashboard.", category="system")
            logger.info("Daily statistics reset successfully by user request.")
            return f"Stats reset! ({datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC)"
        except Exception as e:
            logger.error(f"Error resetting daily stats: {e}")
            return f"Error: {e}"

    # -- Symbol Status Sidebar (dynamic container) -----------------
    @app.callback(
        Output("symbol-status-container", "children"),
        Input("interval-slow", "n_intervals"),
    )
    def update_symbol_status(_n):
        analysis = bot_state.get("analysis_status", {})
        symbols = _get_active_symbols(bot_state)
        rows = []
        for sym in symbols:
            info = analysis.get(sym, {})
            bias = info.get("bias", "unknown")
            step = info.get("step", 0)
            status = info.get("status", "Scanning...")
            if bias == "bullish":
                bias_text, bias_style = "UP", {"fontSize": "12px", "color": "#198754", "fontWeight": "700"}
            elif bias == "bearish":
                bias_text, bias_style = "DN", {"fontSize": "12px", "color": "#dc3545", "fontWeight": "700"}
            else:
                bias_text, bias_style = "--", {"fontSize": "12px", "opacity": "0.5"}
            rows.append(html.Div([
                dbc.Row([
                    dbc.Col(html.Span(sym, style={"fontWeight": "600", "fontSize": "13px"}), width=5),
                    dbc.Col(html.Span(bias_text, style=bias_style), width=2, style={"textAlign": "center"}),
                    dbc.Col(html.Span(f"{step}/9", style={"fontSize": "11px", "fontWeight": "600"}), width=5, style={"textAlign": "right"}),
                ], align="center"),
                html.Div(status, style={"fontSize": "11px", "marginTop": "2px", "opacity": "0.7"}),
                html.Hr(style={"margin": "6px 0", "opacity": "0.3"}),
            ]))
        return rows if rows else [html.P("No symbols", style={"fontSize": "11px", "opacity": "0.5"})]

    # -- Intent Panel (Bot Thinking) ------------------------------
    @app.callback(
        Output("intent-panel", "children"),
        Input("interval-slow", "n_intervals"),
    )
    def update_intent_panel(_n):
        intent_data = bot_state.get("intent_data", {})
        active = intent_data.get("active_intents", {})

        if not active:
            return html.P("No pending setups", style={
                "fontSize": "11px", "opacity": "0.5", "fontStyle": "italic",
            })

        items = []
        phase_colors = {
            "watching": "#fd7e14",   # orange
            "ready": "#0d6efd",     # blue
            "armed": "#198754",     # green
            "triggered": "#51cf66", # light green
        }

        for sym, info in active.items():
            phase = info.get("phase", "watching")
            color = phase_colors.get(phase, "#6c757d")
            direction = info.get("direction", "")
            dir_icon = "📈" if direction == "buy" else "📉"
            ttl = info.get("ttl_minutes")
            ttl_text = f" ({ttl:.0f}m left)" if ttl else ""

            items.append(html.Div([
                html.Div([
                    html.Span(f"{dir_icon} {sym} ", style={
                        "fontWeight": "700", "fontSize": "12px",
                    }),
                    html.Span(phase.upper(), style={
                        "fontSize": "10px", "fontWeight": "700",
                        "color": "white", "backgroundColor": color,
                        "padding": "1px 6px", "borderRadius": "3px",
                        "marginLeft": "4px",
                    }),
                    html.Span(ttl_text, style={
                        "fontSize": "10px", "opacity": "0.5",
                        "marginLeft": "4px",
                    }),
                ]),
                html.Div(
                    info.get("narrative", ""),
                    style={
                        "fontSize": "10px", "opacity": "0.7",
                        "marginTop": "2px", "lineHeight": "1.3",
                    },
                ),
                html.Div(
                    info.get("waiting_detail", ""),
                    style={
                        "fontSize": "10px", "fontWeight": "600",
                        "color": color, "marginTop": "1px",
                    },
                ),
                html.Hr(style={"margin": "6px 0", "opacity": "0.2"}),
            ]))

        return items

    # -- Chart Symbol Options Sync --------------------------------
    @app.callback(
        [Output("chart-symbol", "options"),
         Output("chart-symbol", "value")],
        Input("interval-fast", "n_intervals"),
        State("chart-symbol", "value")
    )
    def update_chart_symbol_options(_n, current_value):
        symbols = _get_active_symbols(bot_state)
        options = [{"label": s, "value": s} for s in symbols]
        if current_value in symbols:
            new_value = current_value
        else:
            new_value = symbols[0] if symbols else "EURUSD"
        return options, new_value

    # -- Main Chart -----------------------------------------------
    @app.callback(
        Output("main-chart", "figure"),
        [Input("interval-slow", "n_intervals"),
         Input("chart-symbol", "value"),
         Input("tf-selector", "value"),
         Input("store-theme", "data")],
    )
    def update_chart(_n, symbol, timeframe, theme):
        dark_mode = (theme != "light")

        if not symbol or not timeframe:
            return build_empty_chart(dark_mode)

        connector = bot_state.get("connector")
        if connector is None or not connector.connected:
            return build_empty_chart(dark_mode)

        fetcher = bot_state.get("fetcher")
        config = bot_state.get("config")
        if fetcher is None or config is None:
            return build_empty_chart(dark_mode)

        try:
            mt5_symbol = config.get_mt5_symbol(symbol)
            df = fetcher.get_ohlcv(mt5_symbol, timeframe, bars=200)
            if df is None or df.empty:
                return build_empty_chart(dark_mode)

            lb = config.swing_left_bars
            rb = config.swing_right_bars
            chart = SMCChartBuilder(df, symbol, timeframe, dark_mode=dark_mode)

            from core.market_structure import detect_swing_highs, detect_swing_lows, detect_bos, detect_choch
            from core.liquidity import LiquidityEngine
            from core.poi import POIEngine

            sh = detect_swing_highs(df, lb, rb)
            sl = detect_swing_lows(df, lb, rb)
            bos = detect_bos(df, sh, sl)
            choch = detect_choch(df, sh, sl)
            chart.add_bos_labels(bos)
            chart.add_choch_labels(choch)

            poi_engine = POIEngine(symbol)
            bull_obs = poi_engine.find_bullish_ob(df, config.ob_strength_min)
            bear_obs = poi_engine.find_bearish_ob(df, config.ob_strength_min)
            chart.add_order_blocks(bull_obs + bear_obs)

            fvgs = poi_engine.find_fvg(df, config.fvg_min_size_pips)
            chart.add_fvgs(fvgs)

            liq = LiquidityEngine(symbol)
            eqh = liq.find_eqh(df, sh, config.eqh_eql_tolerance_pips)
            eql = liq.find_eql(df, sl, config.eqh_eql_tolerance_pips)
            chart.add_eqh_eql(eqh, eql)

            if timeframe in ["M5", "M15", "H1"]:
                df_h1 = fetcher.get_ohlcv(mt5_symbol, "H1", bars=50)
                if df_h1 is not None:
                    asian = liq.get_asian_range(df_h1)
                    chart.add_asian_range(asian)

            signals = bot_state.get("signals", {})
            sig = signals.get(symbol)
            if sig:
                chart.add_entry_lines(
                    entry=sig.get("entry_price"),
                    sl=sig.get("sl_price"),
                    tp=sig.get("tp_price"),
                )

            return chart.get_figure()
        except Exception as exc:
            logger.error(f"Chart update error: {exc}")
            return build_empty_chart(dark_mode)

    # -- Positions Table ------------------------------------------
    @app.callback(
        Output("positions-table", "children"),
        Input("interval-fast", "n_intervals"),
    )
    def update_positions(_n):
        order_mgr = bot_state.get("order_manager")
        connector = bot_state.get("connector")
        if not order_mgr or not connector or not connector.connected:
            return html.P("Not connected", style={"fontSize": "13px", "opacity": "0.6"})

        try:
            positions = order_mgr.get_open_positions()
            if not positions:
                return html.P("No open positions", style={"fontSize": "13px", "opacity": "0.6"})

            rows = []
            for p in positions:
                direction = "BUY" if p.type == 0 else "SELL"
                pnl = p.profit
                pnl_color = "#198754" if pnl >= 0 else "#dc3545"

                risk = abs(p.price_open - p.sl) if p.sl > 0 else 0
                if risk > 0:
                    if direction == "BUY":
                        current_rr = (p.price_current - p.price_open) / risk
                    else:
                        current_rr = (p.price_open - p.price_current) / risk
                else:
                    current_rr = 0

                rows.append(html.Tr([
                    html.Td(p.symbol, style={"fontWeight": "600"}),
                    html.Td(direction, style={"color": "#198754" if direction == "BUY" else "#dc3545",
                                               "fontWeight": "600"}),
                    html.Td(f"{p.price_open:.5f}"),
                    html.Td(f"{p.price_current:.5f}"),
                    html.Td(f"{p.sl:.5f}" if p.sl > 0 else "--"),
                    html.Td(f"{p.tp:.5f}" if p.tp > 0 else "--"),
                    html.Td(f"1:{current_rr:.1f}"),
                    html.Td(f"${pnl:.2f}", style={"color": pnl_color, "fontWeight": "700"}),
                ], style={"fontSize": "12px"}))

            return dbc.Table([
                html.Thead(html.Tr([
                    html.Th(h, style={"fontSize": "11px", "fontWeight": "600"})
                    for h in ["Symbol", "Dir", "Entry", "Current", "SL", "TP", "RR", "PnL"]
                ])),
                html.Tbody(rows),
            ], bordered=False, hover=True, size="sm", style={"marginBottom": "0"})

        except Exception as exc:
            return html.P(f"Error: {exc}", style={"color": "#dc3545", "fontSize": "12px"})

    # -- Signal Log -----------------------------------------------
    @app.callback(
        Output("trade-log", "children"),
        Input("interval-fast", "n_intervals"),
    )
    def update_log(_n):
        db = bot_state.get("db")
        if db is None:
            return []
        try:
            logs = db.get_recent_logs(limit=50)
            items = []
            for log in reversed(logs):
                msg = log.get("message", "")
                ts = log.get("created_at", "")[:19]
                if "T" in ts:
                    ts = ts.split("T")[1][:8]
                symbol = log.get("symbol", "")
                category = log.get("category", "")

                if category in ("signal", "trade") and "TRADE" in msg:
                    color = "#198754"
                    dot = "[+]"
                elif category == "signal" or "Signal" in msg:
                    color = "#198754"
                    dot = "[+]"
                elif "FAIL" in msg or category == "error":
                    color = "#dc3545"
                    dot = "[x]"
                elif "Step" in msg or "Waiting" in msg:
                    color = "#fd7e14"
                    dot = "[~]"
                else:
                    color = "inherit"
                    dot = "[ ]"

                items.append(html.Div([
                    html.Span(f"{dot} [{ts}] ", style={"opacity": "0.5"}),
                    html.Span(f"{symbol} | " if symbol else "",
                              style={"fontWeight": "600"}),
                    html.Span(msg, style={"color": color}),
                ], style={"marginBottom": "2px"}))

            return items if items else [
                html.P("No log entries yet", style={"opacity": "0.5"})
            ]
        except Exception:
            return []

    # -- Statistics -----------------------------------------------
    @app.callback(
        [Output("stat-trades", "children"),
         Output("stat-wins", "children"),
         Output("stat-winrate", "children"),
         Output("stat-pnl", "children"),
         Output("stat-pnl", "style"),
         Output("stat-best-rr", "children"),
         Output("stat-avg-rr", "children"),
         Output("stat-drawdown", "children"),
         Output("stat-week-trades", "children"),
         Output("stat-week-wl", "children"),
         Output("stat-week-pnl", "children"),
         Output("stat-week-pnl", "style")],
        Input("interval-slow", "n_intervals"),
    )
    def update_stats(_n):
        db = bot_state.get("db")
        risk_mgr = bot_state.get("risk_manager")
        config = bot_state.get("config")

        default_style = {"fontSize": "18px", "fontWeight": "700"}
        default = ("--", "--", "--", "--", default_style,
                    "--", "--", "--", "--", "--", "--", default_style)

        if db is None:
            return default
        try:
            day_stats = db.get_daily_stats()
            pnl = day_stats.get("total_pnl", 0)
            pnl_color = "#198754" if pnl >= 0 else "#dc3545"
            pnl_style = {"fontSize": "18px", "fontWeight": "700", "color": pnl_color}

            max_trades = config.max_trades_per_day if config else 2
            trades_text = f"{day_stats.get('total_trades', 0)}/{max_trades}"

            dd = "0%"
            if risk_mgr:
                daily = risk_mgr.get_daily_stats(db)
                dd = f"{daily.get('drawdown', 0):.1f}%"

            week_stats = db.get_weekly_stats()
            week_pnl = week_stats.get("total_pnl", 0)
            week_pnl_color = "#198754" if week_pnl >= 0 else "#dc3545"
            week_pnl_style = {"fontSize": "18px", "fontWeight": "700", "color": week_pnl_color}

            return (
                trades_text,
                str(day_stats.get("wins", 0)),
                f"{day_stats.get('win_rate', 0):.0f}%",
                f"${pnl:+.2f}",
                pnl_style,
                f"1:{day_stats.get('best_rr', 0):.1f}",
                f"1:{day_stats.get('avg_rr', 0):.1f}",
                dd,
                str(week_stats.get("total_trades", 0)),
                f"W:{week_stats.get('wins', 0)} L:{week_stats.get('losses', 0)}",
                f"${week_pnl:+.2f}",
                week_pnl_style,
            )
        except Exception:
            return default

    # -- Signal Panel ---------------------------------------------
    @app.callback(
        Output("signal-content", "children"),
        Input("interval-slow", "n_intervals"),
    )
    def update_signals(_n):
        signals = bot_state.get("signals", {})
        acknowledged = bot_state.get("acknowledged_signals", {})
        config = bot_state.get("config")
        active = config.active_symbols if config else []
        if not active:
            return html.P("No instruments selected", style={"fontSize": "13px", "opacity": "0.6"})

        rows = []
        for sym in active:
            sig = signals.get(sym)
            if sig:
                signal_key = sig.get("signal_key") or (
                    f"{sig.get('direction', '')}_{float(sig.get('entry_price', 0.0)):.5f}"
                )
                acked = acknowledged.get(sym) == signal_key or bool(sig.get("acknowledged"))
                direction = str(sig.get("direction", "")).upper()
                direction_color = "#198754" if direction == "BUY" else "#dc3545"
                rows.append(html.Tr([
                    html.Td(sym, style={"fontWeight": "700"}),
                    html.Td(direction, style={"fontWeight": "700", "color": direction_color}),
                    html.Td(f"{float(sig.get('entry_price', 0.0)):.5f}"),
                    html.Td(f"1:{float(sig.get('rr_ratio', 0.0)):.1f}"),
                    html.Td(f"{float(sig.get('setup_score', 0.0)):.0f}"),
                    html.Td("✅" if acked else "⏳", style={"textAlign": "center"}),
                ], style={"fontSize": "12px"}))
            else:
                rows.append(html.Tr([
                    html.Td(sym),
                    html.Td("Scanning", colSpan=5, style={"opacity": "0.5"}),
                ], style={"fontSize": "12px"}))

        return dbc.Table([
            html.Thead(html.Tr([
                html.Th("Symbol"),
                html.Th("Dir"),
                html.Th("Entry"),
                html.Th("RR"),
                html.Th("Score"),
                html.Th("Acknowledged"),
            ], style={"fontSize": "11px"})),
            html.Tbody(rows),
        ], bordered=False, hover=True, size="sm", style={"marginBottom": "0"})

    # -- Settings Save --------------------------------------------
    @app.callback(
        Output("settings-status", "children"),
        Input("btn-save-settings", "n_clicks"),
        [State("set-risk", "value"),
         State("set-max-trades", "value"),
         State("set-drawdown", "value"),
         State("set-min-rr", "value"),
         State("set-setup-score", "value"),
         State("set-symbols", "value"),
         State("set-killzones", "value"),
         State("set-telegram-token", "value"),
         State("set-telegram-chat-id", "value"),
         State("set-scan-interval", "value")],
        prevent_initial_call=True,
    )
    def save_settings(n, risk, max_trades, dd, min_rr, setup_score, symbols,
                      kzs, telegram_token, telegram_chat_id, scan_interval):
        config = bot_state.get("config")
        db = bot_state.get("db")
        if config is None:
            return "Error: Config not loaded"

        config.risk_per_trade_pct = risk or 1.0
        config.max_trades_per_day = int(max_trades or 5)
        config.daily_drawdown_pct = dd if dd is not None else 3.0
        config.min_rr = min_rr or 1.5
        config.min_setup_score = setup_score if setup_score is not None else 55.0
        config.active_symbols = symbols or []
        config.trade_london_kz = "london" in (kzs or [])
        config.trade_ny_kz = "ny" in (kzs or [])
        config.trade_asian_kz = "asian" in (kzs or [])
        config.trade_london_close = "london_close" in (kzs or [])
        config.telegram_token = telegram_token or ""
        config.telegram_chat_id = str(telegram_chat_id or "")
        config.scan_interval_seconds = max(30, int(scan_interval or 30))

        bot_state["active_symbols"] = config.active_symbols.copy()
        scheduler = bot_state.get("scheduler")
        if scheduler:
            try:
                scheduler.reschedule_job(
                    "scanner",
                    trigger="interval",
                    seconds=config.scan_interval_seconds,
                )
            except Exception as exc:
                logger.error(f"Could not update scanner interval: {exc}")

        if config.telegram_token and config.telegram_chat_id and bot_state.get("notifier") is None:
            try:
                from telegram_notifier import TelegramNotifier

                notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
                notifier.bind_state(
                    bot_state.get("signals", {}),
                    bot_state.get("notified_signals", {}),
                    bot_state.get("acknowledged_signals", {}),
                )
                notifier.start_polling()
                bot_state["notifier"] = notifier
                logger.info("Telegram notifier initialized from dashboard settings")
            except Exception as exc:
                logger.error(f"Telegram notifier initialization failed: {exc}")
        elif not config.telegram_token or not config.telegram_chat_id:
            bot_state["notifier"] = None

        if db:
            db.save_settings(config.to_dict())

        logger.info("Settings saved from dashboard")
        return "Settings saved!"

    # -- Refresh Symbols from MT5 ---------------------------------
    @app.callback(
        Output("refresh-symbols-status", "children"),
        Input("btn-refresh-symbols", "n_clicks"),
        prevent_initial_call=True,
    )
    def refresh_symbols(_n):
        connector = bot_state.get("connector")
        config = bot_state.get("config")
        if not connector or not connector.connected or not config:
            return "Not connected to MT5"
        try:
            auto_symbols = connector.auto_load_symbols(
                categories=getattr(config, 'auto_load_categories', ['Forex', 'Metals', 'Indices']),
                max_symbols=getattr(config, 'auto_load_max_symbols', 20),
            )
            new_count = 0
            for base, mt5_name in auto_symbols.items():
                if base not in config.symbols:
                    config.symbols[base] = mt5_name
                    new_count += 1
                if base not in config.active_symbols:
                    config.active_symbols.append(base)
            verified = connector.verify_symbols(config.symbols)
            bot_state["verified_symbols"] = verified
            config.active_symbols = [s for s in config.active_symbols if verified.get(s, False)]
            bot_state["active_symbols"] = config.active_symbols.copy()
            return f"Found {len(auto_symbols)} symbols, {new_count} new. {len(config.active_symbols)} active."
        except Exception as exc:
            logger.error(f"Refresh symbols error: {exc}")
            return f"Error: {str(exc)[:50]}"

    # -- HTF Bias Badge -------------------------------------------
    @app.callback(
        Output("chart-bias-badge", "children"),
        [Input("chart-symbol", "value"),
         Input("interval-slow", "n_intervals")],
    )
    def update_bias(symbol, _n):
        connector = bot_state.get("connector")
        fetcher = bot_state.get("fetcher")
        config = bot_state.get("config")
        if not connector or not connector.connected or not fetcher or not symbol or not config:
            return ""
        try:
            mt5_symbol = config.get_mt5_symbol(symbol)
            data = fetcher.get_multi_timeframe(mt5_symbol)
            from core.market_structure import get_market_bias
            bias, confidence, score = get_market_bias(
                data.get("W1"), data.get("D1"), data.get("H4"),
                min_bias_score=getattr(config, "min_bias_score", 4),
                strict=getattr(config, "strict_pivot_bias", False)
            )
            color = "#198754" if bias == "bullish" else "#dc3545" if bias == "bearish" else "inherit"
            return html.Span([
                html.Span("HTF Bias: ", style={"fontSize": "12px", "opacity": "0.6"}),
                html.Span(f"{bias.upper()} ", style={
                    "color": color, "fontWeight": "700", "fontSize": "13px",
                }),
                html.Span(f"({confidence})", style={"fontSize": "11px", "opacity": "0.5"}),
            ])
        except Exception:
            return ""
