"""Tests for the backtest simulator, metrics and walk-forward runner."""

from __future__ import annotations

import pandas as pd
from conftest import candles_from_closes

from backtest.runner import Backtester
from backtest.simulator import TradeResult, compute_metrics, simulate_trade


def _future(highs, lows, closes=None):
    data = {"high": highs, "low": lows}
    if closes is not None:
        data["close"] = closes
    return pd.DataFrame(data)


class TestSimulateTrade:
    def test_buy_win(self):
        fut = _future([1.01, 1.02, 1.05], [1.00, 1.00, 1.00])
        res = simulate_trade(fut, "buy", entry=1.00, sl=0.99, tp=1.04, rr=2.0)
        assert res.outcome == "win"
        assert res.r_multiple == 2.0

    def test_buy_loss(self):
        fut = _future([1.005, 1.006], [1.00, 0.985])
        res = simulate_trade(fut, "buy", entry=1.00, sl=0.99, tp=1.04, rr=2.0)
        assert res.outcome == "loss"
        assert res.r_multiple == -1.0

    def test_tie_break_is_loss(self):
        # One bar touches both SL and TP -> conservative loss.
        fut = _future([1.05], [0.98])
        res = simulate_trade(fut, "buy", entry=1.00, sl=0.99, tp=1.04, rr=2.0)
        assert res.outcome == "loss"

    def test_open_when_unresolved(self):
        fut = _future([1.01, 1.02], [1.00, 1.00])
        res = simulate_trade(fut, "buy", entry=1.00, sl=0.99, tp=1.10, rr=5.0)
        assert res.outcome == "open"
        assert res.r_multiple == 0.0

    def test_sell_win(self):
        fut = _future([1.00, 1.00], [0.99, 0.95])
        res = simulate_trade(fut, "sell", entry=1.00, sl=1.01, tp=0.96, rr=3.0)
        assert res.outcome == "win"

    def test_timeout_closes_at_last_bar(self):
        # Never reaches SL(0.99) or TP(1.10); capped at 3 bars, last close 1.01.
        fut = _future([1.01, 1.02, 1.015], [1.00, 1.00, 1.005], [1.005, 1.015, 1.01])
        res = simulate_trade(fut, "buy", entry=1.00, sl=0.99, tp=1.10, rr=10.0, max_bars=3)
        assert res.outcome == "timeout"
        # realised R = (1.01 - 1.00) / (1.00 - 0.99) = 1.0
        assert abs(res.r_multiple - 1.0) < 1e-9

    def test_far_tp_does_not_win_after_cap(self):
        # Price eventually hits TP but only after the holding cap -> timeout, not win.
        fut = _future(
            [1.01, 1.02, 1.50], [1.00, 1.00, 1.00], [1.01, 1.01, 1.50]
        )
        res = simulate_trade(fut, "buy", entry=1.00, sl=0.99, tp=1.40, rr=40.0, max_bars=2)
        assert res.outcome == "timeout"
        assert res.r_multiple < 40.0

    def test_cost_applied(self):
        fut = _future([1.05], [1.00], [1.05])
        res = simulate_trade(fut, "buy", entry=1.00, sl=0.99, tp=1.04, rr=4.0, cost_r=0.1)
        assert res.outcome == "win"
        assert abs(res.r_multiple - 3.9) < 1e-9


class TestMetrics:
    def test_aggregate(self):
        trades = [
            TradeResult("buy", 1, 0.99, 1.02, 2.0, "win", 2.0),
            TradeResult("buy", 1, 0.99, 1.02, 2.0, "loss", -1.0),
            TradeResult("buy", 1, 0.99, 1.02, 2.0, "loss", -1.0),
            TradeResult("buy", 1, 0.99, 1.02, 2.0, "win", 2.0),
            TradeResult("buy", 1, 0.99, 1.02, 2.0, "open", 0.0),
        ]
        m = compute_metrics(trades)
        assert m["signals"] == 5
        assert m["trades"] == 4
        assert m["wins"] == 2 and m["losses"] == 2
        assert m["win_rate"] == 50.0
        assert m["total_r"] == 2.0           # 2 -1 -1 2
        assert m["expectancy_r"] == 0.5
        assert m["profit_factor"] == 2.0     # 4 / 2
        assert m["max_consecutive_losses"] == 2
        # Equity curve R: +2, +1, 0, +2 -> peak 2, trough 0 -> max DD 2.
        assert m["max_drawdown_r"] == 2.0
        assert m["avg_win_r"] == 2.0
        assert m["avg_loss_r"] == -1.0
        assert m["largest_win_r"] == 2.0

    def test_empty(self):
        m = compute_metrics([])
        assert m["trades"] == 0
        assert m["win_rate"] == 0.0


class TestBacktesterSmoke:
    def test_runs_without_error(self):
        # Flat-ish synthetic history across all TFs; we only assert it executes
        # and returns a well-formed metrics dict (signals likely 0).
        closes = [1.0 + 0.001 * (i % 5) for i in range(160)]
        history = {
            tf: candles_from_closes(closes, freq_minutes=fm)
            for tf, fm in [
                ("W1", 10080), ("D1", 1440), ("H4", 240),
                ("H1", 60), ("M15", 15), ("M5", 5), ("M1", 1),
            ]
        }
        bt = Backtester("EURUSD", step=10, warmup=60)
        metrics = bt.run(history)
        assert "trades" in metrics and "win_rate" in metrics
        assert isinstance(metrics["_trades"], list)
