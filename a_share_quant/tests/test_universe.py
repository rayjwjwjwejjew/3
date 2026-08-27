"""股票池测试：覆盖 candidate 与 tradable 两层筛选。"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.data.schema import (
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_ST,
    COL_SUSPENDED,
    COL_VOL,
    make_empty_bars,
)
from src.universe.stock_pool import (
    UniverseSnapshot,
    build_candidate_universe,
    build_tradable_universe,
    build_universe_snapshot,
)


def _gen_bars(
    codes: list[str],
    start: str = "2024-01-02",
    n_days: int = 200,
    base_prices: dict[str, float] | None = None,
    st_set: set[str] | None = None,
    suspended_dates: dict[str, set[str]] | None = None,
    amounts: dict[str, float] | None = None,
) -> pd.DataFrame:
    """按 code × day 合成 bars。"""
    st_set = st_set or set()
    suspended_dates = suspended_dates or {}
    amounts = amounts or {}
    base = base_prices or {c: 10.0 for c in codes}
    dates = pd.date_range(start, periods=n_days, freq="B")

    rows = []
    for code in codes:
        for d in dates:
            p = base[code]
            susp = d.strftime("%Y-%m-%d") in suspended_dates.get(code, set())
            rows.append({
                COL_CODE: code,
                COL_DATE: d,
                COL_CLOSE: p if not susp else np.nan,
                COL_LIMIT_UP: round(p * 1.10, 2) if not susp else np.nan,
                COL_LIMIT_DOWN: round(p * 0.90, 2) if not susp else np.nan,
                COL_SUSPENDED: susp,
                COL_ST: code in st_set,
                COL_AMOUNT: (amounts.get(code, 200_000_000.0) if not susp else 0.0),
                COL_VOL: 1_000_000,
            })
    return pd.DataFrame(rows)


def _basic(codes: list[str], list_date: str = "1999-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        COL_CODE: codes,
        "list_date": pd.to_datetime([list_date] * len(codes)),
        "delist_date": pd.NaT,
    })


# ===== candidate_universe =====
def test_candidate_excludes_new_listing():
    """上市未满 60 天的股票应被剔除。"""
    bars = _gen_bars(["600000", "600001"], start="2024-01-02", n_days=200)
    # 600001 在 2024-07-15 才上市；asof=8-1 → 不足 60 天
    sb = pd.DataFrame([
        {COL_CODE: "600000", "list_date": pd.to_datetime("1999-01-01"), "delist_date": pd.NaT},
        {COL_CODE: "600001", "list_date": pd.to_datetime("2024-07-15"), "delist_date": pd.NaT},
    ])
    cand = build_candidate_universe(bars, sb, asof_date=date(2024, 8, 1))
    codes = set(cand[COL_CODE].astype(str))
    assert "600000" in codes
    assert "600001" not in codes  # 上市不足 60 天


def test_candidate_excludes_st():
    bars = _gen_bars(["600000", "600002"], st_set={"600002"})
    sb = _basic(["600000", "600002"])
    cand = build_candidate_universe(bars, sb, asof_date=date(2024, 8, 1))
    codes = set(cand[COL_CODE].astype(str))
    assert "600000" in codes
    assert "600002" not in codes


def test_candidate_excludes_suspended_today():
    today = "2024-08-01"  # 周一
    bars = _gen_bars(["600000", "600003"], suspended_dates={"600003": {today}})
    sb = _basic(["600000", "600003"])
    cand = build_candidate_universe(bars, sb, asof_date=date(2024, 8, 1))
    codes = set(cand[COL_CODE].astype(str))
    assert "600000" in codes
    assert "600003" not in codes


def test_candidate_excludes_low_liquidity():
    bars = _gen_bars(["600000", "600004"], amounts={"600004": 10_000_000.0})  # 1kw，<1亿
    sb = _basic(["600000", "600004"])
    cand = build_candidate_universe(bars, sb, asof_date=date(2024, 8, 1))
    codes = set(cand[COL_CODE].astype(str))
    assert "600000" in codes
    assert "600004" not in codes


def test_candidate_excludes_delisted():
    bars = _gen_bars(["600000", "600005"])
    sb = pd.DataFrame([
        {COL_CODE: "600000", "list_date": pd.to_datetime("1999-01-01"), "delist_date": pd.NaT},
        {COL_CODE: "600005", "list_date": pd.to_datetime("2000-01-01"), "delist_date": pd.to_datetime("2024-01-01")},
    ])
    cand = build_candidate_universe(bars, sb, asof_date=date(2024, 8, 1))
    codes = set(cand[COL_CODE].astype(str))
    assert "600005" not in codes


def test_candidate_empty_when_no_bars():
    """bars 为空：返回空 code 表，不报错。"""
    sb = _basic(["600000"])
    cand = build_candidate_universe(make_empty_bars(), sb, asof_date=date(2024, 8, 1))
    assert len(cand) == 0


# ===== tradable_universe =====
def test_tradable_buy_excludes_at_limit_up():
    """当日已涨停 → 不能买入（明天大概率继续封板）。"""
    today = "2024-08-01"
    bars = _gen_bars(["600000", "600099"])
    # 把 600099 当日 close 改成 = limit_up（涨停）
    mask = (bars[COL_CODE] == "600099") & (bars[COL_DATE] == pd.Timestamp(today))
    bars.loc[mask, COL_CLOSE] = bars.loc[mask, COL_LIMIT_UP]
    buy, _sell = build_tradable_universe(bars, asof_date=date(2024, 8, 1))
    codes = set(buy[COL_CODE].astype(str))
    assert "600000" in codes
    assert "600099" not in codes


def test_tradable_sell_excludes_at_limit_down():
    """当日已跌停 → 不能卖出。"""
    today = "2024-08-01"
    bars = _gen_bars(["600000", "600199"])
    mask = (bars[COL_CODE] == "600199") & (bars[COL_DATE] == pd.Timestamp(today))
    bars.loc[mask, COL_CLOSE] = bars.loc[mask, COL_LIMIT_DOWN]
    _buy, sell = build_tradable_universe(bars, asof_date=date(2024, 8, 1))
    codes = set(sell[COL_CODE].astype(str))
    assert "600000" in codes
    assert "600199" not in codes


def test_tradable_excludes_suspended():
    today = "2024-08-01"
    bars = _gen_bars(["600000", "600299"], suspended_dates={"600299": {today}})
    buy, sell = build_tradable_universe(bars, asof_date=date(2024, 8, 1))
    codes = (set(buy[COL_CODE]) | set(sell[COL_CODE]))
    assert "600299" not in codes


# ===== snapshot =====
def test_snapshot_returns_dataclass():
    bars = _gen_bars(["600000"])
    sb = _basic(["600000"])
    snap = build_universe_snapshot(bars, sb, asof_date=date(2024, 8, 1))
    assert isinstance(snap, UniverseSnapshot)
    assert "600000" in snap.candidate_codes
    assert "600000" in snap.tradable_buy_codes
    assert "600000" in snap.tradable_sell_codes
    # 三个集合都是 [code] 单列
    assert list(snap.candidate.columns) == [COL_CODE]


def test_snapshot_empty_inputs():
    snap = build_universe_snapshot(make_empty_bars(), pd.DataFrame(columns=[COL_CODE, "list_date", "delist_date"]),
                                    asof_date=date(2024, 8, 1))
    assert len(snap.candidate) == 0
    assert len(snap.tradable_buy) == 0
    assert len(snap.tradable_sell) == 0
