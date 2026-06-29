"""
Trade outcome simulation and performance metrics.

Given a signal (entry / SL / TP) and the future price path, decide whether the
trade hit its take-profit or stop-loss first, then aggregate results into the
standard metrics a trader cares about (win rate, expectancy in R, profit
factor, max consecutive losses).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

import pandas as pd


@dataclass
class TradeResult:
    direction: str
    entry: float
    sl: float
    tp: float
    rr: float
    outcome: str          # 'win' | 'loss' | 'timeout' | 'open'
    r_multiple: float     # +rr win, -1 loss, realised R on timeout, 0 if open


def simulate_trade(
    future: pd.DataFrame,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    rr: float,
    max_bars: int | None = None,
    cost_r: float = 0.0,
) -> TradeResult:
    """
    Walk the future bars and resolve the trade.

    Conservative tie-break: if a single bar touches BOTH the stop and the
    target, we assume the stop was hit first (worst case).

    Realism controls:
      - ``max_bars``: cap the holding window. Without it, a far structural TP
        will "win" if price EVER reaches it (even months later), which wildly
        inflates win rate and expectancy. When the cap is reached without an
        SL/TP touch, the trade is closed at that bar's close ('timeout') and
        its realised R is recorded.
      - ``cost_r``: spread/commission/slippage charged (in R) on every closed
        trade.
    """
    highs = future["high"].to_numpy()
    lows = future["low"].to_numpy()
    closes = future["close"].to_numpy() if "close" in future.columns else None
    risk = abs(entry - sl)

    n = len(highs)
    if max_bars is not None:
        n = min(n, max_bars)

    outcome = "open"
    r = 0.0
    for i in range(n):
        if direction == "buy":
            hit_sl = lows[i] <= sl
            hit_tp = highs[i] >= tp
        else:
            hit_sl = highs[i] >= sl
            hit_tp = lows[i] <= tp

        if hit_sl:
            outcome, r = "loss", -1.0
            break
        if hit_tp:
            outcome, r = "win", rr
            break
    else:
        # No SL/TP within the window -> close at the last bar's close.
        if closes is not None and n > 0 and risk > 0:
            exit_price = closes[n - 1]
            realised = (exit_price - entry) if direction == "buy" else (entry - exit_price)
            outcome, r = "timeout", realised / risk

    if outcome != "open":
        r -= cost_r

    return TradeResult(direction, entry, sl, tp, rr, outcome, r)


def compute_metrics(results: Iterable[TradeResult]) -> dict:
    """Aggregate TradeResults into summary performance metrics.

    A trade is 'closed' if it resolved (win/loss/timeout); 'open' trades are
    excluded. Win/loss are classified by realised R sign so timeouts count as
    whichever side they finished on.
    """
    results = list(results)
    closed: List[TradeResult] = [r for r in results if r.outcome != "open"]
    wins = [r for r in closed if r.r_multiple > 0]
    losses = [r for r in closed if r.r_multiple <= 0]

    gross_win = sum(r.r_multiple for r in wins)
    gross_loss = abs(sum(r.r_multiple for r in losses))
    total_r = sum(r.r_multiple for r in closed)

    # Longest losing streak and peak-to-trough drawdown over the R equity curve.
    max_consec = run = 0
    cum = peak = 0.0
    max_dd = 0.0
    for r in closed:
        if r.r_multiple <= 0:
            run += 1
            max_consec = max(max_consec, run)
        else:
            run = 0
        cum += r.r_multiple
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    n = len(closed)
    return {
        "signals": len(results),
        "trades": n,
        "open": len([r for r in results if r.outcome == "open"]),
        "timeouts": len([r for r in results if r.outcome == "timeout"]),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "expectancy_r": round(total_r / n, 3) if n else 0.0,
        "total_r": round(total_r, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf") if gross_win else 0.0,
        "avg_win_r": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss_r": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "largest_win_r": round(max((r.r_multiple for r in wins), default=0.0), 2),
        "max_drawdown_r": round(max_dd, 2),
        "max_consecutive_losses": max_consec,
    }
