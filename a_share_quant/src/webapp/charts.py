"""Streamlit 渲染用图表工具（同花顺风格 Plotly）。"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.data.schema import COL_CODE


# ===== 净值曲线（带基准对比）=====
def nav_chart(
    nav: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    title: str = "净值曲线",
    height: int = 420,
) -> go.Figure:
    """同花顺式净值曲线：策略 + 基准（可选）。"""
    fig = go.Figure()
    s = nav["nav"] if "nav" in nav.columns else nav.iloc[:, 0]
    base_s = s.iloc[0]
    norm_s = s / base_s
    color = "#ef4444" if norm_s.iloc[-1] >= 1.0 else "#10b981"

    fig.add_trace(go.Scatter(
        x=s.index, y=norm_s, name="策略",
        mode="lines", line=dict(color=color, width=2.5),
        fill="tozeroy", fillcolor="rgba(239, 68, 68, 0.08)" if color == "#ef4444" else "rgba(16, 185, 129, 0.08)",
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>NAV: %{y:.4f}<extra></extra>",
    ))

    if benchmark is not None and not benchmark.empty:
        b = benchmark["nav"] if "nav" in benchmark.columns else benchmark.iloc[:, 0]
        norm_b = b / b.iloc[0]
        fig.add_trace(go.Scatter(
            x=b.index, y=norm_b, name="基准（等权 A 股）",
            mode="lines", line=dict(color="#94a3b8", width=1.5, dash="dot"),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>基准: %{y:.4f}<extra></extra>",
        ))

    fig.add_hline(y=1.0, line=dict(color="#64748b", width=1, dash="dash"),
                  annotation_text="基准 = 1.0", annotation_position="right")

    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        height=height,
        margin=dict(l=0, r=0, t=40, b=0),
        hovermode="x unified",
        template="plotly_dark",
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# ===== K 线图（同花顺风格：红涨绿跌）=====
def kline_chart(bars: pd.DataFrame, code: str, height: int = 420) -> go.Figure:
    """单只股票的 K 线图（A 股惯例：红涨绿跌）。"""
    if bars.empty or COL_CODE not in bars.columns:
        return go.Figure().update_layout(
            title=f"无 {code} 数据",
            template="plotly_dark", paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
            height=height,
        )
    df = bars[bars[COL_CODE] == code].copy()
    if df.empty:
        return go.Figure().update_layout(
            title=f"无 {code} 数据",
            template="plotly_dark", paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
            height=height,
        )
    df = df.sort_values("date").reset_index(drop=True)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.03,
    )

    # K 线：红涨绿跌
    fig.add_trace(go.Candlestick(
        x=df["date"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=code,
        increasing_line_color="#ef4444", decreasing_line_color="#10b981",
        increasing_fillcolor="#ef4444", decreasing_fillcolor="#10b981",
    ), row=1, col=1)

    # 成交量
    colors = ["#ef4444" if c >= o else "#10b981" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df["date"], y=df["volume"], name="成交量",
        marker_color=colors, opacity=0.7,
    ), row=2, col=1)

    # 20 日均线
    ma20 = df["close"].rolling(20).mean()
    fig.add_trace(go.Scatter(
        x=df["date"], y=ma20, name="MA20",
        line=dict(color="#fbbf24", width=1.2),
    ), row=1, col=1)

    fig.update_layout(
        title=dict(text=f"{code} K 线（同花顺风格）", font=dict(size=16)),
        height=height,
        template="plotly_dark", paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
        xaxis_rangeslider_visible=False,  # 关掉下方拖拽条
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# ===== 基准对比柱状图 =====
def benchmark_bar(strategy_total: float, benchmark_total: float, height: int = 300) -> go.Figure:
    fig = go.Figure()
    items = [
        ("策略", strategy_total, "#ef4444" if strategy_total > 0 else "#10b981"),
        ("基准", benchmark_total, "#ef4444" if benchmark_total > 0 else "#10b981"),
    ]
    fig.add_trace(go.Bar(
        x=[n for n, _, _ in items],
        y=[v * 100 for _, v, _ in items],
        marker_color=[c for _, _, c in items],
        text=[f"{v*100:.2f}%" for _, v, _ in items],
        textposition="auto",
    ))
    fig.update_layout(
        title="策略 vs 基准（总收益 %）",
        height=height,
        yaxis_title="总收益 (%)",
        template="plotly_dark", paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
    )
    return fig


# ===== 年度收益柱状（带策略+基准）=====
def yearly_bar_compare(
    strategy_yearly: pd.Series,
    benchmark_yearly: pd.Series,
    height: int = 320,
) -> go.Figure:
    fig = go.Figure()
    years = sorted(set(list(strategy_yearly.index) + list(benchmark_yearly.index)))
    years = [y for y in years if pd.notna(y)]

    s_vals = [strategy_yearly.get(y, 0) * 100 for y in years]
    b_vals = [benchmark_yearly.get(y, 0) * 100 for y in years]
    year_labels = [y.strftime("%Y") if hasattr(y, "strftime") else str(y) for y in years]

    fig.add_trace(go.Bar(
        x=year_labels, y=s_vals, name="策略",
        marker_color=["#ef4444" if v >= 0 else "#10b981" for v in s_vals],
    ))
    fig.add_trace(go.Bar(
        x=year_labels, y=b_vals, name="基准",
        marker_color=["#94a3b8" if v >= 0 else "#475569" for v in b_vals],
        opacity=0.6,
    ))
    fig.update_layout(
        title="年度收益对比",
        height=height,
        yaxis_title="收益 (%)",
        template="plotly_dark", paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
        barmode="group",
    )
    return fig


# ===== 导出图表 =====
def export_to_png(fig: go.Figure, path: str | Path, scale: float = 2.0) -> bool:
    """Plotly → PNG。需要 kaleido。失败返回 False。"""
    try:
        fig.write_image(str(path), scale=scale)
        return True
    except Exception:
        return False
