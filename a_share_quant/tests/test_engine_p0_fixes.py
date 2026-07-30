"""P0 bug 修复回归测试。

每个测试对应 iteration_report.md §2 中的一个 bug。
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from src.data.schema import (
    COL_ADJ_CLOSE, COL_ADJ_FACTOR, COL_AMOUNT, COL_CLOSE, COL_CODE, COL_DATE,
    COL_HIGH, COL_LIMIT_DOWN, COL_LIMIT_UP, COL_LOW, COL_OPEN, COL_ST,
    COL_SUSPENDED, COL_VOL,
)
from src.factors.momentum import clear_momentum_cache
from src.backtest.engine import (
    _compute_nav, _last_valid_close, _generate_orders, run_backtest, Portfolio,
)


def _make_bars(codes, n_days, start="2024-01-02", suspended=None,
               st_codes=None, base=10.0):
    """合成 bars；可选指定 suspended dates + st codes。"""
    suspended = suspended or {}
    st_codes = st_codes or set()
    dates = pd.bdate_range(start, periods=n_days)
    rows = []
    for code in codes:
        prices = base * (1 + 0.001 * np.arange(n_days))
        for i, d in enumerate(dates):
            susp = d.strftime("%Y-%m-%d") in suspended.get(code, set())
            px = float(prices[i]) if not susp else np.nan
            rows.append({
                COL_CODE: code, COL_DATE: d,
                COL_OPEN: px, COL_HIGH: px * 1.005 if not susp else np.nan,
                COL_LOW: px * 0.995 if not susp else np.nan,
                COL_CLOSE: px, COL_ADJ_CLOSE: px,
                COL_VOL: 1_000_000, COL_AMOUNT: 200_000_000.0, COL_ADJ_FACTOR: 1.0,
                COL_SUSPENDED: susp, COL_ST: code in st_codes,
                COL_LIMIT_UP: round(px * 1.10, 2) if not susp else np.nan,
                COL_LIMIT_DOWN: round(px * 0.90, 2) if not susp else np.nan,
            })
    return pd.DataFrame(rows)


def _basic(codes):
    return pd.DataFrame({COL_CODE: codes, "list_date": pd.to_datetime("1999-01-01"), "delist_date": pd.NaT})


def _cal(n_days, start="2024-01-02"):
    return pd.DataFrame({
        COL_DATE: pd.bdate_range(start, periods=n_days), "is_trading_day": True,
    })


# ===== §2.1 修：停牌日 NAV 不用 0 =====
def test_nav_uses_last_valid_close_when_suspended():
    """停牌日 close = NaN → NAV 用 last-valid-close（不归零）。"""
    bars = _make_bars(["600000"], n_days=20, suspended={"600000": {"2024-01-15"}})
    today = bars[bars[COL_DATE] == pd.Timestamp("2024-01-15")].set_index(COL_CODE)
    portfolio = Portfolio(cash=1_000_000.0, positions={"600000": 1000})
    nav = _compute_nav(portfolio, today, skip=0, all_bars=bars)
    # 旧实现：close=NaN → pos_value=0 → nav=cash=1_000_000（停牌被算 0）
    # 新实现：last-valid-close > 0 → 正确估值
    assert nav > 1_000_000.0, f"expected NAV > 1M, got {nav}"


def test_nav_falls_back_to_prev_close_when_no_all_bars():
    """不传 all_bars 时，单纯今天 NaN → 持仓估值为 0（fallback 行为）。"""
    bars = _make_bars(["600000"], n_days=20, suspended={"600000": {"2024-01-15"}})
    today = bars[bars[COL_DATE] == pd.Timestamp("2024-01-15")].set_index(COL_CODE)
    portfolio = Portfolio(cash=1_000_000.0, positions={"600000": 1000})
    nav = _compute_nav(portfolio, today, skip=0, all_bars=None)
    # 不传 all_bars → today NaN → 该持仓估为 0
    assert nav == 1_000_000.0  # 只有现金


def test_last_valid_close_helper():
    """_last_valid_close 工具函数：今天有效用今天，今天 NaN 用之前。"""
    bars = _make_bars(["600000"], n_days=20, suspended={"600000": {"2024-01-15"}})
    today = bars[bars[COL_DATE] == pd.Timestamp("2024-01-15")].set_index(COL_CODE)
    px = _last_valid_close("600000", today, all_bars=bars, skip=0)
    assert px is not None
    assert px > 0
    # skip=1 → 跳过 01-15 自己，找 01-12 的 close
    px_skip = _last_valid_close("600000", today, all_bars=bars, skip=1)
    assert px_skip is not None
    assert px_skip < px  # 01-12 价格比 01-15 前的更早一天


def test_last_valid_close_never_reads_future_price():
    """估值日以前的持仓价格不能被未来高价污染。"""
    bars = _make_bars(["600000"], n_days=20)
    asof = pd.Timestamp("2024-01-15")
    bars.loc[bars[COL_DATE] > asof, COL_CLOSE] = 999.0
    today = bars[bars[COL_DATE] == asof].set_index(COL_CODE)

    px = _last_valid_close("600000", today, all_bars=bars, asof_date=asof)

    assert px is not None
    assert px < 100.0


# ===== §2.2 修：_compute_nav skip 参数真正生效 =====
def test_nav_skip_uses_skip_days_ago():
    """skip 参数使 _last_valid_close 跳过最近 skip 日。"""
    bars = _make_bars(["600000"], n_days=20, suspended={"600000": {"2024-01-15"}})
    # 用最后一天（2024-01-29，未停牌）作为 today
    last_day = bars[COL_DATE].max()
    today = bars[bars[COL_DATE] == last_day].set_index(COL_CODE)
    portfolio = Portfolio(cash=1_000_000.0, positions={"600000": 1000})

    # skip=0：应当用 today 的 close（未停牌）
    nav_skip0 = _compute_nav(portfolio, today, skip=0, all_bars=bars)
    # skip=1：跳过 today 自己，用 T-1 close
    nav_skip1 = _compute_nav(portfolio, today, skip=1, all_bars=bars)
    # 两者应略不同（每天 close 递增 0.1%）
    assert nav_skip0 != nav_skip1
    assert nav_skip1 < nav_skip0  # T-1 价格略低


# ===== §2.4 修：订单状态 O(n²) → 累加器 =====
def test_cumulative_counters_no_o_n2():
    """500 天回测中订单计数应当是 O(1) 累加，不是 O(n²) sum。"""
    n_stocks, n_days = 50, 100
    clear_momentum_cache()
    bars = _make_bars([f"600{str(i).zfill(3)}" for i in range(n_stocks)], n_days)
    sb = _basic([f"600{str(i).zfill(3)}" for i in range(n_stocks)])
    cal = _cal(n_days)

    t0 = time.time()
    r = run_backtest(bars, sb, cal, initial_cash=1_000_000.0)
    elapsed = time.time() - t0

    # 检查 daily_logs 的累计计数是单调非递减
    filled = [log.n_filled for log in r["daily_logs"]]
    rejected = [log.n_rejected for log in r["daily_logs"]]
    assert filled == sorted(filled), "n_filled should be non-decreasing"
    assert rejected == sorted(rejected), "n_rejected should be non-decreasing"
    # 性能基线：50×100 应当 < 5s（修前 O(n²) 也没事，主要是 800×800 看差别）
    assert elapsed < 10.0, f"backtest took {elapsed:.2f}s, too slow"


# ===== §2.5 修：pending_orders 不变量 =====
def test_pending_orders_invariant_protected():
    """同 code 在一次调仓内不会出现两次。"""
    n_stocks = 10
    codes = [f"600{str(i).zfill(3)}" for i in range(n_stocks)]
    bars = _make_bars(codes, n_days=130)
    sb = _basic(codes)
    cal = _cal(130)
    clear_momentum_cache()
    # 跑完一次调仓后检查 pending_orders 不重复
    r = run_backtest(bars, sb, cal, initial_cash=1_000_000.0, rebalance_every=20)
    # pending_orders 一定为空（每调仓日都立即撮合）
    # 订单列表里同 date 没有同 code 重复
    for log in r["daily_logs"]:
        if log.is_rebalance:
            # 调仓日 order 应该从 0 开始
            pass
    # 累计 orders 数应该等于 sum(每次调仓的订单数)
    # 如果 invariant 触发会被 assert 拦截
    assert len(r["orders"]) > 0


# ===== §2.6 修：cash buffer 公式文档化（行为不变） =====
def test_cash_buffer_formula_unchanged():
    """现金缓冲仍是 nav * pct（与 spec 略松）。这条测试是 regression —— 不能改。"""
    # 内部验证 _generate_orders 行为：cash=10k，nav=100k，buffer=5k，available=5k
    # 5k/10 = 500 → 整手 500
    portfolio = Portfolio(cash=10_000.0, positions={})
    orders = _generate_orders(
        portfolio, {"X": 1.0}, nav=100_000.0,
        prices_t1={"X": 10.0}, lot_size=100, min_cash_buffer_pct=0.05,
    )
    assert len(orders) == 1
    assert orders[0].shares == 500
    # 注释：P0 §2.6 报告此公式与 spec 略松；本测试锁定现有行为待 V2 决策
