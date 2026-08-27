"""A-Share Quant V1 Web App（Streamlit 多页，同花顺风格）。

启动：
    streamlit run app.py

浏览器打开 http://localhost:8501

5 个页面：
1. 概览：KPI 卡片 + 净值曲线 + 风险信号
2. 回测：详细指标 + 年度收益 + 调仓时间线 + 导出 PNG
3. 行情：单只股票 K 线 + 成交量 + MA20
4. 过拟合：多策略对比
5. 实盘：broker 状态 + emergency stop

设计要素（参考同花顺客户端）：
- 暗色主题 + 红涨绿跌（A 股惯例）
- 顶部 KPI 大数字 + ▲/▼ 箭头
- Plotly 真实图表（可缩放、悬停）
- 自动异常检测
- 优先真实数据，回退合成数据
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# ===== 数据层 =====
from src.factors.momentum import clear_momentum_cache
from src.backtest.engine import run_backtest
from src.reports.performance import build_report, PerformanceReport
from src.data.schema import (
    COL_ADJ_CLOSE, COL_ADJ_FACTOR, COL_AMOUNT, COL_CODE, COL_DATE,
    COL_LIMIT_DOWN, COL_LIMIT_UP, COL_LOW, COL_OPEN, COL_HIGH, COL_CLOSE,
    COL_VOL, COL_SUSPENDED, COL_ST,
)
from src.data.benchmark.benchmarks import make_equal_weight_benchmark
from src.webapp.data_loader import has_real_data, sample_real_data
from src.webapp.charts import nav_chart, kline_chart, export_to_png
from src.webapp.theme import apply_theme, render_app_header, render_section_heading

PROJECT_ROOT = Path(__file__).resolve().parent

# ===== 页面配置 =====
st.set_page_config(
    page_title="A-Share Quant V1",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_theme(st)


# ===== 数据生成 =====
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


# ===== 回测入口（带缓存）=====
@st.cache_data(show_spinner="回测运行中…")
def run_backtest_cached(
    n_stocks: int, n_days: int, initial_cash: float,
    rebalance_every: int, lookback: int, skip: int, top_k: int,
    seed: int, use_real: bool, cost_multiplier: float = 1.0,
) -> dict:
    clear_momentum_cache()
    if use_real:
        bars = sample_real_data(n_stocks, n_days, seed=seed)
        if bars is None:
            bars = make_synthetic_bars(n_stocks, n_days, seed=seed)
    else:
        bars = make_synthetic_bars(n_stocks, n_days, seed=seed)
    codes = list(bars[COL_CODE].unique())
    sb = make_stock_basic(codes)
    cal = make_calendar(bars[COL_DATE].unique())
    return run_backtest(
        bars, sb, cal, initial_cash=initial_cash,
        rebalance_every=rebalance_every, lookback=lookback,
        skip=skip, top_k=top_k, cost_multiplier=cost_multiplier,
    )


# ===== 侧边栏 =====
def render_sidebar():
    with st.sidebar:
        st.markdown('<div class="sidebar-kicker">Experiment setup</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-title">实验配置</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-copy">修改参数后，页面会重新计算当前研究实验。</div>', unsafe_allow_html=True)

        use_real = st.checkbox(
            "📊 使用真实数据（已导入行情）",
            value=False,
            help="需先执行 `python -m src clean --partitioned` 或生成 bars.parquet",
            disabled=not has_real_data(),
        )
        if not has_real_data():
            st.caption("⚠️ 暂无真实数据，使用合成数据")

        with st.expander("实验规模", expanded=True):
            n_stocks = st.slider("股票数", 50, 2000, 200, step=50)
            n_days = st.slider("交易日数", 100, 1500, 500, step=50)
        with st.expander("策略参数", expanded=True):
            lookback = st.slider("动量回看", 60, 250, 120, step=10)
            skip = st.slider("动量跳过", 0, 30, 5, step=1)
            top_k = st.slider("持仓数量", 5, 30, 10, step=1)
            rebalance_every = st.slider("调仓频率（每 N 日）", 5, 60, 20, step=5)
        with st.expander("资金与复现", expanded=False):
            initial_cash = st.number_input("初始资金 (¥)", value=1_000_000, step=100_000, format="%d")
            seed = st.number_input("随机种子", value=2026, step=1)

        st.caption("💡 合成数据**不代表**真实 A 股表现")

        return {
            "n_stocks": n_stocks, "n_days": n_days, "seed": seed,
            "lookback": lookback, "skip": skip, "top_k": top_k,
            "rebalance_every": rebalance_every, "initial_cash": initial_cash,
            "use_real": use_real,
        }


# ===== 共用：渲染 KPI 卡片 =====
def render_kpi_cards(rep: PerformanceReport):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_kpi_card("总收益率",
            f"{_arrow(rep.total_return)} {_fmt_pct(rep.total_return)}",
            f"初始 ¥{rep.initial_cash:,.0f} → 终值 ¥{rep.final_nav:,.0f}",
            _color_ret(rep.total_return)), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi_card("年化收益", _fmt_pct(rep.annualized_return),
            f"波动率 {_fmt_pct(rep.annualized_vol)}", _color_ret(rep.annualized_return)),
            unsafe_allow_html=True)
    with c3:
        rec = f"{rep.max_dd_recovery_days} 天" if rep.max_dd_recovery_days is not None else "未修复"
        st.markdown(_kpi_card("最大回撤",
            f"{_arrow(rep.max_drawdown)} {_fmt_pct(rep.max_drawdown)}",
            f"修复 {rec}", _color_ret(rep.max_drawdown)), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi_card("夏普比率", f"{rep.sharpe:+.2f}",
            f"换手率 {_fmt_pct(rep.annualized_turnover, 1)}（年化）",
            _color_ret(rep.sharpe)), unsafe_allow_html=True)


# ===== 共用：风险信号 =====
def render_risk_signals(rep: PerformanceReport):
    """把风险信息压缩为可扫描的状态卡，避免原生告警占满页面。"""
    signals = []
    if rep.max_drawdown < -0.20:
        signals.append(("🔴", f"最大回撤 {_fmt_pct(rep.max_drawdown)} 过大", "error"))
    elif rep.max_drawdown < -0.10:
        signals.append(("🟡", f"最大回撤 {_fmt_pct(rep.max_drawdown)} 中等", "warning"))
    if rep.sharpe < 0:
        signals.append(("🔴", f"夏普 {rep.sharpe:.2f} 为负，未跑赢现金", "error"))
    elif rep.sharpe < 0.5:
        signals.append(("🟡", f"夏普 {rep.sharpe:.2f} 偏低", "warning"))
    if rep.cost_ratio > 0.30:
        signals.append(("🟡", f"成本/收益 {_fmt_pct(rep.cost_ratio, 1)} 较高", "warning"))
    if rep.longest_losing_streak_months >= 6:
        signals.append(("🟡", f"最长连续亏损 {rep.longest_losing_streak_months} 月", "warning"))
    if rep.max_dd_recovery_days is None:
        signals.append(("🟡", "回撤至今未修复", "warning"))
    if not signals:
        signals.append(("🟢", "所有指标在合理范围内", "ok"))
    cards = "".join(
        f'<article class="signal-card {level}"><span class="signal-icon">{icon}</span><span class="signal-copy">{msg}</span></article>'
        for icon, msg, level in signals
    )
    st.markdown(f'<div class="signal-stack">{cards}</div>', unsafe_allow_html=True)


def render_research_verdict(rep: PerformanceReport, *, use_real: bool) -> None:
    """总览页优先给研究判断，而不是让用户自己解读四个数字。"""
    if rep.sharpe < 0 or rep.max_drawdown < -0.20:
        state = "当前实验未通过"
        copy = "收益与风险信号尚未满足研究晋级条件；请先检查数据、成本与参数稳定性。"
    elif rep.sharpe < 0.5 or rep.max_dd_recovery_days is None:
        state = "需要更多稳健性证据"
        copy = "结果未形成明确优势。下一步应比较样本外区间、参数邻域和成本压力。"
    else:
        state = "可继续做稳健性验证"
        copy = "当前指标没有触发主要风险阈值，但仍需通过冻结样本外与模拟盘检验。"
    data_name = "真实数据" if use_real else "合成数据"
    st.markdown(
        f'''<section class="research-card"><div class="research-label">研究判断</div><div class="research-state">{state}</div><p class="research-copy">{copy}</p><div class="research-meta"><span>{data_name}</span><span>夏普 {rep.sharpe:+.2f}</span><span>回撤 {_fmt_pct(rep.max_drawdown)}</span></div></section>''',
        unsafe_allow_html=True,
    )


# ===== 页面 1: 概览 =====
def page_overview(params, result, nav, rep):
    render_section_heading(
        st, "Current experiment", "先看结论，再看曲线",
        "同一组参数下的四个核心指标；红涨绿跌沿用 A 股习惯。",
    )
    render_kpi_cards(rep)

    left, right = st.columns([1.65, 1], gap="large")
    benchmark = make_equal_weight_benchmark(result["bars"] if "bars" in result else None,
                                          initial_cash=params["initial_cash"])
    with left:
        render_section_heading(st, "Performance", "净值与等权基准", "拖拽、缩放或悬停查看单日数据。")
        fig = nav_chart(nav, benchmark, title="")
        st.plotly_chart(fig, use_container_width=True)

        if st.button("导出净值图 PNG"):
            path = PROJECT_ROOT / "results" / f"nav_chart_{params['seed']}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            if export_to_png(fig, path):
                st.success(f"已保存 {path.name}")
            else:
                st.error("导出失败（kaleido 缺失？）")
    with right:
        render_section_heading(st, "Decision", "研究状态", "自动汇总当前实验的主要风险。")
        render_research_verdict(rep, use_real=params["use_real"])
        st.markdown('<div style="height:.7rem"></div>', unsafe_allow_html=True)
        render_risk_signals(rep)


# ===== 页面 2: 回测 =====
def page_backtest(params, result, nav, rep, daily_logs):
    render_section_heading(
        st, "Backtest record", "回测明细",
        "把绩效、交易成本与调仓事件拆开，便于复核每个结论。",
    )

    col1, col2 = st.columns(2)
    with col1:
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
        table = pd.DataFrame(data).to_html(escape=False, index=False)
        st.markdown(f'<div class="table-card"><div class="table-card-title">关键指标</div>{table}</div>', unsafe_allow_html=True)
    with col2:
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
        table = pd.DataFrame(data2).to_html(escape=False, index=False)
        st.markdown(f'<div class="table-card"><div class="table-card-title">交易统计</div>{table}</div>', unsafe_allow_html=True)

    render_section_heading(st, "Time series", "年度收益", "先比较不同年份，再判断单段收益是否具有代表性。")
    if not rep.yearly_returns.empty:
        rows = []
        for y, r in rep.yearly_returns.items():
            y_str = y.strftime("%Y") if hasattr(y, "strftime") else str(y)
            if pd.isna(r):
                rows.append({"年份": y_str, "收益": "—", "柱状": ""})
                continue
            bar_len = max(1, int(abs(r) * 30))
            bar = "█" * bar_len
            rows.append({
                "年份": y_str,
                "收益": f"<span class='{_color_ret(r)}'><b>{_fmt_pct(r)}</b></span>",
                "柱状": f"<span class='{_color_ret(r)}' style='font-family: monospace'>{bar}</span>",
            })
        st.markdown(pd.DataFrame(rows).to_html(escape=False, index=False), unsafe_allow_html=True)

    render_section_heading(st, "Execution trail", "调仓时间线", "最近 30 次调仓，用于回看仓位变化和当日 NAV。")
    rebal_logs = [log for log in (daily_logs or []) if getattr(log, "is_rebalance", False)]
    if rebal_logs:
        rows = []
        for log in rebal_logs[:30]:
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
            st.markdown(pd.DataFrame(rows).to_html(escape=False, index=False), unsafe_allow_html=True)
    else:
        st.info("无调仓事件（参数下调整仓频率）")

    render_section_heading(st, "Export", "导出结果", "下载当前参数组合的净值序列。")
    st.download_button(
        "下载 NAV.csv",
        data=nav.to_csv().encode("utf-8"),
        file_name="nav.csv",
        mime="text/csv",
    )


# ===== 页面 3: 行情（K 线）=====
def page_market(bars: pd.DataFrame):
    render_section_heading(
        st, "Market lens", "个股行情",
        "观察单一标的的价格、成交量和 MA20；此页不产生交易指令。",
    )
    if bars is None or bars.empty:
        st.warning("无数据")
        return

    codes = sorted(bars[COL_CODE].unique().tolist())
    control, spacer = st.columns([1, 2])
    with control:
        code = st.selectbox("选择股票", codes, index=0)
        n_bars = st.slider("显示交易日", 60, 500, 200, step=20)
    recent = bars.sort_values(COL_DATE).tail(n_bars * 50)  # 留 buffer

    fig = kline_chart(recent, code)
    st.plotly_chart(fig, use_container_width=True)

    if st.button("导出 K 线 PNG"):
        path = PROJECT_ROOT / "results" / f"kline_{code}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if export_to_png(fig, path):
            st.success(f"已保存 {path.name}")
        else:
            st.error("导出失败")


# ===== 页面 4: 过拟合 =====
def page_overfit(params):
    render_section_heading(
        st, "Robustness checks", "稳健性检验",
        "只改一个变量，观察结论是否对调仓、回看期和成本假设过度敏感。",
    )
    st.markdown('<div class="boundary-card">此处是局部敏感性检查，不替代冻结样本外、DSR、PBO 或模拟盘证据。</div>', unsafe_allow_html=True)

    n_stocks = min(params["n_stocks"], 300)  # 限规模避免太慢
    n_days = min(params["n_days"], 500)

    render_section_heading(st, "Frequency", "调仓频率对比", "比较每 5、10、20 个交易日调仓的结果。")
    rows = []
    for f in (5, 10, 20):
        try:
            r = run_backtest_cached(n_stocks, n_days, params["initial_cash"],
                                     f, params["lookback"], params["skip"], params["top_k"],
                                     params["seed"], params["use_real"])
            rep = build_report(r["nav"], r["orders"], r.get("daily_logs"))
            rows.append({
                "调仓频率": f"每 {f} 日",
                "总收益": f"<span class='{_color_ret(rep.total_return)}'>{_fmt_pct(rep.total_return)}</span>",
                "夏普": f"{rep.sharpe:+.2f}",
                "最大回撤": f"<span class='{_color_ret(rep.max_drawdown)}'>{_fmt_pct(rep.max_drawdown)}</span>",
                "成交": rep.filled_trades,
            })
        except Exception as e:
            rows.append({"调仓频率": f"每 {f} 日", "总收益": "—", "夏普": "—", "最大回撤": "—", "成交": str(e)[:20]})
    st.markdown(pd.DataFrame(rows).to_html(escape=False, index=False), unsafe_allow_html=True)

    render_section_heading(st, "Lookback", "回看期敏感性", "围绕当前默认值测试临近参数。")
    rows = []
    for lb in (110, 120, 130):
        try:
            r = run_backtest_cached(n_stocks, n_days, params["initial_cash"],
                                     params["rebalance_every"], lb, params["skip"], params["top_k"],
                                     params["seed"], params["use_real"])
            rep = build_report(r["nav"], r["orders"], r.get("daily_logs"))
            rows.append({
                "lookback": lb,
                "总收益": f"<span class='{_color_ret(rep.total_return)}'>{_fmt_pct(rep.total_return)}</span>",
                "夏普": f"{rep.sharpe:+.2f}",
                "最大回撤": f"<span class='{_color_ret(rep.max_drawdown)}'>{_fmt_pct(rep.max_drawdown)}</span>",
            })
        except Exception:
            rows.append({"lookback": lb, "总收益": "—", "夏普": "—", "最大回撤": "—"})
    st.markdown(pd.DataFrame(rows).to_html(escape=False, index=False), unsafe_allow_html=True)

    render_section_heading(st, "Costs", "成本压力测试", "将交易成本翻倍，检查结论是否依赖过于乐观的成本假设。")
    rows = []
    for m, label in [(1.0, "正常"), (2.0, "×2")]:
        try:
            r = run_backtest_cached(
                n_stocks, n_days, params["initial_cash"],
                params["rebalance_every"], params["lookback"], params["skip"], params["top_k"],
                params["seed"], params["use_real"], cost_multiplier=m,
            )
            rep = build_report(r["nav"], r["orders"], r.get("daily_logs"))
            rows.append({
                "成本": label,
                "总收益": f"<span class='{_color_ret(rep.total_return)}'>{_fmt_pct(rep.total_return)}</span>",
                "总成本": f"¥{rep.total_costs:,.0f}",
                "成本/收益": _fmt_pct(rep.cost_ratio, 2),
            })
        except Exception:
            rows.append({"成本": label, "总收益": "—", "总成本": "—", "成本/收益": "—"})
    st.markdown(pd.DataFrame(rows).to_html(escape=False, index=False), unsafe_allow_html=True)


# ===== 页面 5: 实盘 =====
def page_broker():
    render_section_heading(
        st, "Execution boundary", "执行边界",
        "状态可见，但 V1 不提供下单能力，也不连接任何真实券商。",
    )
    st.markdown('<div class="boundary-card">研究结果不是交易许可。必须先完成稳健性验证、至少 30 个交易日模拟盘与人工对账。</div>', unsafe_allow_html=True)

    from src.broker import is_emergency_stopped
    from src.risk.controls import TRADING_ENABLED

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("TRADING_ENABLED", "✅ True" if TRADING_ENABLED else "🔴 False",
                  delta="正常运行" if TRADING_ENABLED else "已停止")
    with col2:
        st.metric("Emergency Stop", "🚨 已触发" if is_emergency_stopped() else "🟢 未触发")
    with col3:
        st.metric("Broker 层", "V1 手动模式", "不连接券商")

    render_section_heading(st, "Safeguard", "紧急停止", "以下命令仅管理本地 broker 框架状态。")
    st.code("python -m src broker --action emergency-stop", language="bash")
    st.code("python -m src broker --action clear-stop", language="bash")
    st.code("python -m src broker --action status", language="bash")

    render_section_heading(st, "Prerequisites", "接入前置条件", "以下条件全部满足前，不开放真实交易接入。")
    st.markdown("""
