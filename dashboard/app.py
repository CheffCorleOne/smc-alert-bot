"""
Main Dash Application -- initializes and configures the dashboard server.

Creates the Dash app, registers callbacks, and exposes start_server()
for the main entry point to launch in a separate thread.

Theme toggling: Both DARKLY and FLATLY CSS are loaded;
a clientside callback toggles which one is active.
"""

import threading
from typing import Dict, Any

import dash
import dash_bootstrap_components as dbc

from dashboard.layout import build_layout
from dashboard.callbacks import register_callbacks
from utils.logger import get_logger

logger = get_logger("dashboard")

# Both themes are loaded via the HTML template; toggling is handled clientside
DARKLY_URL = "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/darkly/bootstrap.min.css"
FLATLY_URL = "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/flatly/bootstrap.min.css"
FONTS_URL = "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap"


def create_app(bot_state: Dict[str, Any]) -> dash.Dash:
    """
    Create and configure the Dash application.

    Args:
        bot_state: Shared state dict for bot <-> dashboard communication.

    Returns:
        Configured Dash app instance.
    """
    app = dash.Dash(
        __name__,
        external_stylesheets=[FONTS_URL],
        title="SMC Bot -- Signal Notifier",
        update_title=None,
        suppress_callback_exceptions=True,
    )

    # Custom index_string: load both themes, dark enabled by default, light disabled
    app.index_string = f'''
    <!DOCTYPE html>
    <html>
        <head>
            {{%metas%}}
            <title>{{%title%}}</title>
            {{%favicon%}}
            {{%css%}}
            <link id="theme-dark" rel="stylesheet" href="{DARKLY_URL}">
            <link id="theme-light" rel="stylesheet" href="{FLATLY_URL}" disabled>
        </head>
        <body>
            {{%app_entry%}}
            <footer>
                {{%config%}}
                {{%scripts%}}
                {{%renderer%}}
            </footer>
        </body>
    </html>
    '''

    # Build layout dynamically on load
    def serve_layout():
        config = bot_state.get("config")
        active_syms = None
        if config and hasattr(config, "active_symbols") and config.active_symbols:
            active_syms = config.active_symbols
        elif "active_symbols" in bot_state and bot_state["active_symbols"]:
            active_syms = bot_state["active_symbols"]
        return build_layout(active_symbols=active_syms, config=config)

    app.layout = serve_layout
    register_callbacks(app, bot_state)

    logger.info("Dashboard app created")
    return app


def start_server(
    app: dash.Dash,
    port: int = 8050,
    debug: bool = False,
) -> threading.Thread:
    """
    Start the Dash server in a background thread.

    Args:
        app: The Dash app instance.
        port: Port number (default 8050).
        debug: Enable debug mode.

    Returns:
        The server thread.
    """
    def run():
        app.run(
            host="0.0.0.0",
            port=port,
            debug=debug,
            use_reloader=False,
        )

    thread = threading.Thread(target=run, daemon=True, name="dash-server")
    thread.start()
    logger.info(f"Dashboard server started on http://localhost:{port}")
    return thread
