"""阶段 8+9 测试：订单状态机 + 回测引擎端到端。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.broker import (
    Order,
    ORDER_CREATED,
    ORDER_FILLED,
    ORDER_REJECTED,
    ORDER_SUBMITTED,
)
from src.backtest.costs import calc_cost
from src.backtest.engine import run_backtest, _generate_orders, _lot_round
from src.data.schema import (
    COL_ADJ_CLOSE,
    COL_ADJ_FACTOR,
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_HIGH,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_LOW,
    COL_OPEN,
    COL_ST,
    COL_SUSPENDED,
    COL_VOL,
    BARS,
    make_empty_bars,
)


# ===== Order 状态机 =====
def test_order_lifecycle_buy():
    o = Order(code="600000", side="BUY", shares=1000, price=10.0)
    assert o.status == ORDER_CREATED
    o.submit()
    assert o.status == ORDER_SUBMITTED
    o.fill(price=10.0)
    assert o.status == ORDER_FILLED
    assert o.filled_shares == 1000
    # 现金流：-1000 * 10 = -10000
    assert o.cash_flow == -10000.0


def test_order_lifecycle_sell():
    o = Order(code="600000", side="SELL", shares=500, price=11.0)
    o.submit()
    o.fill(11.0)
    assert o.cash_flow == 5500.0


def test_order_reject_path():
    o = Order(code="600000", side="BUY", shares=100, price=10.0)
    o.submit()
    o.reject("at limit up")
    assert o.status == ORDER_REJECTED
    assert o.reject_reason == "at limit up"


def test_order_cannot_fill_before_submit():
    o = Order(code="600000", side="BUY", shares=100, price=10.0)
    with pytest.raises(RuntimeError):
        o.fill(10.0)


# ===== 整手 =====
def test_lot_round_down():
    assert _lot_round(1234, 100) == 1200
    assert _lot_round(99, 100) == 0
    assert _lot_round(100, 100) == 100
    assert _lot_round(0, 100) == 0
    assert _lot_round(-50, 100) == 0


# ===== 成本 =====
def test_cost_buy_shanghai():
    c = calc_cost("600000", "BUY", 100_000.0, 0.00025, 5.0, 0.001, 0.00001)
    assert c.commission == 25.0
    assert c.stamp_tax == 0.0
    assert c.transfer_fee > 0
    assert c.total == c.commission + c.transfer_fee


def test_cost_sell_shanghai_has_stamp_tax():
    c = calc_cost("600000", "SELL", 100_000.0, 0.00025, 5.0, 0.001, 0.00001)
    assert c.stamp_tax == 100.0
    assert c.commission == 25.0


def test_cost_buy_shenzhen_no_transfer_fee():
    c = calc_cost("000001", "BUY", 100_000.0, 0.00025, 5.0, 0.001, 0.00001)
    assert c.transfer_fee == 0.0


def test_cost_commission_minimum():
    """小金额：佣金 = 5 元最低。"""
    c = calc_cost("600000", "BUY", 1_000.0, 0.00025, 5.0, 0.001, 0.00001)
    assert c.commission == 5.0


def test_cost_zero_amount():
    c = calc_cost("600000", "BUY", 0, 0.00025, 5.0, 0.001, 0.00001)
    assert c.total == 0


# ===== generate_orders =====
def test_generate_orders_basic():
    """持仓 0，目标全仓 1 只股票 100% 仓位。"""
    from src.backtest.engine import Portfolio
    p = Portfolio(cash=100_000.0)
    prices = {"600000": 10.0}
    orders = _generate_orders(
        p, target_weights={"600000": 1.0}, nav=100_000.0,
        prices_t1=prices, lot_size=100, min_cash_buffer_pct=0.05,
    )
    # 95% × 100k / 10 = 9500 → 整手 9500
    assert len(orders) == 1
    assert orders[0].side == "BUY"
    assert orders[0].shares == 9500


def test_generate_orders_cash_buffer_shrinks_buy():
    """现金不足时缩股。"""
    from src.backtest.engine import Portfolio
    p = Portfolio(cash=10_000.0)
    prices = {"600000": 10.0}
    orders = _generate_orders(
        p, target_weights={"600000": 1.0}, nav=100_000.0,
        prices_t1=prices, lot_size=100, min_cash_buffer_pct=0.05,
    )
    # 可用现金 = 10k - 5k（缓冲）= 5k / 10 = 500 → 500 股
    # 但有上限：nav × target × buffer 缩股
    assert orders[0].shares <= 1000  # ≤ 1w


# ===== 端到端回测 =====
def _make_smoke_bars(codes, n_days, base=10.0, drift_per_day=0.001):
    """合成 bars：每只股票按 code 决定价格，线性漂移。"""
    rows = []
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    for code in codes:
        for i, d in enumerate(dates):
            px = base + i * drift_per_day * (hash(code) % 7 - 3)
            rows.append({
                COL_CODE: code,
                COL_DATE: d,
                COL_OPEN: px, COL_HIGH: px * 1.005, COL_LOW: px * 0.995,
                COL_CLOSE: px, COL_ADJ_CLOSE: px,
                COL_VOL: 1_000_000, COL_AMOUNT: 200_000_000.0,
                COL_ADJ_FACTOR: 1.0,
                COL_SUSPENDED: False, COL_ST: False,
                COL_LIMIT_UP: round(px * 1.10, 2),
                COL_LIMIT_DOWN: round(px * 0.90, 2),
            })
    return pd.DataFrame(rows)


def test_engine_runs_and_returns_nav():
    """150 个交易日，5 只股票，至少触发 1 次调仓。"""
    bars = _make_smoke_bars(["600000", "600001", "600002", "600003", "600004"], n_days=150)
    sb = pd.DataFrame({
        COL_CODE: ["600000", "600001", "600002", "600003", "600004"],
        "list_date": pd.to_datetime("1999-01-01"),
        "delist_date": pd.NaT,
    })
    cal = pd.DataFrame({
        COL_DATE: pd.bdate_range("2024-01-02", periods=150),
        "is_trading_day": True,
    })
    result = run_backtest(bars, sb, cal, initial_cash=1_000_000.0)
    nav = result["nav"]
    assert not nav.empty
    assert len(nav) == 150
    # 至少有一次调仓
    n_rebalances = sum(1 for log in result["daily_logs"] if log.is_rebalance)
    assert n_rebalances >= 1
    # 净值始终为正
    assert (nav["nav"] > 0).all()


def test_engine_idempotent_on_same_input():
    """同一输入跑两次：NAV 完全一致。"""
    bars = _make_smoke_bars(["600000", "600001", "600002"], n_days=100)
    sb = pd.DataFrame({COL_CODE: ["600000", "600001", "600002"],
                       "list_date": pd.to_datetime("1999-01-01"),
                       "delist_date": pd.NaT})
    cal = pd.DataFrame({COL_DATE: pd.bdate_range("2024-01-02", periods=100),
                        "is_trading_day": True})
    r1 = run_backtest(bars, sb, cal, 1_000_000.0)
    r2 = run_backtest(bars, sb, cal, 1_000_000.0)
    pd.testing.assert_frame_equal(r1["nav"], r2["nav"], check_exact=False, rtol=1e-9)
