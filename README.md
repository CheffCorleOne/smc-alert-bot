# SMC Signal Notifier

Smart Money Concepts (ICT/SMC) signal notifier for MetaTrader 5.

The bot connects to a running MT5 terminal, scans selected instruments with the existing SMC analysis engine, and sends qualified trade ideas to Telegram. It does not place, close, or modify orders. Order execution is manual.

## What It Does

- Connects to MetaTrader 5 for market data, account info, and open-position stats.
- Runs the existing 9-step SMC analysis pipeline.
- Keeps PendingIntent memory for partially formed setups.
- Filters setups by enabled killzones, minimum setup score, minimum RR, daily limits, and session rules.
- Calculates an advisory lot size from account balance, SL distance, and configured risk percent.
- Sends Telegram messages with entry, SL, TP, RR, score, session, lot size, and estimated hold time.
- Lets you mark a signal as handled with the Telegram inline button `Order Placed`.
- Shows active signals and acknowledgement status in the Dash dashboard.

## What It Does Not Do

- It does not place market orders.
- It does not place limit orders.
- It does not close positions.
- It does not partial close.
- It does not trail stop loss.
- It does not need MT5 Algo Trading enabled for execution.

## Requirements

- Windows 10/11
- Python 3.11+
- MetaTrader 5 installed, open, and logged in
- A Telegram bot token from BotFather
- A Telegram chat ID for the chat that should receive signals

Install dependencies:

```powershell
cd "C:\Users\temir\Desktop\SMC ALERT BOT"
py -m pip install -r requirements.txt
```

Run:

```powershell
py main.py
```

Open the dashboard:

```text
http://localhost:8050
```

## First-Time Setup

1. Start MetaTrader 5 and log in.
2. Make sure the symbols you want are visible in Market Watch.
3. Start the bot with `py main.py`.
4. Open `http://localhost:8050`.
5. Go to `Settings`.
6. Configure:
   - Active Symbols
   - Min Setup Score
   - Minimum RR Ratio
   - Risk per Trade %
   - Max Daily Trades
   - Daily Drawdown Limit %
   - Session Filters
   - Telegram Token
   - Telegram Chat ID
   - Scan Interval
7. Click `Save Settings`.

The bot starts scanning automatically after launch. The `Start Bot` button is only needed if you previously clicked `Stop Bot`.

## Telegram Setup

Create a bot:

1. Open `@BotFather` in Telegram.
2. Run `/newbot`.
3. Copy the token.
4. Send any message to your new bot.
5. Open this URL in a browser, replacing `TOKEN`:

```text
https://api.telegram.org/botTOKEN/getUpdates
```

Find:

```json
"chat":{"id":123456789}
```

Use that number as `Telegram Chat ID`.

Never commit your Telegram token to GitHub.

## Configuration & Secrets (.env)

Telegram credentials can be supplied via environment variables instead of the
dashboard/database — this is the recommended way to keep secrets out of
`data/bot_data.db`. Copy `.env.example` to `.env` and fill it in:

```text
TELEGRAM_TOKEN=123456:abc...
TELEGRAM_CHAT_ID=123456789
```

`.env` is git-ignored. Values from the environment take precedence over anything
saved in the database.

## How To Know It Is Working

In the terminal you should see:

```text
MT5 connection established
Dashboard server started on http://localhost:8050
Scheduler started (scanner: 30s)
Market Signal Scan Start
Scan complete
```

In the dashboard:

- Connection should show `Connected`.
- Active symbols should be listed.
- `Analysis Status` should update every scan.
- `Signal Analysis` should show active signals when they exist.

In Telegram:

```text
/status
```

If Telegram is connected, the bot replies with active signals or `No active signals.`

## Analysis Pipeline

1. HTF bias: W1, D1, and H4 directional context.
2. Liquidity target: Asian range, EQH/EQL, or HTF pools.
3. Sweep: valid liquidity sweep confirmation.
4. Structure shift: CHoCH/BOS after sweep.
5. POI: OB/FVG/breaker/mitigation zone.
6. Session filter: enabled killzones and Silver Bullet logic.
7. Confirmation or intent: POI tap, confirmation, or PendingIntent continuation.
8. Risk/reward: structural SL and structural TP, with minimum RR check.
9. Signal: log setup, calculate advisory lot size, and notify Telegram.

There is no maximum RR cap. RR is calculated from the structural entry, stop loss, and take profit found during analysis.

### Analysis details

- **Closed bars only.** The fetcher drops the current forming candle, so signals are evaluated on completed bars and do not repaint within a candle.
- **DST-aware killzones.** Sessions are defined in New York local time (`America/New_York`) and shift automatically with US daylight saving — they are not hard-coded UTC windows.
- **Quality scoring.** `setup_score` is a weighted blend of HTF-bias strength, structure-shift type (MSS > CHoCH > BOS), POI tightness vs ATR, sweep displacement, OTE location, and reward:risk — so `Min Setup Score` actually discriminates between setups.
- **Premium/Discount** is measured from the current working swing leg (most recent swing high/low), not the all-time extremes of the lookback.

