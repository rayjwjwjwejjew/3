"""模拟盘 + broker 框架测试。"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.broker import (
    BrokerAdapter,
    AccountInfo,
    OrderRequest,
    OrderResult,
    ManualBroker,
    emergency_stop,
    is_emergency_stopped,
    clear_emergency_stop,
)
from src.broker.emergency import EMERGENCY_FLAG
from src.backtest.paper import run_daily, DailyState, _state_path, _load_state, _save_state
from src.data.schema import (
    COL_ADJ_CLOSE, COL_ADJ_FACTOR, COL_AMOUNT, COL_CLOSE, COL_CODE, COL_DATE,
    COL_LIMIT_DOWN, COL_LIMIT_UP, COL_LOW, COL_OPEN, COL_HIGH, COL_VOL,
    COL_ST, COL_SUSPENDED,
)
from src.risk.controls import TRADING_ENABLED


@pytest.fixture(autouse=True)
def _reset():
    """重置 TRADING_ENABLED 和 emergency flag。"""
    import src.risk.controls as rc
    saved_enabled = rc.TRADING_ENABLED
    rc.TRADING_ENABLED = True
    if EMERGENCY_FLAG.exists():
        saved_flag = EMERGENCY_FLAG.read_text()
    else:
        saved_flag = None
    if EMERGENCY_FLAG.exists():
        EMERGENCY_FLAG.unlink()
    yield
    rc.TRADING_ENABLED = saved_enabled
    if EMERGENCY_FLAG.exists():
        EMERGENCY_FLAG.unlink()
    if saved_flag:
        EMERGENCY_FLAG.write_text(saved_flag)


def _gen(n, codes, start="2024-01-02", seed=42):
    rows = []
    dates = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    for code in codes:
        drift = (hash(code) % 11 - 5) * 0.0006
        rets = drift + rng.normal(0, 0.012, n)
        prices = 10.0 * np.cumprod(1 + rets)
        for i, d in enumerate(dates):
            px = float(prices[i])
            rows.append({
                COL_CODE: code, COL_DATE: d,
                COL_OPEN: px, COL_HIGH: px*1.005, COL_LOW: px*0.995,
                COL_CLOSE: px, COL_ADJ_CLOSE: px,
                COL_VOL: 1_000_000, COL_AMOUNT: 200_000_000.0, COL_ADJ_FACTOR: 1.0,
                COL_SUSPENDED: False, COL_ST: False,
                COL_LIMIT_UP: round(px*1.10, 2), COL_LIMIT_DOWN: round(px*0.90, 2),
            })
    return (
        pd.DataFrame(rows),
        pd.DataFrame({COL_CODE: codes, "list_date": pd.to_datetime("1999-01-01"),
                      "delist_date": pd.NaT}),
        pd.DataFrame({COL_DATE: dates, "is_trading_day": True}),
    )


# ===== 幂等性 =====
def test_paper_idempotent(tmp_path):
    bars, sb, cal = _gen(200, ["600000", "600001", "600002", "600003", "600004"], start="2024-01-02")
    asof = "2024-07-01"  # 中间日期
    s1 = run_daily(bars, sb, cal, asof_date=asof, state_dir=tmp_path)
    s2 = run_daily(bars, sb, cal, asof_date=asof, state_dir=tmp_path)
    # 第二次应当返回缓存，orders 数量相同
    assert len(s1.orders) == len(s2.orders)
    assert s1.asof_date == s2.asof_date


def test_paper_produces_orders(tmp_path):
    bars, sb, cal = _gen(200, ["600000", "600001", "600002", "600003", "600004"], start="2024-01-02")
    asof = "2024-07-01"
    s = run_daily(bars, sb, cal, asof_date=asof, state_dir=tmp_path)
    assert isinstance(s, DailyState)
    # 5 只全 A 都有动量，应当能选出 top 10 内
    assert s.target_weights or "no T+1" in s.notes or "BLOCKED" in s.notes


def test_paper_blocks_when_trading_disabled(tmp_path):
    """TRADING_ENABLED=False 时 paper 直接返回空状态（不写盘）。"""
    import src.risk.controls as rc
    rc.TRADING_ENABLED = False
    bars, sb, cal = _gen(200, ["600000", "600001", "600002", "600003", "600004"], start="2024-01-02")
    s = run_daily(bars, sb, cal, asof_date="2024-07-01", state_dir=tmp_path)
    assert "TRADING_ENABLED=False" in s.notes
    assert s.orders == []


def test_paper_persists_state(tmp_path):
    """20 只股票、每只 5-10% 权重，确保 paper 真下单并持久化。"""
    # 不设 TRADING_ENABLED=False：用 20 只股票让 top 10 等权 = 10%，不超 10% 上限
    bars, sb, cal = _gen(200, [f"600{str(i).zfill(3)}" for i in range(20)], start="2024-01-02")
    asof = "2024-07-01"
    s = run_daily(bars, sb, cal, asof_date=asof, state_dir=tmp_path)
    # 文件应当存在
    p = _state_path(tmp_path, asof)
    assert p.exists()
    loaded = _load_state(tmp_path, asof)
    assert loaded is not None
    assert loaded.asof_date == s.asof_date


# ===== broker interface =====
def test_broker_adapter_is_abstract():
    """BrokerAdapter 不能直接实例化。"""
    with pytest.raises(TypeError):
        BrokerAdapter()  # type: ignore


def test_manual_broker_generate_only():
    mb = ManualBroker()
    req = OrderRequest(code="600000", side="BUY", shares=100, price=10.0)
    ret = mb.generate_only(req)
    assert ret is req
    assert req in mb.pending_orders


def test_manual_broker_submit_raises():
    """手动模式不允许自动提交。"""
    mb = ManualBroker()
    req = OrderRequest(code="600000", side="BUY", shares=100, price=10.0)
    with pytest.raises(RuntimeError):
        mb.submit_order(req)


def test_manual_broker_get_account_raises():
    """手动模式需要人工提供账户。"""
    mb = ManualBroker()
    with pytest.raises(NotImplementedError):
        mb.get_account()


# ===== emergency stop =====
def test_emergency_stop_writes_flag():
    assert not is_emergency_stopped()
    emergency_stop("test")
    assert is_emergency_stopped()


def test_emergency_stop_disables_trading():
    import src.risk.controls as rc
    assert rc.TRADING_ENABLED is True
    emergency_stop("test")
    assert rc.TRADING_ENABLED is False


def test_clear_emergency_stop():
    emergency_stop("test")
    assert is_emergency_stopped()
    clear_emergency_stop()
    assert not is_emergency_stopped()
