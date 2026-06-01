"""
SMC Chart Builder -- Plotly candlestick chart with SMC overlays.

Draws Order Blocks, FVGs, EQH/EQL lines, BOS/CHoCH labels,
Asian range, and entry/SL/TP lines on the candlestick chart.
Supports dark and light themes.
"""

from typing import List, Optional, Dict, Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

from core.poi import OrderBlock, FVG
from core.liquidity import EqualLevel
from core.market_structure import BOSEvent, CHoCHEvent


# -- Theme color sets ------------------------------------------
DARK_COLORS = {
    "bg": "#0d1117", "card": "#161b22", "grid": "#21262d",
    "text": "#8b949e", "green": "#00ff88", "red": "#ff4466",
    "blue": "#4488ff", "orange": "#ff9800", "cyan": "#00bcd4",
    "gray": "#484f58", "entry": "#ffffff",
    "ob_bull": "rgba(68,136,255,0.15)", "ob_bear": "rgba(255,68,102,0.15)",
    "fvg_bull": "rgba(0,255,136,0.1)", "fvg_bear": "rgba(255,68,102,0.1)",
    "asian_fill": "rgba(72,79,88,0.1)",
    "template": "plotly_dark", "tp2": "#00ff00",
}

LIGHT_COLORS = {
    "bg": "#ffffff", "card": "#fafafa", "grid": "#e9ecef",
    "text": "#495057", "green": "#198754", "red": "#dc3545",
    "blue": "#0d6efd", "orange": "#fd7e14", "cyan": "#0dcaf0",
    "gray": "#adb5bd", "entry": "#0d6efd",
    "ob_bull": "rgba(13,110,253,0.12)", "ob_bear": "rgba(220,53,69,0.12)",
    "fvg_bull": "rgba(25,135,84,0.1)", "fvg_bear": "rgba(220,53,69,0.1)",
    "asian_fill": "rgba(173,181,189,0.1)",
    "template": "plotly_white", "tp2": "#0a6b35",
}


