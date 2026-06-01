"""
Dashboard Layout -- Dash HTML structure.

Adaptive theme with toggle switch (Dark/Light).
Bootstrap grid layout:
- Header bar: theme toggle, connection status, account, session, clock, controls
- Left sidebar: dynamic instrument checklist + per-symbol analysis progress
- Center: candlestick chart with SMC overlays
- Right: signal panel + statistics
- Bottom: positions table, trade log
- Settings page with ? tooltips + new Symbol Management section
"""

import dash_bootstrap_components as dbc
from dash import html, dcc


DEFAULT_INSTRUMENTS = ["EURUSD", "GBPUSD", "GBPJPY", "USDJPY", "XAUUSD", "NAS100", "EURGBP", "AUDUSD"]
TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"]


def _tooltip(target_id: str, text: str) -> dbc.Tooltip:
    """Create a Bootstrap tooltip for a target element."""
    return dbc.Tooltip(text, target=target_id, placement="top")


def _label_with_help(label_text: str, help_id: str, tooltip_text: str):
    """Create a label with a ? icon that has a tooltip."""
    return html.Div([
        html.Label(label_text, style={
            "fontSize": "13px", "fontWeight": "600", "marginRight": "6px",
        }),
        html.Span("?", id=help_id, style={
            "display": "inline-flex", "alignItems": "center",
            "justifyContent": "center",
            "width": "18px", "height": "18px",
            "borderRadius": "50%", "backgroundColor": "rgba(128,128,128,0.2)",
            "fontSize": "11px", "fontWeight": "700",
            "cursor": "help", "verticalAlign": "middle",
        }),
        _tooltip(help_id, tooltip_text),
    ], style={"display": "flex", "alignItems": "center", "marginBottom": "4px"})


def build_header() -> dbc.Navbar:
    """Build the top header bar with theme toggle."""
    return dbc.Navbar(
        dbc.Container([
            dbc.Row([
                # Logo
                dbc.Col([
                    html.Span("SMC Bot", style={
                        "fontSize": "20px", "fontWeight": "700",
                        "letterSpacing": "1px",
                    }),
                ], width="auto"),

                # Theme toggle
                dbc.Col([
                    html.Div([
                        html.Span("Dark", style={
                            "fontSize": "11px", "marginRight": "6px",
                            "opacity": "0.7",
                        }),
                        dbc.Switch(
                            id="theme-toggle",
                            value=False,  # False = dark (default)
                            style={"display": "inline-block"},
                        ),
                        html.Span("Light", style={
                            "fontSize": "11px", "marginLeft": "2px",
                            "opacity": "0.7",
                        }),
                    ], style={"display": "flex", "alignItems": "center"}),
                ], width="auto"),

                # Connection + Broker
                dbc.Col([
                    html.Div(id="connection-status", children=[
                        html.Span("Disconnected", style={
                            "color": "#ff6b6b", "fontSize": "13px",
                        }),
                    ]),
                ], width="auto"),

                # Symbol count
                dbc.Col([
                    html.Span(id="symbols-count", children="Symbols: --",
                              style={"fontSize": "12px", "opacity": "0.7"}),
                ], width="auto"),

                # Session
                dbc.Col([
                    html.Span("--", id="session-text",
                              style={"fontSize": "13px", "opacity": "0.8"}),
                ], width="auto"),

                # UTC clock
                dbc.Col([
                    html.Div(id="utc-clock", children="00:00:00 UTC",
                             style={"fontSize": "14px", "fontFamily": "monospace"}),
                ], width="auto"),

                # Account info
                dbc.Col([
                    html.Div(id="header-account", children="",
                             style={"fontSize": "12px", "opacity": "0.7"}),
                ], width="auto"),

                # Buttons
                dbc.Col([
                    dbc.ButtonGroup([
                        dbc.Button("Start Bot", id="btn-start",
                                   color="success", size="sm",
                                   style={"fontWeight": "600"}),
                        dbc.Button("Stop Bot", id="btn-stop",
                                   color="danger", size="sm",
                                   style={"fontWeight": "600"}),
                    ]),
                ], width="auto"),
            ], align="center", className="g-3 w-100 justify-content-between"),
        ], fluid=True),
        color="dark",
        dark=True,
        style={"padding": "10px 0"},
    )


