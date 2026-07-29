"""风控测试。"""

from __future__ import annotations

import pytest

from src.risk.controls import (
    RiskViolation,
    TRADING_ENABLED,
    check_cash_ratio,
    check_max_holdings,
    check_max_single_weight,
    check_min_holdings,
    check_portfolio_consistency,
    check_weights_sum,
    disable_trading,
    run_pre_trade_checks,
)


@pytest.fixture(autouse=True)
def _reset_trading_flag():
    """测试前后重置 TRADING_ENABLED。"""
    import src.risk.controls as rc
    saved = rc.TRADING_ENABLED
    rc.TRADING_ENABLED = True
    yield
    rc.TRADING_ENABLED = saved


# ===== 单项 =====
def test_max_single_weight_ok():
    assert check_max_single_weight({"A": 0.10, "B": 0.05}, 0.10) == []


def test_max_single_weight_violation():
    v = check_max_single_weight({"A": 0.15}, 0.10)
    assert len(v) == 1
    assert v[0].severity == "ERROR"


def test_max_holdings_ok():
    assert check_max_holdings({f"X{i}": 0.05 for i in range(10)}, 15) == []


def test_max_holdings_violation():
    v = check_max_holdings({f"X{i}": 0.05 for i in range(20)}, 15)
    assert len(v) == 1
    assert v[0].severity == "ERROR"


def test_min_holdings_warning():
    v = check_min_holdings({"A": 0.5, "B": 0.5}, floor=5)
    assert len(v) == 1
    assert v[0].severity == "WARNING"


def test_min_holdings_zero_no_warning():
    """0 持仓时 min_holdings 警告不触发（已空仓）。"""
    assert check_min_holdings({}, floor=5) == []


def test_weights_sum_over():
    v = check_weights_sum({"A": 0.7, "B": 0.5}, tol=0.01)
    assert len(v) == 1
    assert v[0].severity == "ERROR"


def test_weights_sum_ok():
    assert check_weights_sum({"A": 0.5, "B": 0.5}, tol=0.01) == []


def test_cash_ratio_violation():
    v = check_cash_ratio(cash=10_000, nav=1_000_000, min_pct=0.05)
    assert len(v) == 1
    assert v[0].severity == "ERROR"


def test_cash_ratio_ok():
    assert check_cash_ratio(cash=50_000, nav=1_000_000, min_pct=0.05) == []


def test_cash_ratio_zero_nav():
    v = check_cash_ratio(cash=0, nav=0, min_pct=0.05)
    assert len(v) == 1


def test_portfolio_consistency_placeholder():
    """V1 阶段：placeholder 永远返回空。"""
    assert check_portfolio_consistency({}, {}) == []


# ===== 总开关 =====
def test_disable_trading_sets_flag():
    import src.risk.controls as rc
    assert rc.TRADING_ENABLED is True
    disable_trading("test reason")
    assert rc.TRADING_ENABLED is False


# ===== run_pre_trade_checks 集成 =====
def test_run_pre_trade_checks_clean():
    """权重合规 → TRADING_ENABLED 保持 True。"""
    import src.risk.controls as rc
    w = {f"X{i}": 0.05 for i in range(10)}  # 10 只 × 5% = 50% + 50% 现金
    rep = run_pre_trade_checks(w, cash=500_000, nav=1_000_000)
    # max_holdings 10 ≤ 15 OK；weights_sum 0.5 ≤ 1.01 OK；cash_ratio 0.5 ≥ 0.05 OK
    assert rc.TRADING_ENABLED is True
    assert rep.trading_enabled is True
    assert not rep.has_blocking


def test_run_pre_trade_checks_triggers_disable():
    """任一 ERROR → 自动 disable trading。"""
    import src.risk.controls as rc
    # 单只 20% > 10% 上限
    w = {"X": 0.20}
    rep = run_pre_trade_checks(w, cash=800_000, nav=1_000_000)
    assert rc.TRADING_ENABLED is False
    assert rep.trading_enabled is False
    assert rep.has_blocking
    # 应有 max_single_weight 的 ERROR
    assert any(v.rule == "max_single_weight" for v in rep.errors)


def test_run_pre_trade_checks_warning_does_not_disable():
    """仅有 WARNING：不 disable。"""
    import src.risk.controls as rc
    w = {"A": 0.5, "B": 0.5}  # 2 只 < min_holdings=5 → WARNING
    rep = run_pre_trade_checks(w, cash=0, nav=1_000_000)
    # 现金不足 5% → ERROR
    # 2 < 5 → WARNING（不阻塞）
    assert any(v.severity == "ERROR" for v in rep.violations)  # 现金
    assert any(v.severity == "WARNING" for v in rep.violations)  # 持仓数
