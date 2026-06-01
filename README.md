# Institutional SMC Trading Bot

Automated Smart Money Concepts (ICT/SMC) trading bot for MetaTrader 5 with a real-time Dash dashboard, pending setup memory, structural risk checks, and automated position management.

The bot is designed for Windows with an open, logged-in MT5 terminal. The default broker profile targets Libertex-style MT5 symbols, but symbols can be mapped in `BotConfig`.

## Key Features

### Thinking Bot Architecture

The bot does not forget a setup just because one confirmation is missing. When HTF bias and liquidity context are present but price has not returned to the POI yet, it creates a `PendingIntent`. The dashboard shows the setup phase as it progresses from watching to ready, armed, and triggered.

### Structural SMC Execution

- HTF bias from W1, D1, and H4 structure.
- Liquidity targets from Asian range, equal highs/lows, and higher-timeframe pools.
- POI detection from order blocks, fair value gaps, breaker blocks, and mitigation blocks.
- Entry confirmation through CHoCH/BOS and optional limit-order intent entries.
- Stop loss is structural: beyond the POI boundary with an ATR buffer.
- If the structural SL is closer than the broker `stops_level`, the trade is rejected instead of widening the SL.
- Take profit must be structural. If no liquidity target exists, the engine searches H4/D1 swing structure; if no target is found, the trade is skipped.

### Risk And Position Management

- Lot size uses MT5 tick value/tick size and rounds down to the broker volume step.
- Original SL distance is stored in order comments as `|SLxx|` for later risk tracking.
- `partial_close_at_rr` is the point where the bot starts watching for weakness, not an automatic exit.
- Partial close only happens if the original idea weakens: M5/M15 protected swing break, opposite CHoCH, adverse M5 momentum after a pullback from max RR, or rejection near TP.
- Trailing stop activates only after partial close and uses H1 ATR distance (`trailing_stop_atr_mult * ATR`).
- Daily drawdown, max trades per day, max spread, and stale-position expiry are enforced.

### Dashboard

- Live Plotly charts with SMC overlays.
- Bot state, open positions, statistics, and logs.
- Settings for risk, RR, partial-close weakness rules, killzones, symbol selection, late entries, and symbol auto-loading.
- Manual daily stats reset without deleting history.

## Setup

Requirements:

- Windows 10/11
- Python 3.11+
- MetaTrader 5 installed, open, and logged in
- MT5 algorithmic trading enabled: `Tools -> Options -> Expert Advisors -> Allow algorithmic trading`

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the bot:

```bash
python main.py
```

Open the dashboard:

```text
http://localhost:8050
```

## Analysis Pipeline

1. HTF bias: determine directional context from W1, D1, and H4.
2. Liquidity target: locate higher-timeframe and session liquidity pools.
3. Liquidity sweep: confirm stops were swept.
4. Structure shift: require CHoCH/BOS confirmation.
5. POI entry zone: select OB/FVG/breaker/mitigation zones in premium or discount.
6. Session filter: enforce enabled killzones and Silver Bullet windows.
7. Confirmation or pending intent: wait for POI tap, confirmation, or eligible limit entry.
8. Risk/reward: calculate structural SL and structural TP, reject weak RR or broker-invalid stops.
9. Execution: check limits, calculate lot size, place order, and manage the position.

## Project Structure

```text
smc_bot/
|-- main.py                    # Entry point and scanner loop
|-- config.py                  # BotConfig, symbol mapping, pip-size helpers
|-- core/
|   |-- entry_engine.py        # 9-step SMC analysis pipeline
|   |-- pending_intent.py      # Setup memory and phase tracking
|   |-- market_structure.py    # Swing, BOS, CHoCH, structure helpers
|   |-- liquidity.py           # Liquidity pools, sweeps, EQH/EQL
|   |-- poi.py                 # OB, FVG, breaker, mitigation detection
|   |-- sessions.py            # Killzone/session logic
|-- broker/
|   |-- connector.py           # MT5 connection and symbol discovery
|   |-- order_manager.py       # Market/limit orders and trade modification
|   |-- risk_manager.py        # Lot sizing, partial close, ATR trailing
|-- dashboard/
|   |-- app.py                 # Dash app creation
|   |-- layout.py              # Dashboard layout and settings controls
|   |-- callbacks.py           # Dashboard callbacks
|   |-- charts.py              # Plotly chart rendering
|-- data/
|   |-- database.py            # Settings, logs, and trade history
|   |-- fetcher.py             # Market data fetching
|-- utils/
|   |-- logger.py              # Logging helpers
|   |-- news_filter.py         # News/event filter
|   |-- config_snapshot.py     # Runtime config logging
```

## Important Defaults

- `risk_per_trade_pct`: `1.0`
- `min_rr`: `2.0`
- `partial_close_at_rr`: `2.0` start watching for weakness
- `partial_close_pullback_rr`: `0.6`
- `partial_close_target_proximity_rr`: `0.5`
- `partial_close_pct`: `50.0`
- `trailing_stop_enabled`: `True`
- `trailing_stop_atr_mult`: `1.5`
- `position_max_age_hours`: `8.0`

## Disclaimer

This project is for education and research. Trading involves substantial risk of loss. Test on a demo account first, verify broker symbol settings, and do not assume past performance predicts future results.