- ✅ V2 阶段过拟合测试通过
- ✅ V3 阶段模拟盘稳定运行 ≥ 30 个交易日
- ✅ 已向开户券商完成程序化交易报告
- ✅ API 密钥通过环境变量（**禁止写入 Git**）
- ✅ 已阅读 `src/broker/README.md`
    """)

    render_section_heading(st, "Future adapter", "接入新券商的标准流程", "仅作为未来架构说明，不执行任何外部操作。")
    st.markdown("""
1. 实现 `BrokerAdapter` 子类（`get_account` / `submit_order` / `cancel_order` / `is_connected`）
2. 添加进 `src/broker/<券商名>.py`
3. CLI 加 `--broker <券商名>` 参数
4. 小规模（建议首笔 ≤ 1w 元）测试
5. 通过 `emergency_stop` 测试：手动触发后能正确停止
    """)


# ===== 主入口 =====
def main():
    params = render_sidebar()
    render_app_header(st, real_data_available=has_real_data(), using_real_data=params["use_real"])

    st.markdown(
        '<section class="nav-context"><div class="nav-label">研究工作区</div><p>选择一个视角；实验配置始终保留在左侧。</p></section>',
        unsafe_allow_html=True,
    )
    page = st.radio(
        "研究工作区",
        ["总览", "回测明细", "行情观察", "稳健性", "执行边界"],
        index=0,
        horizontal=True,
        label_visibility="collapsed",
    )

    if page in ("总览", "回测明细", "稳健性"):
        try:
            result = run_backtest_cached(**params)
        except Exception as e:
            st.error(f"回测失败：{e}")
            return
        nav = result["nav"]
        daily_logs = result.get("daily_logs", [])
        rep = build_report(nav, result["orders"], daily_logs)
        # 把 bars 放到 result 里，给 benchmark 用
        result["bars"] = make_synthetic_bars(params["n_stocks"], params["n_days"], seed=params["seed"]) \
            if not params["use_real"] else sample_real_data(params["n_stocks"], params["n_days"], seed=params["seed"])

    if page == "总览":
        page_overview(params, result, nav, rep)
    elif page == "回测明细":
        page_backtest(params, result, nav, rep, daily_logs)
    elif page == "行情观察":
        if params["use_real"]:
            bars = sample_real_data(params["n_stocks"], params["n_days"], seed=params["seed"])
        else:
            bars = make_synthetic_bars(params["n_stocks"], params["n_days"], seed=params["seed"])
        page_market(bars)
    elif page == "稳健性":
        page_overfit(params)
    elif page == "执行边界":
        page_broker()


if __name__ == "__main__":
    main()