def build_symbols_sidebar(active_symbols=None, config=None) -> html.Div:
    """Build the left sidebar with per-symbol analysis status (dynamic)."""
    if active_symbols is None:
        active_symbols = DEFAULT_INSTRUMENTS

    return dbc.Card([
        dbc.CardBody([
            # Broker indicator
            html.Div(id="broker-badge", children=[
                html.Span("Broker: ", style={
                    "fontSize": "11px", "opacity": "0.6",
                }),
                html.Span("--", id="broker-name-text", style={
                    "fontSize": "11px", "fontWeight": "700",
                }),
            ], style={"marginBottom": "8px"}),

            html.H6("Active Instruments", className="mb-2", style={"fontWeight": "700"}),
            html.Div(
                id="sidebar-active-symbols",
                children=[
                    dbc.Badge(s, color="success", className="me-1 mb-1", style={"fontSize": "11px"})
                    for s in active_symbols
                ],
                style={"marginBottom": "12px", "display": "flex", "flexWrap": "wrap"},
            ),
            html.Hr(style={"margin": "10px 0", "opacity": "0.3"}),
            html.H6("Analysis Status", className="mb-2",
                     style={"fontSize": "13px", "fontWeight": "700"}),
            # Dynamic container — populated by callback
            html.Div(id="symbol-status-container", children=[
                html.P("Waiting for scan...", style={
                    "fontSize": "11px", "opacity": "0.5", "fontStyle": "italic",
                }),
            ]),
            html.Hr(style={"margin": "10px 0", "opacity": "0.3"}),
            html.H6("🧠 Bot Thinking", className="mb-2",
                     style={"fontSize": "13px", "fontWeight": "700"}),
            html.Div(id="intent-panel", children=[
                html.P("No pending setups", style={
                    "fontSize": "11px", "opacity": "0.5", "fontStyle": "italic",
                }),
            ]),
        ]),
    ], className="mb-2")


def build_signal_panel() -> dbc.Card:
    """Build the signal analysis panel."""
    return dbc.Card([
        dbc.CardBody([
            html.H6("Signal Analysis", className="mb-2",
                     style={"fontWeight": "700"}),
            html.Div(id="signal-content", children=[
                html.P("No active signals", style={
                    "fontSize": "13px", "opacity": "0.6",
                }),
            ]),
        ]),
    ], className="mb-2")


def build_chart_area(active_symbols=None) -> dbc.Card:
    """Build the main chart area."""
    if active_symbols is None:
        active_symbols = DEFAULT_INSTRUMENTS

    return dbc.Card([
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.Select(
                        id="chart-symbol",
                        options=[{"label": s, "value": s} for s in active_symbols],
                        value=active_symbols[0] if active_symbols else "EURUSD",
                        style={"fontSize": "13px"},
                    ),
                ], width=2),
                dbc.Col([
                    dbc.RadioItems(
                        id="tf-selector",
                        options=[{"label": f" {tf}", "value": tf} for tf in TIMEFRAMES],
                        value="M15",
                        inline=True,
                        style={"fontSize": "12px", "paddingTop": "6px"},
                        input_style={"marginRight": "4px"},
                        label_style={"marginRight": "12px"},
                    ),
                ], width=6),
                dbc.Col([
                    html.Div(id="chart-bias-badge",
                             style={"fontSize": "13px", "paddingTop": "6px",
                                    "textAlign": "right"}),
                ], width=4),
            ], className="mb-2"),
            dcc.Graph(
                id="main-chart",
                config={
                    "displayModeBar": True,
                    "scrollZoom": True,
                    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
                },
                style={"height": "500px"},
            ),
        ]),
    ], className="mb-2")


def build_positions_table() -> dbc.Card:
    """Build the active positions table."""
    return dbc.Card([
        dbc.CardBody([
            html.H6("Active Positions", className="mb-2",
                     style={"fontWeight": "700"}),
            html.Div(id="positions-table", children=[
                html.P("No open positions", style={
                    "fontSize": "13px", "opacity": "0.6",
                }),
            ]),
        ]),
    ], className="mb-2")


def build_trade_log() -> dbc.Card:
    """Build the signal log panel."""
    return dbc.Card([
        dbc.CardBody([
            html.H6("Signal Log", className="mb-2", style={"fontWeight": "700"}),
            html.Div(
                id="trade-log",
                children=[],
                style={
                    "maxHeight": "200px", "overflowY": "auto",
                    "fontSize": "12px", "fontFamily": "monospace",
                },
            ),
        ]),
    ], className="mb-2")


