"""回测报告测试。"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest.broker import Order, ORDER_FILLED
from src.reports.performance import (
    _annualized_return,
    _annualized_vol,
    _max_drawdown,
    _sharpe,
    _turnover,
    _yearly_returns,
    PerformanceReport,
    build_report,
)


def _nav_series(returns: list[float], start: float = 1_000_000.0,
                start_date: str = "2024-01-02") -> pd.DataFrame:
    """按日收益序列造 NAV。"""
    dates = pd.bdate_range(start_date, periods=len(returns) + 1)
    nav = [start]
    for r in returns:
        nav.append(nav[-1] * (1 + r))
    df = pd.DataFrame({
        "date": dates,
        "nav": nav,
        "cash": [start] * len(dates),
        "position_value": [0.0] * len(dates),
    })
    return df.set_index("date")


# ===== 指标函数 =====
def test_max_drawdown_simple():
    nav = pd.Series([100.0, 110.0, 90.0, 95.0, 120.0],
                    index=pd.bdate_range("2024-01-02", periods=5))
    dd = _max_drawdown(nav)
    # 最大回撤 110 → 90 = -18.18%
    assert dd["max_drawdown"] == pytest.approx((90 - 110) / 110)
    assert dd["end"] == nav.index[2]


def test_max_drawdown_recovery():
    """100 → 120 → 90 → 110 → 120：从 120 起回撤 -25%，回撤结束于 90，110 处修复。"""
    nav = pd.Series([100.0, 120.0, 90.0, 110.0, 120.0],
                    index=pd.bdate_range("2024-01-02", periods=5))
    dd = _max_drawdown(nav)
    assert dd["max_drawdown"] == pytest.approx((90 - 120) / 120)
    assert dd["recovery_days"] is not None
    # 90 → 110 是 2 个 B 日
    assert dd["recovery_days"] >= 2


def test_annualized_return_basic():
    """100 → 200 同日：years ≈ 0 → return = 0。"""
    nav = pd.Series([100.0, 200.0], index=[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-01")])
    ret = _annualized_return(nav)
    assert ret == 0.0


def test_annualized_return_one_year():
    start = pd.Timestamp("2023-01-02")
    end = start + pd.DateOffset(years=1)
    nav = pd.Series([100.0, 200.0], index=[start, end])
    ret = _annualized_return(nav)
    assert ret == pytest.approx(1.0, rel=1e-2)


def test_sharpe_zero_vol():
    """无波动 → 夏普 0。"""
    daily_ret = pd.Series([0.0] * 100)
    assert _sharpe(daily_ret) == 0.0


def test_turnover_simple():
    """1 笔成交 10w，nav 平均 100w → 10%。"""
    o = Order(code="X", side="BUY", shares=10_000, price=10.0)
    o.submit()
    o.fill(price=10.0, cost_total=0.0)
    # cash_flow = -100_000；abs = 100_000；nav_mean = 1_000_000
    t = _turnover([o], nav_mean=1_000_000)
    assert t == pytest.approx(0.10)


def test_yearly_returns_multi_year():
    nav = pd.Series(
        [100.0, 110.0, 121.0, 130.0, 140.0],
        index=pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31", "2026-12-31"]),
    )
    yr = _yearly_returns(nav)
    # 5 个年末点 → 5 段；首段是 0（base），其余是年化收益
    assert len(yr) == 5
    assert yr.iloc[0] == 0.0
    assert yr.iloc[1] == pytest.approx(0.10)  # 2023
    assert yr.iloc[2] == pytest.approx(0.10)  # 2024


# ===== build_report 集成 =====
def _mk_order(code, side, shares, price, status=ORDER_FILLED):
    o = Order(code=code, side=side, shares=shares, price=price)
    o.submit()
    o.fill(price=price, cost_total=5.0)
    return o


def test_build_report_basic():
    nav = _nav_series([0.001 + (0.005 if i % 2 else -0.003) for i in range(250)])
    orders = [_mk_order("X", "BUY", 1000, 10.0), _mk_order("Y", "SELL", 500, 11.0)]
    rep = build_report(nav, orders)
    assert rep.filled_trades == 2
    assert rep.start_date is not None
    assert rep.end_date is not None
    assert rep.total_return != 0
    assert rep.annualized_return != 0
    assert rep.max_drawdown <= 0
    assert rep.sharpe != 0


def test_build_report_empty():
    rep = build_report(pd.DataFrame(), [])
    assert rep.total_return == 0
    assert rep.total_trades == 0


def test_report_pretty_contains_key_fields():
    nav = _nav_series([0.001] * 100)
    orders = []
    rep = build_report(nav, orders)
    text = rep.pretty()
    assert "V1 回测报告" in text
    assert "总收益率" in text
    assert "夏普比率" in text
    assert "最大回撤" in text
    assert "成本 / 收益" in text


def test_report_to_dict_serializable():
    import json
    nav = _nav_series([0.001] * 50)
    rep = build_report(nav, [])
    d = rep.to_dict()
    # 应当可以 JSON 序列化
    json.dumps(d, default=str)
