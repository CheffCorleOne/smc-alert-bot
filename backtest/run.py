"""
CLI entry point: backtest a symbol on live MT5 history.

Usage (MT5 terminal open & logged in):
    py -m backtest.run EURUSD --bars 5000 --step 3

Pulls multi-timeframe history from MT5, runs the walk-forward Backtester, and
prints the metrics. For offline/CI use, import Backtester directly and feed it
your own DataFrames.
"""

from __future__ import annotations

import argparse
import sys

from broker.connector import MT5Connector
from data.fetcher import MULTI_TF_FRAMES, MarketDataFetcher
from backtest.runner import Backtester


def main() -> int:
    parser = argparse.ArgumentParser(description="SMC strategy backtester")
    parser.add_argument("symbol", help="Base symbol, e.g. EURUSD")
    parser.add_argument("--bars", type=int, default=5000, help="M5 bars of history")
    parser.add_argument("--step", type=int, default=3, help="Evaluate every N M5 bars")
    parser.add_argument("--warmup", type=int, default=300, help="Warmup bars before testing")
    parser.add_argument(
        "--max-hold-bars", type=int, default=288,
        help="Max trade holding window in M5 bars (288 = 1 day). Caps far-TP inflation.",
    )
    parser.add_argument(
        "--cost-r", type=float, default=0.0,
        help="Round-turn cost per trade in R (spread+commission+slippage), e.g. 0.05",
    )
    args = parser.parse_args()

    # The fetcher talks to MT5 directly, so the terminal must be initialised
    # first (the live bot does this via the connector). Without it MT5 returns
    # '-10004 No IPC connection'.
    connector = MT5Connector()
    if not connector.connect():
        print(
            "Could not connect to MT5. Make sure the MetaTrader 5 terminal is "
            "running and logged in, then try again."
        )
        return 1

    try:
        # Resolve the broker-specific symbol name (e.g. EURUSD -> EURUSDm on
        # Libertex) and select it into Market Watch.
        symbol_map = {args.symbol: args.symbol}
        verified = connector.verify_symbols(symbol_map)
        if not verified.get(args.symbol):
            print(
                f"Symbol {args.symbol} not found at this broker. "
                f"Check it exists in Market Watch (tried name variations)."
            )
            return 1
        mt5_name = symbol_map[args.symbol]
        if mt5_name != args.symbol:
            print(f"Resolved {args.symbol} -> {mt5_name}")

        fetcher = MarketDataFetcher()
        history = {}
        for tf in MULTI_TF_FRAMES:
            # Lower TFs need many bars; HTFs need fewer.
            n = args.bars if tf in ("M1", "M5") else max(500, args.bars // 5)
            df = fetcher.get_ohlcv(mt5_name, tf, bars=n)
            if df is not None:
                history[tf] = df

        if "M5" not in history:
            print(f"No M5 data for {mt5_name}. Is the symbol in Market Watch?")
            return 1

        # Backtester is given the BASE symbol (for pip size / profile / sessions);
        # the price history is already resolved to the broker name above.
        bt = Backtester(
            args.symbol, step=args.step, warmup=args.warmup,
            max_hold_bars=args.max_hold_bars, cost_r=args.cost_r,
        )
        metrics = bt.run(history)
        metrics.pop("_trades", None)

        bars_loaded = {tf: len(df) for tf, df in history.items()}
        print(f"\n=== Backtest: {args.symbol} ({mt5_name}) ===")
        print(f"  bars loaded: {bars_loaded}")
        for k, v in metrics.items():
            print(f"  {k:>24}: {v}")
        return 0
    finally:
        connector.disconnect()


if __name__ == "__main__":
    sys.exit(main())