def build_statistics() -> dbc.Card:
    """Build the statistics panel."""
    def stat_card(label, val_id):
        return html.Div([
            html.Div(label, style={
                "fontSize": "11px", "fontWeight": "500", "opacity": "0.6",
            }),
            html.Div(id=val_id, children="--",
                     style={"fontSize": "18px", "fontWeight": "700"}),
        ], style={"textAlign": "center", "padding": "8px"})

    return dbc.Card([
        dbc.CardBody([
            html.H6("Statistics", className="mb-2", style={"fontWeight": "700"}),
            html.Div("Today:", className="mb-1",
                     style={"fontSize": "12px", "fontWeight": "600"}),
            dbc.Row([
                dbc.Col(stat_card("Trades", "stat-trades"), width=3),
                dbc.Col(stat_card("Wins", "stat-wins"), width=3),
                dbc.Col(stat_card("Win Rate", "stat-winrate"), width=3),
                dbc.Col(stat_card("PnL", "stat-pnl"), width=3),
            ]),
            dbc.Row([
                dbc.Col(stat_card("Best RR", "stat-best-rr"), width=4),
                dbc.Col(stat_card("Avg RR", "stat-avg-rr"), width=4),
                dbc.Col(stat_card("Drawdown", "stat-drawdown"), width=4),
            ]),
            html.Hr(style={"margin": "8px 0", "opacity": "0.3"}),
            html.Div("This Week:", className="mb-1",
                     style={"fontSize": "12px", "fontWeight": "600"}),
            dbc.Row([
                dbc.Col(stat_card("Trades", "stat-week-trades"), width=4),
                dbc.Col(stat_card("W/L", "stat-week-wl"), width=4),
                dbc.Col(stat_card("PnL", "stat-week-pnl"), width=4),
            ]),
            html.Hr(style={"margin": "8px 0", "opacity": "0.3"}),
            dbc.Button(
                "Reset Daily Stats",
                id="btn-reset-stats",
                color="warning",
                size="sm",
                className="w-100",
                style={"fontWeight": "600"}
            ),
            html.Div(id="reset-stats-status", style={
                "fontSize": "11px", "color": "#198754", "textAlign": "center", "marginTop": "4px", "fontWeight": "600"
            }),
        ]),
    ], className="mb-2")


def build_settings_tab(active_symbols=None, config=None) -> dbc.Card:
    """Build the settings configuration page with ? tooltips."""
    if active_symbols is None:
        active_symbols = DEFAULT_INSTRUMENTS

    field_style = {"marginBottom": "18px"}
    input_style = {"fontSize": "13px", "width": "160px"}
    risk_val = config.risk_per_trade_pct if config else 1.0
    max_trades_val = config.max_trades_per_day if config else 5
    drawdown_val = config.daily_drawdown_pct if config else 3.0
    min_rr_val = config.min_rr if config else 1.5
    setup_score_val = config.min_setup_score if config else 55.0
    telegram_token_val = getattr(config, "telegram_token", "") if config else ""
    telegram_chat_id_val = getattr(config, "telegram_chat_id", "") if config else ""
    scan_interval_val = getattr(config, "scan_interval_seconds", 30) if config else 30
    all_symbols = list(config.symbols.keys()) if config else active_symbols.copy()
    if not all_symbols:
        all_symbols = active_symbols.copy()

    kz_values = []
    if config:
        if config.trade_london_kz:
            kz_values.append("london")
        if config.trade_ny_kz:
            kz_values.append("ny")
        if config.trade_asian_kz:
            kz_values.append("asian")
        if config.trade_london_close:
            kz_values.append("london_close")
    else:
        kz_values = ["london", "ny", "asian", "london_close"]

    return dbc.Card([
        dbc.CardBody([
            html.H5("Settings Configuration", className="mb-3", style={"fontWeight": "700"}),
            dbc.Row([
                dbc.Col([
                    html.Div([
                        _label_with_help(
                            "Active Symbols", "help-symbols",
                            "Select which symbols the notifier scans for qualifying SMC signals."
                        ),
                        dbc.Checklist(
                            id="set-symbols",
                            options=[{"label": f" {s}", "value": s} for s in all_symbols],
                            value=active_symbols.copy(),
                            style={"fontSize": "13px"},
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Min Setup Score", "help-setup-score",
                            "Minimum quality score required before a Telegram signal is sent."
                        ),
                        dcc.Slider(
                            id="set-setup-score",
                            min=0, max=100, step=5, value=setup_score_val,
                            marks={0: "0", 50: "50", 75: "75", 100: "100"},
                            tooltip={"placement": "top"},
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Minimum RR Ratio", "help-min-rr",
                            "Minimum reward-to-risk ratio a setup must offer."
                        ),
                        dbc.Input(
                            id="set-min-rr", type="number", min=1.0, step=0.1,
                            value=min_rr_val, style=input_style,
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Risk per Trade (%)", "help-risk",
                            "Risk percentage used to estimate advisory lot size."
                        ),
                        dbc.Input(
                            id="set-risk", type="number", min=0.1, step=0.1,
                            value=risk_val, style=input_style,
                        ),
                    ], style=field_style),
                ], md=6),
                dbc.Col([
                    html.Div([
                        _label_with_help(
                            "Max Daily Trades", "help-max-trades",
                            "Maximum daily signals counted before scans pause for the day."
                        ),
                        dbc.Input(
                            id="set-max-trades", type="number", min=1, step=1,
                            value=max_trades_val, style=input_style,
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Daily Drawdown Limit (%)", "help-drawdown",
                            "Maximum daily drawdown percentage before scans pause."
                        ),
                        dbc.Input(
                            id="set-drawdown", type="number", min=0, step=0.5,
                            value=drawdown_val, style=input_style,
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Session Filters", "help-killzones",
                            "Sessions where qualifying SMC signals may be sent."
                        ),
                        dbc.Checklist(
                            id="set-killzones",
                            options=[
                                {"label": " London KZ", "value": "london"},
                                {"label": " New York KZ", "value": "ny"},
                                {"label": " Asian KZ", "value": "asian"},
                                {"label": " London Close", "value": "london_close"},
                            ],
                            value=kz_values,
                            style={"fontSize": "13px"},
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Telegram Token", "help-telegram-token",
                            "Bot token used to send Telegram signal alerts."
                        ),
                        dbc.Input(
                            id="set-telegram-token", type="password",
                            value=telegram_token_val, style={"fontSize": "13px"},
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Telegram Chat ID", "help-telegram-chat-id",
                            "Chat ID that receives signal alerts and acknowledgement buttons."
                        ),
                        dbc.Input(
                            id="set-telegram-chat-id", type="text",
                            value=telegram_chat_id_val, style={"fontSize": "13px"},
                        ),
                    ], style=field_style),
                    html.Div([
                        _label_with_help(
                            "Scan Interval (seconds)", "help-scan-interval",
                            "Seconds between market scans. Default: 30."
                        ),
                        dbc.Input(
                            id="set-scan-interval", type="number", min=30, step=30,
                            value=scan_interval_val, style=input_style,
                        ),
                    ], style=field_style),
                ], md=6),
            ]),
            dbc.Button(
                "Save Settings", id="btn-save-settings",
                color="primary", className="w-100 mt-2",
                style={"fontWeight": "700"},
            ),
            html.Div(id="settings-status", style={
                "fontSize": "12px", "marginTop": "8px",
                "textAlign": "center", "fontWeight": "600",
            }),
        ]),
    ])


