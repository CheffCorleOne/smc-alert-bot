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

## Disclaimer

This project is for education and research. Trading involves substantial risk of loss. Test on a demo account first, verify broker symbol settings, and make all trading decisions yourself.
