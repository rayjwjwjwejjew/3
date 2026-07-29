"""性能回归测试。

不在 CI 严格卡时间——但若性能下降超过 50% 会失败。
这是 **基线**，告诉你"优化不能回退太多"。
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import run_backtest
from src.data.schema import (
    COL_ADJ_CLOSE, COL_ADJ_FACTOR, COL_AMOUNT, COL_CLOSE, COL_CODE, COL_DATE,
    COL_HIGH, COL_LIMIT_DOWN, COL_LIMIT_UP, COL_LOW, COL_OPEN, COL_ST,
    COL_SUSPENDED, COL_VOL,
)
from src.factors.momentum import clear_momentum_cache, compute_momentum


def _make_bars(n_stocks=300, n_days=300, seed=2026):
    rng = np.random.default_rng(seed)
    codes = [f"{600000 + i}" for i in range(n_stocks)]
    codes_arr = np.repeat(codes, n_days)
    dates_arr = np.tile(pd.bdate_range("2022-01-04", periods=n_days), n_stocks)
    rets = rng.normal(0, 0.015, n_stocks * n_days)
    prices = 10.0 * np.cumprod(1 + rets)
    return pd.DataFrame({
        COL_CODE: codes_arr, COL_DATE: dates_arr,
        COL_OPEN: prices, COL_HIGH: prices * 1.005, COL_LOW: prices * 0.995,
        COL_CLOSE: prices, COL_ADJ_CLOSE: prices,
        COL_VOL: 1_000_000, COL_AMOUNT: 200_000_000.0, COL_ADJ_FACTOR: 1.0,
        COL_SUSPENDED: False, COL_ST: False,
        COL_LIMIT_UP: np.round(prices * 1.10, 2), COL_LIMIT_DOWN: np.round(prices * 0.90, 2),
    })


def test_run_backtest_finishes_under_10s():
    """300 股 × 300 天的回测应在 10s 内完成。

    之前基线 ~6.9s；优化目标 < 4s。当前 ~3.3s。
    阈值设为 10s 是"宽松"——CI 慢机器也能过，但显著回退会失败。
    """
    clear_momentum_cache()
    bars = _make_bars(n_stocks=300, n_days=300)
    sb = pd.DataFrame({
        COL_CODE: bars[COL_CODE].unique(),
        "list_date": pd.to_datetime("1999-01-01"),
        "delist_date": pd.NaT,
    })
    cal = pd.DataFrame({COL_DATE: pd.bdate_range("2022-01-04", periods=300), "is_trading_day": True})

    t0 = time.time()
    run_backtest(bars, sb, cal, initial_cash=10_000_000.0)
    elapsed = time.time() - t0
    assert elapsed < 10.0, f"backtest took {elapsed:.2f}s, expected < 10s"


def test_momentum_cached_repeat_call_is_fast():
    """compute_momentum 同 bars 第二次调用应 < 5ms（缓存命中）。"""
    clear_momentum_cache()
    bars = _make_bars(n_stocks=100, n_days=200)
    # 第一次（冷）
    compute_momentum(bars, lookback=120, skip=5)
    # 第二次（热）
    t0 = time.time()
    for _ in range(10):
        compute_momentum(bars, lookback=120, skip=5)
    elapsed = (time.time() - t0) / 10
    assert elapsed < 0.005, f"cached call took {elapsed*1000:.2f}ms avg, expected < 5ms"