def build_layout(active_symbols=None, config=None) -> html.Div:
    """Build the complete dashboard layout."""
    if active_symbols is None:
        active_symbols = DEFAULT_INSTRUMENTS

    return html.Div([
        # Intervals for auto-refresh
        dcc.Interval(id="interval-fast", interval=5_000, n_intervals=0),
        dcc.Interval(id="interval-slow", interval=30_000, n_intervals=0),
        dcc.Interval(id="interval-clock", interval=1_000, n_intervals=0),

        # Stores
        dcc.Store(id="store-bot-running", data=False),
        dcc.Store(id="store-signals", data={}),
        dcc.Store(id="store-theme", data="dark", storage_type="local"),
        dcc.Store(id="store-active-symbols", data=active_symbols),

        # Header
        build_header(),

        # Main content
        dbc.Container([
            dbc.Tabs([
                dbc.Tab(label="Trading", tab_id="tab-trading", children=[
                    dbc.Row([
                        dbc.Col([
                            build_symbols_sidebar(active_symbols, config),
                        ], md=2, style={"paddingRight": "5px"}),
                        dbc.Col([
                            build_chart_area(active_symbols),
                            build_positions_table(),
                        ], md=7, style={"padding": "0 5px"}),
                        dbc.Col([
                            build_signal_panel(),
                            build_statistics(),
                        ], md=3, style={"paddingLeft": "5px"}),
                    ], className="mt-3"),
                    dbc.Row([
                        dbc.Col(build_trade_log(), md=12),
                    ]),
                ]),
                dbc.Tab(label="Settings", tab_id="tab-settings", children=[
                    dbc.Row([
                        dbc.Col(build_settings_tab(active_symbols, config), md=12),
                    ], className="mt-3"),
                ]),
            ], id="tabs", active_tab="tab-trading",
               style={"marginTop": "10px"}),
        ], fluid=True),
    ], style={
        "minHeight": "100vh",
        "fontFamily": "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
    })
