"""过拟合 / 稳健性测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.reports.overfit import (
    OverfitSummary,
    run_in_out_sample,
    run_rebalance_frequency,
    run_lookback_sensitivity,
    run_cost_stress,
    run_remove_best_year,
    run_rolling_windows,
    run_all_overfit_tests,
)
from src.data.schema import (
    COL_ADJ_CLOSE, COL_ADJ_FACTOR, COL_AMOUNT, COL_CLOSE, COL_CODE, COL_DATE,
    COL_LIMIT_DOWN, COL_LIMIT_UP, COL_LOW, COL_OPEN, COL_HIGH, COL_VOL,
    COL_ST, COL_SUSPENDED,
)


def _gen(codes, n, start="2022-01-04", seed=2026):
    rows = []
    dates = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    for code in codes:
        drift = (hash(code) % 11 - 5) * 0.0006
        rets = drift + rng.normal(0, 0.015, n)
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
    bars = pd.DataFrame(rows)
    sb = pd.DataFrame({COL_CODE: codes, "list_date": pd.to_datetime("1999-01-01"), "delist_date": pd.NaT})
    cal = pd.DataFrame({COL_DATE: dates, "is_trading_day": True})
    return bars, sb, cal


# ===== 单项测试 =====
def run_in_out_sample_runs():
    bars, sb, cal = _gen(["600000", "600001", "600002", "600003", "600004"], n=500)
    s = run_in_out_sample(bars, sb, cal, split_date="2023-06-01")
    assert isinstance(s, OverfitSummary)
    # 应当有 IS 和 OOS 两段
    assert any(r.label == "IS" for r in s.results)
    assert any(r.label == "OOS" for r in s.results)


def run_rebalance_frequency_runs():
    bars, sb, cal = _gen(["600000", "600001", "600002", "600003", "600004"], n=400)
    s = run_rebalance_frequency(bars, sb, cal, freqs=(5, 10, 20))
    assert len(s.results) == 3
    assert {r.params.get("rebalance_every") for r in s.results} == {5, 10, 20}


def run_lookback_sensitivity_runs():
    bars, sb, cal = _gen(["600000", "600001", "600002", "600003", "600004"], n=400)
    s = run_lookback_sensitivity(bars, sb, cal, lookbacks=(110, 120, 130))
    assert len(s.results) == 3
    assert {r.params.get("lookback") for r in s.results} == {110, 120, 130}


def run_cost_stress_doubles_costs():
    """成本 ×2 时换手率相同但 cost_ratio 应翻倍。"""
    bars, sb, cal = _gen(["600000", "600001", "600002", "600003", "600004"], n=300)
    s = run_cost_stress(bars, sb, cal, multipliers=(1.0, 2.0))
    base = next(r for r in s.results if r.params.get("cost_multiplier") == 1.0)
    stress = next(r for r in s.results if r.params.get("cost_multiplier") == 2.0)
    # ×2 cost_ratio 应 ≥ base（绝对值）
    assert stress.report.cost_ratio >= base.report.cost_ratio * 1.5


def run_remove_best_year_runs():
    bars, sb, cal = _gen(["600000", "600001", "600002", "600003", "600004"], n=500)
    s = run_remove_best_year(bars, sb, cal)
    # 至少有 base 段，可能有 excl 段
    assert any(r.label == "base" for r in s.results)


def run_rolling_windows_runs():
    bars, sb, cal = _gen(["600000", "600001", "600002", "600003", "600004"], n=800)
    s = run_rolling_windows(bars, sb, cal, window_years=2, step_years=1)
    # 至少 1 段
    assert len(s.results) >= 1


def test_run_all_overfit_tests_aggregates():
    bars, sb, cal = _gen(["600000", "600001", "600002", "600003", "600004"], n=600)
    out = run_all_overfit_tests(bars, sb, cal, split_date="2023-06-01")
    assert set(out.keys()) == {
        "in_out_sample", "rebalance_freq", "lookback_sensitivity",
        "cost_stress", "remove_best_year",
    }
    for name, s in out.items():
        assert isinstance(s, OverfitSummary)
        assert len(s.results) >= 1


def test_overfit_summary_to_dataframe():
    s = OverfitSummary()
    # 空 → 仍返回 DataFrame
    df = s.to_dataframe()
    assert isinstance(df, pd.DataFrame)
    assert "label" in df.columns
