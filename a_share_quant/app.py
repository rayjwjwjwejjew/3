"""A-Share Quant V1 Web App（Streamlit，同花顺风格）。

启动：
    streamlit run app.py

然后浏览器打开 http://localhost:8501

设计要素（参考同花顺客户端）：
- 侧边栏：参数面板（lookback / skip / top_k / 调仓频率 / 初始资金 / 数据规模）
- 顶部：4 个大数字 KPI（总收益 / 年化 / 最大回撤 / Sharpe）+ 端值 + 区间
- 主区：
  1. 净值曲线（Plotly 真实图表，可缩放）
  2. 关键指标 + 交易统计（两列）
  3. 年度收益（带柱状）
  4. 调仓时间线
  5. 风险信号（异常检测）

A 股惯例：红涨绿跌。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 确保项目根在 path（streamlit run 时的 cwd 不一定是项目根）
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.factors.momentum import clear_momentum_cache
from src.backtest.engine import run_backtest
from src.reports.performance import build_report, PerformanceReport
from src.data.schema import (
    COL_ADJ_CLOSE, COL_ADJ_FACTOR, COL_AMOUNT, COL_CODE, COL_DATE,
    COL_LIMIT_DOWN, COL_LIMIT_UP, COL_LOW, COL_OPEN, COL_HIGH, COL_CLOSE,
    COL_VOL, COL_SUSPENDED, COL_ST,
)


# ===== 页面配置 =====
st.set_page_config(
    page_title="A-Share Quant V1",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ===== 同花顺风格 CSS =====
st.markdown("""
<style>
/* 红涨绿跌（A 股惯例） */
.pos { color: #ef4444; font-weight: 600; }
.neg { color: #10b981; font-weight: 600; }
.neu { color: #9ca3af; }

/* 顶部 KPI 大数字 */
.kpi-card {
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
    padding: 20px 24px;
    border-radius: 10px;
    border: 1px solid #334155;
    color: #f1f5f9;
}
.kpi-label {
    color: #94a3b8;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 8px;
}
.kpi-value {
    color: #f8fafc;
    font-size: 32px;
    font-weight: 700;
    line-height: 1.2;
}
.kpi-sub {
    color: #64748b;
    font-size: 13px;
    margin-top: 6px;
}

/* 表格美化 */
.stDataFrame {
    border-radius: 8px;
    overflow: hidden;
}

/* section 标题 */
.section-title {
    color: #1e293b;
    font-size: 18px;
    font-weight: 600;
    border-left: 4px solid #ef4444;
    padding-left: 12px;
    margin: 24px 0 12px 0;
}
</style>
""", unsafe_allow_html=True)


# ===== 数据生成（合成 / 或加载 processed） =====
@st.cache_data(show_spinner="合成数据中…")
def make_synthetic_bars(n_stocks: int, n_days: int, seed: int = 2026) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    codes = [f"{600000 + i}" for i in range(n_stocks)]
    codes_arr = np.repeat(codes, n_days)
    dates_arr = np.tile(pd.bdate_range("2022-01-04", periods=n_days), n_stocks)
    rets = rng.normal(0.0008, 0.015, n_stocks * n_days)
    prices = 10.0 * np.cumprod(1 + rets)
    return pd.DataFrame({
        COL_CODE: codes_arr, COL_DATE: dates_arr,
        COL_OPEN: prices, COL_HIGH: prices * 1.005, COL_LOW: prices * 0.995,
        COL_CLOSE: prices, COL_ADJ_CLOSE: prices,
        COL_VOL: 1_000_000, COL_AMOUNT: 200_000_000.0, COL_ADJ_FACTOR: 1.0,
        COL_SUSPENDED: False, COL_ST: False,
        COL_LIMIT_UP: np.round(prices * 1.10, 2), COL_LIMIT_DOWN: np.round(prices * 0.90, 2),
    })


def make_stock_basic(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        COL_CODE: codes,
        "list_date": pd.to_datetime("1999-01-01"),
        "delist_date": pd.NaT,
    })


def make_calendar(dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({COL_DATE: dates, "is_trading_day": True})


# ===== 工具函数 =====
def _color_ret(v: float) -> str:
    if v > 0:
        return "pos"
    if v < 0:
        return "neg"
    return "neu"


def _arrow(v: float) -> str:
    return "▲" if v > 0 else ("▼" if v < 0 else "─")


def _fmt_pct(v: float, digits: int = 2) -> str:
    if pd.isna(v):
        return "—"
    return f"{v * 100:.{digits}f}%"


def _kpi_card(label: str, value: str, sub: str = "", color_class: str = "neu") -> str:
    return f"""
<div class="kpi-card">
  <div class="kpi-label">{label}</div>
  <div class="kpi-value {color_class}">{value}</div>
  <div class="kpi-sub">{sub}</div>
</div>
"""


# ===== 回测执行（带缓存） =====
@st.cache_data(show_spinner="回测运行中…")
def run_backtest_cached(
    n_stocks: int,
    n_days: int,
    initial_cash: float,
    rebalance_every: int,
    lookback: int,
    skip: int,
    top_k: int,
    seed: int,
) -> dict:
    """统一的回测入口。streamlit 缓存保证同参数不重跑。"""
    clear_momentum_cache()
    bars = make_synthetic_bars(n_stocks, n_days, seed=seed)
    codes = list(bars[COL_CODE].unique())
    sb = make_stock_basic(codes)
    cal = make_calendar(bars[COL_DATE].unique())
    return run_backtest(
        bars, sb, cal,
        initial_cash=initial_cash,
        rebalance_every=rebalance_every,
        lookback=lookback, skip=skip, top_k=top_k,
    )


# ===== 主区 =====
def render_header(rep: PerformanceReport):
    """顶部 4 个 KPI + 端值。"""
    st.markdown('<div class="section-title">📊 概览</div>', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)

    color_total = _color_ret(rep.total_return)
    color_ann = _color_ret(rep.annualized_return)
    color_dd = _color_ret(rep.max_drawdown)
    color_sh = _color_ret(rep.sharpe)

    with col1:
        st.markdown(_kpi_card(
            "总收益率",
            f"{_arrow(rep.total_return)} {_fmt_pct(rep.total_return)}",
            f"初始 ¥{rep.initial_cash:,.0f} → 终值 ¥{rep.final_nav:,.0f}",
            color_total,
        ), unsafe_allow_html=True)
    with col2:
        st.markdown(_kpi_card(
            "年化收益",
            _fmt_pct(rep.annualized_return),
            f"波动率 {_fmt_pct(rep.annualized_vol)}",
            color_ann,
        ), unsafe_allow_html=True)
    with col3:
        st.markdown(_kpi_card(
            "最大回撤",
            f"{_arrow(rep.max_drawdown)} {_fmt_pct(rep.max_drawdown)}",
            f"修复 {rep.max_dd_recovery_days or '未修复'} 天" if rep.max_dd_recovery_days is not None else f"修复 [未修复]",
            color_dd,
        ), unsafe_allow_html=True)
    with col4:
        st.markdown(_kpi_card(
            "夏普比率",
            f"{rep.sharpe:+.2f}",
            f"换手率 {_fmt_pct(rep.annualized_turnover, 1)}（年化）",
            color_sh,
        ), unsafe_allow_html=True)


def render_nav_chart(nav: pd.DataFrame, rep: PerformanceReport):
    """净值曲线（Plotly）。"""
    import plotly.graph_objects as go

    st.markdown('<div class="section-title">📈 净值曲线</div>', unsafe_allow_html=True)

    if nav.empty or "nav" not in nav.columns:
        st.warning("无净值数据")
        return

    s = nav["nav"]
    # 红涨绿跌配色
    color = "#ef4444" if s.iloc[-1] >= s.iloc[0] else "#10b981"
    fill_color = "rgba(239, 68, 68, 0.1)" if s.iloc[-1] >= s.iloc[0] else "rgba(16, 185, 129, 0.1)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values,
        mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy",
        fillcolor=fill_color,
        name="NAV",
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>NAV: ¥%{y:,.0f}<extra></extra>",
    ))
    # 基准线：初始资金
    fig.add_hline(
        y=rep.initial_cash,
        line=dict(color="#94a3b8", width=1, dash="dash"),
        annotation_text=f"初始 ¥{rep.initial_cash:,.0f}",
        annotation_position="right",
    )
    fig.update_layout(
        height=400,
        margin=dict(l=0, r=0, t=20, b=0),
        xaxis_title="",
        yaxis_title="净值 (¥)",
        hovermode="x unified",
        template="plotly_dark",
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
    )
    st.plotly_chart(fig, use_container_width=True)

    # 区间高低
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("区间高", f"¥{s.max():,.0f}", _fmt_pct(s.max() / s.iloc[0] - 1))
    with c2:
        st.metric("区间低", f"¥{s.min():,.0f}", _fmt_pct(s.min() / s.iloc[0] - 1))
    with c3:
        st.metric("区间", f"{(s.index[-1] - s.index[0]).days} 天", "")


def render_metrics(rep: PerformanceReport, daily_logs: list | None):
    """关键指标 + 交易统计。"""
    st.markdown('<div class="section-title">📋 详细指标</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**关键指标**")
        data = {
            "指标": ["年化收益", "夏普比率", "最大回撤", "回撤修复", "波动率", "换手率", "成本/收益", "最长亏损"],
            "值": [
                f"<span class='{_color_ret(rep.annualized_return)}'>{_fmt_pct(rep.annualized_return)}</span>",
                f"<span class='{_color_ret(rep.sharpe)}'>{rep.sharpe:+.2f}</span>",
                f"<span class='{_color_ret(rep.max_drawdown)}'>{_fmt_pct(rep.max_drawdown)}</span>",
                f"{rep.max_dd_recovery_days or '⚠ 未修复'} 天",
                _fmt_pct(rep.annualized_vol),
                _fmt_pct(rep.annualized_turnover, 1),
                _fmt_pct(rep.cost_ratio, 2),
                f"{rep.longest_losing_streak_months} 月",
            ],
        }
        st.markdown(
            pd.DataFrame(data).to_html(escape=False, index=False),
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown("**交易统计**")
        status_dist = rep.order_status_distribution or {}
        data2 = {
            "项": ["总订单", "成交 (FILLED)", "被拒 (REJECTED)", "撤销 (CANCELLED)", "总成本", "换手 (年化)"],
            "值": [
                f"{rep.total_trades}",
                f"<span class='pos'>{status_dist.get('FILLED', 0)}</span>",
                f"<span class='neg'>{status_dist.get('REJECTED', 0)}</span>",
                f"<span class='neu'>{status_dist.get('CANCELLED', 0)}</span>",
                f"¥{rep.total_costs:,.0f}",
                _fmt_pct(rep.annualized_turnover, 1),
            ],
        }
        st.markdown(
            pd.DataFrame(data2).to_html(escape=False, index=False),
            unsafe_allow_html=True,
        )


def render_yearly(rep: PerformanceReport):
    """年度收益（含柱状图）。"""
    st.markdown('<div class="section-title">📅 年度收益</div>', unsafe_allow_html=True)
    if rep.yearly_returns.empty:
        st.warning("无年度收益数据")
        return

    import plotly.graph_objects as go

    rows = []
    for y, r in rep.yearly_returns.items():
        y_str = y.strftime("%Y") if hasattr(y, "strftime") else str(y)
        if pd.isna(r):
            rows.append({"年份": y_str, "收益": "—", "柱状": ""})
            continue
        color = "ef4444" if r > 0 else "10b981"
        bar_len = max(1, int(abs(r) * 30))  # 1% = 3 块
        bar = "█" * bar_len
        rows.append({
            "年份": y_str,
            "收益": f"<span class='{_color_ret(r)}'><b>{_fmt_pct(r)}</b></span>",
            "柱状": f"<span class='{_color_ret(r)}' style='font-family: monospace'>{bar}</span>",
        })
    st.markdown(
        pd.DataFrame(rows).to_html(escape=False, index=False),
        unsafe_allow_html=True,
    )


def render_rebalance_timeline(nav: pd.DataFrame, daily_logs: list | None):
    """调仓时间线。"""
    st.markdown('<div class="section-title">🔄 调仓时间线</div>', unsafe_allow_html=True)
    rebal_logs = [log for log in (daily_logs or []) if getattr(log, "is_rebalance", False)]
    if not rebal_logs:
        st.info("无调仓事件（参数下调整仓频率）")
        return

    rows = []
    for log in rebal_logs[:30]:  # 最多 30 行
        d = log.date
        if d in nav.index and len(nav) > 0:
            idx = nav.index.get_loc(d)
            if idx > 0:
                prev = float(nav["nav"].iloc[idx - 1])
                cur = float(nav["nav"].iloc[idx])
                daily_ret = (cur / prev) - 1 if prev > 0 else 0.0
                rows.append({
                    "调仓日": d.strftime("%Y-%m-%d"),
                    "当日收益": f"<span class='{_color_ret(daily_ret)}'>{_arrow(daily_ret)} {_fmt_pct(daily_ret, 1)}</span>",
                    "当日 NAV": f"¥{cur:,.0f}",
                    "持仓数": log.n_holdings,
                })
    if rows:
        st.markdown(
            pd.DataFrame(rows).to_html(escape=False, index=False),
            unsafe_allow_html=True,
        )


def render_risk_signals(rep: PerformanceReport):
    """风险信号（同花顺式自动异常检测）。"""
    st.markdown('<div class="section-title">⚠️ 风险信号</div>', unsafe_allow_html=True)
    signals = []

    if rep.max_drawdown < -0.20:
        signals.append(("🔴", f"最大回撤 {rep.sharpe:.2%} 过大，建议检查策略", "error"))
    elif rep.max_drawdown < -0.10:
        signals.append(("🟡", f"最大回撤 {_fmt_pct(rep.max_drawdown)} 中等", "warning"))

    if rep.sharpe < 0:
        signals.append(("🔴", f"夏普比率 {rep.sharpe:.2f} 为负，策略未跑赢现金", "error"))
    elif rep.sharpe < 0.5:
        signals.append(("🟡", f"夏普比率 {rep.sharpe:.2f} 偏低", "warning"))

    if rep.cost_ratio > 0.30:
        signals.append(("🟡", f"成本/收益 {_fmt_pct(rep.cost_ratio, 1)} 较高", "warning"))

    if rep.longest_losing_streak_months >= 6:
        signals.append(("🟡", f"最长连续亏损 {rep.longest_losing_streak_months} 月，心理压力测试", "warning"))

    if rep.max_dd_recovery_days is None:
        signals.append(("🟡", "回撤至今未修复（仍在回撤中）", "warning"))

    if not signals:
        signals.append(("🟢", "所有指标在合理范围内", "ok"))

    for icon, msg, level in signals:
        if level == "error":
            st.error(f"{icon} {msg}")
        elif level == "warning":
            st.warning(f"{icon} {msg}")
        else:
            st.success(f"{icon} {msg}")


# ===== 侧边栏 =====
def render_sidebar():
    with st.sidebar:
        st.markdown("## ⚙️ 参数设置")
        st.markdown("---")

        st.markdown("**数据规模**（合成数据）")
        n_stocks = st.slider("股票数", 50, 2000, 200, step=50)
        n_days = st.slider("交易日数", 100, 1500, 500, step=50)
        seed = st.number_input("随机种子", value=2026, step=1)

        st.markdown("---")
        st.markdown("**策略参数**")
        lookback = st.slider("动量回看 (lookback)", 60, 250, 120, step=10)
        skip = st.slider("动量跳过 (skip)", 0, 30, 5, step=1)
        top_k = st.slider("持仓数量 (top_k)", 5, 30, 10, step=1)
        rebalance_every = st.slider("调仓频率（每 N 个交易日）", 5, 60, 20, step=5)

        st.markdown("---")
        st.markdown("**资金**")
        initial_cash = st.number_input("初始资金 (¥)", value=1_000_000, step=100_000, format="%d")

        st.markdown("---")
        st.markdown("💡 **提示**：所有合成数据随机生成，**不代表真实 A 股表现**。")
        st.markdown("V1 阶段仅用作系统连通性验证。")

        return {
            "n_stocks": n_stocks, "n_days": n_days, "seed": seed,
            "lookback": lookback, "skip": skip, "top_k": top_k,
            "rebalance_every": rebalance_every, "initial_cash": initial_cash,
        }


# ===== 主入口 =====
def main():
    st.title("📈 A-Share Quant V1")
    st.caption("同花顺风格回测面板 · V1 演示 · 数据为合成随机序列")

    params = render_sidebar()

    try:
        result = run_backtest_cached(**params)
    except Exception as e:
        st.error(f"回测失败：{e}")
        return

    nav = result["nav"]
    daily_logs = result.get("daily_logs", [])
    rep = build_report(nav, result["orders"], daily_logs)

    render_header(rep)
    render_nav_chart(nav, rep)
    render_metrics(rep, daily_logs)
    render_yearly(rep)
    render_rebalance_timeline(nav, daily_logs)
    render_risk_signals(rep)

    with st.expander("📦 原始数据 / 调试信息"):
        st.markdown(f"**回测区间**：{nav.index[0].date()} → {nav.index[-1].date()}")
        st.markdown(f"**调仓次数**：{sum(1 for l in daily_logs if getattr(l, 'is_rebalance', False))}")
        st.markdown(f"**总订单数**：{len(result['orders'])}")
        st.markdown("**NAV（最后 10 天）**")
        st.dataframe(nav.tail(10))


if __name__ == "__main__":
    main()
