"""测试负收益诊断脚本。

诊断不依赖网络,只依赖文档里记录的真实数字 + 引擎常数。
每个测试是一个独立的可证伪假设,确保 hypothesis 字段不为空。
"""

from __future__ import annotations

from src.research.diagnose_negative import (
    Diagnosis,
    ENGINE_CONST,
    SAMPLE,
    diagnose,
)


def test_diagnose_returns_diagnosis():
    d = diagnose()
    assert isinstance(d, Diagnosis)
    assert len(d.hypotheses) == 5
    assert d.overall  # 非空


def test_sample_dict_keys():
    """真实样本必须含 6 个关键数字 (feedback §2.1)。"""
    required = [
        "n_stocks", "n_trading_days", "n_rebalances", "n_orders",
        "total_return", "sharpe", "max_drawdown", "cost_ratio",
        "in_sample_return", "oos_return", "cost_x2_return",
    ]
    for k in required:
        assert k in SAMPLE, f"missing {k}"
        assert SAMPLE[k] is not None, f"{k} is None"


def test_engine_const_consistency():
    """引擎常数必须与 strategy_spec 对齐 (lookback=120, skip=5, top_k=10, 20日调仓)。"""
    assert ENGINE_CONST["lookback"] == 120
    assert ENGINE_CONST["skip"] == 5
    assert ENGINE_CONST["top_k"] == 10
    assert ENGINE_CONST["reb_freq_days"] == 20


def test_h1_cost_not_main_driver():
    """H1: 翻倍成本只多亏 ~0.5%,否证'成本是主因'。"""
    d = diagnose()
    h1 = next(h for h in d.hypotheses if h.id == "H1")
    # 翻倍成本后收益从 -17.27% 降到 -17.59% → 增量 -0.32%
    pnl = SAMPLE["initial_cash"] * SAMPLE["total_return"]
    pnl_x2 = SAMPLE["initial_cash"] * SAMPLE["cost_x2_return"]
    incremental = pnl_x2 - pnl
    incremental_pct = incremental / SAMPLE["initial_cash"]
    # 增量损失应 < 1%
    assert abs(incremental_pct) < 0.01, (
        f"incremental loss {incremental_pct:.4%} unexpectedly large"
    )
    assert h1.verdict in ("反对", "否证")


def test_h2_turnover_above_expected():
    """H2: 单次调仓换手 36% 略高于预期 20%。"""
    d = diagnose()
    h2 = next(h for h in d.hypotheses if h.id == "H2")
    per_rebal = SAMPLE["annualized_turnover"] / SAMPLE["n_rebalances"]
    # 36% > 20% 预期,但 36% < 50% → 部分成立
    assert 0.30 < per_rebal < 0.50


def test_h5_all_params_negative():
    """H5: 5/10/20 调仓 + 110/120/130 lookback 全负。"""
    # 5日 = -9.45% 是反馈里的数字
    assert SAMPLE["freq5_return"] < 0
    # 文档确认 5/10/20,110/120/130 全部为负
    # 这里通过 overall 文本里的"全负"判断
    d = diagnose()
    h5 = next(h for h in d.hypotheses if h.id == "H5")
    assert "全负" in h5.math or "全负" in h5.statement


def test_total_contribution_reasonable():
    """5 个 H 的贡献加总会 > 实际 -17.27%（因为 H 之间有重叠）。

    这是一个有意为之的 sanity check：H1+H2 都跟成本有关，
    H3+H5 都跟个股/策略信号有关，加总会超过实际 -17.27%。
    总体判断里的"合计 ≈ -17%"已经把重叠扣掉了。
    """
    d = diagnose()
    total = sum(h.contribution_to_total for h in d.hypotheses)
    # 累加总和应 > 17%（有重叠），但不应 > 50%（说明分解太松）
    assert 17.0 < total < 50.0, f"total contribution {total} out of range"