## Dashboard Settings

Visible settings are intentionally limited to signal-advisor controls:

- Active Symbols
- Min Setup Score
- Minimum RR Ratio
- Risk per Trade %
- Max Daily Trades
- Daily Drawdown Limit %
- Session Filters
- Telegram Token
- Telegram Chat ID
- Scan Interval

Advanced analysis parameters still live in `config.py`, but they are not exposed in the simplified dashboard.

## Local Data And Secrets

Runtime settings are stored locally in:

```text
data/bot_data.db
```

This file can contain Telegram credentials. It is ignored by `.gitignore` and should not be committed to GitHub.

On a new laptop, the bot creates a fresh `data/bot_data.db` automatically on first run. You will need to enter dashboard settings again.

## Move To Another Laptop

1. Copy or clone the project.
2. Install Python and MetaTrader 5.
3. Install dependencies:

```powershell
py -m pip install -r requirements.txt
```

4. Start MT5 and log in.
5. Run:

```powershell
py main.py
```

6. Open the dashboard and enter the same Telegram token and chat ID.

Use only one running copy at a time if you want to avoid duplicate Telegram signals.

## Project Structure

```text
SMC ALERT BOT/
|-- main.py                    # Entry point, scheduler, scanner, Telegram integration
|-- config.py                  # BotConfig, symbol mapping, pip-size helpers
|-- telegram_notifier.py       # Telegram signal sender, polling, acknowledgements
|-- core/
|   |-- entry_engine.py        # 9-step SMC analysis pipeline
|   |-- pending_intent.py      # Setup memory and phase tracking
|   |-- market_structure.py    # Swing, BOS, CHoCH, structure helpers
|   |-- liquidity.py           # Liquidity pools, sweeps, EQH/EQL
|   |-- poi.py                 # OB, FVG, breaker, mitigation detection
|   |-- sessions.py            # Killzone/session logic
|-- broker/
|   |-- mt5_client.py          # Single lazy MetaTrader5 accessor (get_mt5)
|   |-- connector.py           # MT5 connection and symbol discovery
|   |-- order_manager.py       # Read-only exposure/history helpers
|   |-- risk_manager.py        # Lot sizing and daily statistics/limits
|-- dashboard/
|   |-- app.py                 # Dash app creation
|   |-- layout.py              # Dashboard layout and settings controls
|   |-- callbacks.py           # Dashboard callbacks
|   |-- charts.py              # Plotly chart rendering
|-- data/
|   |-- database.py            # Settings, logs, signal/trade history
|   |-- fetcher.py             # Market data fetching
|-- utils/
|   |-- logger.py              # Logging helpers
|   |-- news_filter.py         # News/event filter
|   |-- config_snapshot.py     # Runtime config logging
|   |-- indicators.py          # Shared ATR (single source of truth)
|   |-- symbols.py             # normalize_symbol + pip-size registry
|   |-- settings.py            # Env/.env secrets via pydantic-settings
|-- backtest/
|   |-- simulator.py           # Trade outcome + performance metrics
|   |-- runner.py              # Walk-forward Backtester (no look-ahead)
|   |-- run.py                 # CLI: py -m backtest.run SYMBOL
|-- tests/                     # pytest suite (run: py -m pytest)
|-- pyproject.toml             # ruff / mypy / pytest config
|-- requirements-dev.txt       # Dev/test tooling
|-- .env.example               # Template for Telegram secrets
```

## GitHub Safety Checklist

Before pushing:

```powershell
git status
```

Do not commit:

- `data/bot_data.db`
- `.env`
- `.venv/`
- `__pycache__/`
- log files
- Telegram tokens

If `data/bot_data.db` was added by mistake:

```powershell
git rm --cached data/bot_data.db
git commit -m "Remove local database from repo"
```

## Development

Install dev tooling and run the test suite:

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest          # ~99 tests, runs offline (MetaTrader5 is imported lazily)
py -m ruff check .    # linting (advisory)
```

The SMC detectors are pure functions over pandas DataFrames, so the test suite
runs without a live MT5 terminal.

## Backtesting

Replay the engine over historical MT5 data (terminal must be open & logged in):

```powershell
py -m backtest.run EURUSD --bars 50000 --cost-r 0.05
```

Useful flags:

- `--bars` — how many M5 bars of history to load.
- `--cost-r` — round-turn cost per trade in R (spread + commission + slippage).
- `--max-hold-bars` — cap a trade's holding window in M5 bars (default 288 = 1 day). Without a cap, far take-profits "win" months later and inflate the results.

The report includes win rate, expectancy (R), profit factor, max drawdown (R),
and max consecutive losses.

**Caveats:** a backtest does not guarantee future performance. The harness runs
with the killzone/session filter relaxed (the engine's session check reads the
wall clock, not the bar time), so results are optimistic versus the live bot,
and entry fills are assumed at the signal price. Treat it as a sanity check of
the logic, not a profit forecast.

## Disclaimer

This project is for education and research. Trading involves substantial risk of loss. Test on a demo account first, verify broker symbol settings, and make all trading decisions yourself.
