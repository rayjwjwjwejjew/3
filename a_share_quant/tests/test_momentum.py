"""阶段 7 测试：动量因子 + top K 选择 + 等权。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.schema import COL_ADJ_CLOSE, COL_CODE, COL_DATE
from src.factors.momentum import compute_momentum, select_top_k, weights_from_top


def _make_bars(code: str, prices: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame({
        COL_CODE: code,
        COL_DATE: dates,
        COL_ADJ_CLOSE: prices,
    })


def test_compute_momentum_basic():
    """单只股票 130 天价格：momentum = adj_close(t-5)/adj_close(t-120) - 1。"""
    prices = [10.0] * 130
    # t-120 = index 9 (距离最后一根 130-1-120=9)，t-5 = index 124 (130-1-5=124)
    prices[124] = 11.0
    prices[9] = 10.0
    bars = _make_bars("600000", prices)
    mom = compute_momentum(bars, lookback=120, skip=5)
    val = mom.loc[("600000", pd.bdate_range("2024-01-02", periods=130)[-1])]
    assert val == pytest.approx(0.10)


def test_compute_momentum_insufficient_window():
    """上市不足 130 天的股票动量为 NaN。"""
    prices = [10.0] * 60
    bars = _make_bars("600000", prices)
    mom = compute_momentum(bars, lookback=120, skip=5)
    assert mom.dropna().empty


def test_compute_momentum_empty_input():
    empty = pd.DataFrame(columns=[COL_CODE, COL_DATE, COL_ADJ_CLOSE])
    out = compute_momentum(empty, 120, 5)
    assert out.empty


def test_compute_momentum_lookback_must_gt_skip():
    bars = _make_bars("600000", [10.0] * 30)
    with pytest.raises(ValueError):
        compute_momentum(bars, lookback=5, skip=10)


def test_select_top_k_basic():
    """3 只股票 130 天，动量分别为 A=20%、B=10%、C=5%，选 top 2。"""
    bars_list = []
    for code, ret in (("A", 0.20), ("B", 0.10), ("C", 0.05)):
        prices = [10.0] * 130
        prices[9] = 10.0
        prices[124] = 10.0 * (1 + ret)
        bars_list.append(_make_bars(code, prices))
    bars = pd.concat(bars_list, ignore_index=True)
    mom = compute_momentum(bars, 120, 5)
    asof = pd.bdate_range("2024-01-02", periods=130)[-1]
    top = select_top_k(mom, asof, top_k=2)
    assert list(top["code"]) == ["A", "B"]
    assert list(top["rank"]) == [1, 2]


def test_select_top_k_filters_candidate_codes():
    bars_list = []
    for code, ret in (("A", 0.20), ("B", 0.10)):
        prices = [10.0] * 130
        prices[9] = 10.0
        prices[124] = 10.0 * (1 + ret)
        bars_list.append(_make_bars(code, prices))
    bars = pd.concat(bars_list, ignore_index=True)
    mom = compute_momentum(bars, 120, 5)
    asof = pd.bdate_range("2024-01-02", periods=130)[-1]
    top = select_top_k(mom, asof, top_k=2, candidate_codes={"A"})
    assert list(top["code"]) == ["A"]


def test_weights_from_top_equal_weight():
    top = pd.DataFrame({"code": ["A", "B"], "momentum": [0.2, 0.1], "rank": [1, 2]})
    w = weights_from_top(top, top_k=10)
    assert w == {"A": 0.5, "B": 0.5}


def test_weights_from_top_partial():
    """3 只被选出但 top_k=10 → 按 3 只等权。"""
    top = pd.DataFrame({"code": ["A", "B", "C"], "momentum": [0.3, 0.2, 0.1], "rank": [1, 2, 3]})
    w = weights_from_top(top, top_k=10)
    assert w == {"A": 1/3, "B": 1/3, "C": 1/3}


def test_weights_from_top_empty():
    assert weights_from_top(pd.DataFrame(columns=["code"])) == {}