class SMCChartBuilder:
    """
    Builds Plotly candlestick charts with SMC concept overlays.

    Supports adding:
    - Order Blocks (colored boxes)
    - Fair Value Gaps (shaded areas)
    - Equal Highs/Lows (dashed lines)
    - BOS / CHoCH labels
    - Asian range (gray shading)
    - Entry, SL, TP1, TP2 lines
    """

    def __init__(self, df: pd.DataFrame, symbol: str = "",
                 timeframe: str = "", dark_mode: bool = True) -> None:
        self.df = df
        self.symbol = symbol
        self.timeframe = timeframe
        self.c = DARK_COLORS if dark_mode else LIGHT_COLORS
        self.fig = self._create_base_chart()

    def _create_base_chart(self) -> go.Figure:
        """Create the base candlestick chart."""
        c = self.c
        fig = go.Figure()

        fig.add_trace(go.Candlestick(
            x=self.df["time"],
            open=self.df["open"],
            high=self.df["high"],
            low=self.df["low"],
            close=self.df["close"],
            increasing_line_color=c["green"],
            decreasing_line_color=c["red"],
            increasing_fillcolor=c["green"],
            decreasing_fillcolor=c["red"],
            name="Price",
        ))

        fig.update_layout(
            template=c["template"],
            paper_bgcolor=c["bg"],
            plot_bgcolor=c["card"],
            font=dict(color=c["text"], family="Inter, sans-serif", size=11),
            title=dict(
                text=f"{self.symbol} -- {self.timeframe}",
                font=dict(color=c["text"], size=14),
                x=0.01,
            ),
            xaxis=dict(
                gridcolor=c["grid"], showgrid=True,
                rangeslider=dict(visible=False), type="date",
            ),
            yaxis=dict(gridcolor=c["grid"], showgrid=True, side="right"),
            margin=dict(l=10, r=60, t=40, b=30),
            showlegend=False,
            hovermode="x unified",
            dragmode="pan",
        )

        return fig

    def add_order_blocks(self, obs: List[OrderBlock]) -> None:
        """Add Order Block rectangles to the chart."""
        c = self.c
        for ob in obs:
            if not ob.is_fresh:
                continue
            color = c["ob_bull"] if ob.type == "bullish" else c["ob_bear"]
            border = c["blue"] if ob.type == "bullish" else c["red"]
            label = f"{'Bull' if ob.type == 'bullish' else 'Bear'} OB"

            start_time = self.df["time"].iloc[ob.index] if ob.index < len(self.df) else self.df["time"].iloc[-1]
            end_time = self.df["time"].iloc[-1]

            self.fig.add_shape(
                type="rect",
                x0=start_time, x1=end_time,
                y0=ob.bottom, y1=ob.top,
                fillcolor=color,
                line=dict(color=border, width=1),
                layer="below",
            )
            self.fig.add_annotation(
                x=start_time, y=ob.top,
                text=f"{label} ({ob.strength:.1f}x)",
                showarrow=False,
                font=dict(color=border, size=9),
                xanchor="left", yanchor="bottom",
            )

    def add_fvgs(self, fvgs: List[FVG]) -> None:
        """Add Fair Value Gap shaded areas."""
        c = self.c
        for fvg in fvgs:
            if fvg.is_filled:
                continue
            color = c["fvg_bull"] if fvg.type == "bullish" else c["fvg_bear"]
            border = c["green"] if fvg.type == "bullish" else c["red"]

            start_time = self.df["time"].iloc[fvg.index] if fvg.index < len(self.df) else self.df["time"].iloc[-1]
            end_time = self.df["time"].iloc[-1]

            self.fig.add_shape(
                type="rect",
                x0=start_time, x1=end_time,
                y0=fvg.bottom, y1=fvg.top,
                fillcolor=color,
                line=dict(color=border, width=0.5, dash="dot"),
                layer="below",
            )
            self.fig.add_annotation(
                x=start_time, y=(fvg.top + fvg.bottom) / 2,
                text="FVG", showarrow=False,
                font=dict(color=border, size=8),
                xanchor="left",
            )

    def add_eqh_eql(self, eqh_list: List[EqualLevel],
                     eql_list: List[EqualLevel]) -> None:
        """Add Equal Highs (dashed orange) and Equal Lows (dashed cyan) lines."""
        c = self.c
        for eqh in eqh_list:
            if eqh.is_swept:
                continue
            self.fig.add_hline(
                y=eqh.price,
                line=dict(color=c["orange"], width=1, dash="dash"),
                annotation_text=f"EQH ({eqh.count}x)",
                annotation_font=dict(color=c["orange"], size=9),
                annotation_position="top left",
            )
        for eql in eql_list:
            if eql.is_swept:
                continue
            self.fig.add_hline(
                y=eql.price,
                line=dict(color=c["cyan"], width=1, dash="dash"),
                annotation_text=f"EQL ({eql.count}x)",
                annotation_font=dict(color=c["cyan"], size=9),
                annotation_position="bottom left",
            )

    def add_bos_labels(self, bos_events: List[BOSEvent]) -> None:
        """Add BOS labels on the chart at breakout candles."""
        c = self.c
        for bos in bos_events[-10:]:
            if bos.index >= len(self.df):
                continue
            color = c["green"] if bos.direction == "bullish" else c["red"]
            y_pos = self.df["high"].iloc[bos.index] if bos.direction == "bullish" else self.df["low"].iloc[bos.index]
            y_anchor = "bottom" if bos.direction == "bullish" else "top"

            self.fig.add_annotation(
                x=self.df["time"].iloc[bos.index], y=y_pos,
                text="BOS", showarrow=True,
                arrowhead=2, arrowsize=0.8, arrowcolor=color,
                font=dict(color=color, size=10, family="Inter"),
                bgcolor="rgba(0,0,0,0.6)" if c is DARK_COLORS else "rgba(255,255,255,0.8)",
                bordercolor=color, borderwidth=1, yanchor=y_anchor,
            )

    def add_choch_labels(self, choch_events: List[CHoCHEvent]) -> None:
        """Add CHoCH labels on structure change candles."""
        c = self.c
        for ch in choch_events:
            if ch.index >= len(self.df):
                continue
            color = c["green"] if ch.direction == "bullish" else c["red"]
            y_pos = self.df["high"].iloc[ch.index] if ch.direction == "bullish" else self.df["low"].iloc[ch.index]

            self.fig.add_annotation(
                x=self.df["time"].iloc[ch.index], y=y_pos,
                text="CHoCH", showarrow=True,
                arrowhead=2, arrowsize=1, arrowcolor=color,
                font=dict(color=color, size=11, family="Inter"),
                bgcolor="rgba(0,0,0,0.7)" if c is DARK_COLORS else "rgba(255,255,255,0.9)",
                bordercolor=color, borderwidth=1.5,
            )

    def add_asian_range(self, asian: Optional[Dict]) -> None:
        """Add Asian range with Midnight Open and sweep indicators."""
        if not asian:
            return
        c = self.c

        high_swept = asian.get("high_swept", False)
        low_swept = asian.get("low_swept", False)

        # Asian High line
        high_color = c["red"] if high_swept else c["gray"]
        high_dash = "solid" if high_swept else "dot"
        high_label = "Asian High SWEPT ✓" if high_swept else "Asian High"
        self.fig.add_hline(
            y=asian["high"],
            line=dict(color=high_color, width=1.5 if high_swept else 1, dash=high_dash),
            annotation_text=high_label,
            annotation_font=dict(color=high_color, size=9),
            annotation_position="top left",
        )

        # Asian Low line
        low_color = c["green"] if low_swept else c["gray"]
        low_dash = "solid" if low_swept else "dot"
        low_label = "Asian Low SWEPT ✓" if low_swept else "Asian Low"
        self.fig.add_hline(
            y=asian["low"],
            line=dict(color=low_color, width=1.5 if low_swept else 1, dash=low_dash),
            annotation_text=low_label,
            annotation_font=dict(color=low_color, size=9),
            annotation_position="bottom left",
        )

        # Asian Range shaded area
        self.fig.add_hrect(
            y0=asian["low"], y1=asian["high"],
            fillcolor=c["asian_fill"],
            line=dict(width=0), layer="below",
        )

        # Midnight Open line (the daily anchor / "North Star")
        midnight_open = asian.get("midnight_open")
        if midnight_open:
            mo_color = c.get("orange", "#ff9800")
            self.fig.add_hline(
                y=midnight_open,
                line=dict(color=mo_color, width=1.5, dash="dashdot"),
                annotation_text="Midnight Open",
                annotation_font=dict(color=mo_color, size=9),
                annotation_position="top right",
            )

    def add_entry_lines(
        self,
        entry: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> None:
        """Add horizontal lines for entry, SL, and TP."""
        c = self.c
        if entry:
            self.fig.add_hline(
                y=entry,
                line=dict(color=c["entry"], width=2),
                annotation_text=f"Entry: {entry}",
                annotation_font=dict(color=c["entry"], size=10),
            )
        if sl:
            self.fig.add_hline(
                y=sl,
                line=dict(color=c["red"], width=2, dash="dash"),
                annotation_text=f"SL: {sl}",
                annotation_font=dict(color=c["red"], size=10),
            )
        if tp:
            self.fig.add_hline(
                y=tp,
                line=dict(color=c["tp2"], width=2, dash="dash"),
                annotation_text=f"TP: {tp}",
                annotation_font=dict(color=c["tp2"], size=10),
            )

    def get_figure(self) -> go.Figure:
        """Return the complete Plotly figure."""
        return self.fig


def build_empty_chart(dark_mode: bool = True) -> go.Figure:
    """Build an empty chart with placeholder text."""
    c = DARK_COLORS if dark_mode else LIGHT_COLORS
    fig = go.Figure()
    fig.update_layout(
        template=c["template"],
        paper_bgcolor=c["bg"],
        plot_bgcolor=c["card"],
        font=dict(color=c["text"]),
        title=dict(text="Waiting for historical data from MT5...",
                   font=dict(color=c["gray"])),
        xaxis=dict(showgrid=False, visible=False),
        yaxis=dict(showgrid=False, visible=False),
        margin=dict(l=10, r=10, t=40, b=10),
        annotations=[
            dict(
                text="Waiting for historical data from MT5...",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False,
                font=dict(size=18, color=c["text"], weight="bold"),
            ),
            dict(
                text="Ensure the bot is connected to the broker and a symbol is selected.",
                xref="paper", yref="paper", x=0.5, y=0.45,
                showarrow=False,
                font=dict(size=14, color=c["gray"]),
            )
        ],
    )
    return fig
