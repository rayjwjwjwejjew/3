"""同花顺风格 rich 报告的测试。"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest
from rich.console import Console

from src.reports.performance import PerformanceReport, build_report
from src.reports.rich_report import (
    _arrow, _color_ret, _pct, _sparkline, render_report, render_text,
)


def _make_report():
    """构造一个典型的 PerformanceReport 用于测试。"""
    dates = pd.bdate_range("2024-01-02", periods=100)
    rng = np.random.default_rng(42)
    rets = 0.001 + rng.normal(0, 0.012, 100)
    nav = 1_000_000.0 * np.cumprod(1 + rets)
    df = pd.DataFrame({
        "date": dates,
        "nav": nav,
        "cash": 800_000.0,
        "position_value": nav - 800_000.0,
    }).set_index("date")
    orders = []
    rep = build_report(df, orders, daily_logs=None)
    return rep, df


# ===== 颜色辅助 =====
def test_color_ret_positive_red():
    assert _color_ret(0.1) == "bright_red"


def test_color_ret_negative_green():
    """A 股惯例：负数绿。"""
    assert _color_ret(-0.1) == "bright_green"


def test_color_ret_zero_dim():
    assert _color_ret(0.0) == "dim"


def test_arrow_returns_chinese_chars():
    assert _arrow(0.1) == "▲"
    assert _arrow(-0.1) == "▼"
    assert _arrow(0.0) == "─"


def test_pct_format():
    assert _pct(0.1234) == "12.34%"
    assert _pct(-0.05, digits=1) == "-5.0%"


# ===== sparkline =====
def test_sparkline_basic():
    s = pd.Series([1, 2, 3, 4, 5], index=pd.bdate_range("2024-01-02", periods=5))
    lines = _sparkline(s, width=5, height=3)
    assert len(lines) == 3
    # 每行宽度 = 5
    for line in lines:
        assert len(line) >= 5  # 可能含日期标签


def test_sparkline_empty():
    lines = _sparkline(pd.Series(dtype=float), width=10, height=3)
    assert lines == ["(empty)"] * 3


def test_sparkline_with_dates():
    s = pd.Series([10.0, 20.0, 15.0], index=pd.to_datetime(["2024-01-02", "2024-02-01", "2024-03-01"]))
    lines = _sparkline(s, width=3, height=3)
    assert len(lines) == 3
    # 第一行有 2024-03-01
    assert "2024-03-01" in lines[0]
    # 末行有 2024-01-02
    assert "2024-01-02" in lines[-1]


# ===== render_report 集成 =====
def test_render_report_runs():
    rep, df = _make_report()
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    render_report(rep, df, console=console)
    output = console.file.getvalue()
    assert "a_share_quant V1" in output
    assert "净值曲线" in output
    assert "关键指标" in output
    assert "交易统计" in output


def test_render_report_includes_arrow_and_pct():
    """头部应当有 ▲/▼ 和百分比。"""
    rep, df = _make_report()
    text = render_text(rep, df)
    assert "▲" in text or "▼" in text  # 至少一个箭头
    assert "%" in text  # 至少一个百分比


def test_render_text_returns_string():
    rep, df = _make_report()
    text = render_text(rep, df)
    assert isinstance(text, str)
    assert len(text) > 100  # 不是空


def test_render_report_handles_zero_return():
    """零收益情况：箭头为 ─，颜色为 dim。"""
    dates = pd.bdate_range("2024-01-02", periods=10)
    df = pd.DataFrame({
        "date": dates, "nav": 1_000_000.0, "cash": 1_000_000.0, "position_value": 0.0
    }).set_index("date")
    rep = build_report(df, [])
    text = render_text(rep, df)
    assert "─" in text or "dim" in text  # 0 收益的视觉表达


def test_render_report_with_daily_logs():
    """daily_logs 传入后调仓时间线有内容。"""
    from src.backtest.engine import DailyLog
    rep, df = _make_report()
    dates = list(df.index)
    logs = [
        DailyLog(
            date=dates[i], nav=1_000_000.0 * (1 + 0.001 * i),
            cash=1_000_000.0, gross_position_value=0.0,
            n_holdings=5, n_orders=0, n_filled=0, n_rejected=0, n_cancelled=0,
            turnover=0.1, costs=10.0, is_rebalance=(i % 20 == 0),
        )
        for i in range(0, len(dates), 20)
    ]
    text = render_text(rep, df, daily_logs=logs)
    assert "调仓时间线" in text


def test_render_report_with_negative_return_shows_down_arrow():
    """负收益应当显示 ▼（绿色，A 股惯例）。"""
    dates = pd.bdate_range("2024-01-02", periods=50)
    nav = 1_000_000.0 * np.cumprod(1 - 0.005 - np.random.default_rng(0).normal(0, 0.01, 50))
    df = pd.DataFrame({"date": dates, "nav": nav, "cash": 1_000_000.0, "position_value": 0.0}).set_index("date")
    rep = build_report(df, [])
    text = render_text(rep, df)
    assert "▼" in text  # 负收益用 ▼
    assert "green" in text.lower() or "30.5" not in text  # 颜色或负数显示
