"""中期动量因子（spec §4.1）。

momentum_i(t) = adj_close(t - skip) / adj_close(t - lookback) - 1

跳过最近 skip 日，剥离短期反转。
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from src.data.schema import (
    COL_ADJ_CLOSE,
    COL_CODE,
    COL_DATE,
)


# bars 通常在一次 backtest 中固定，momentum 全段只算一次。
# 用 id(bars) 作 cache key，调用方负责保证 bars 不变。
_MOMENTUM_CACHE: dict[int, tuple[pd.DataFrame, int, int, pd.Series]] = {}


def compute_momentum(
    bars: pd.DataFrame,
    lookback: int,
    skip: int,
) -> pd.Series:
    """计算每只股票在每个交易日的动量值。

    入参：
    - bars: 至少含 code, date, adj_close 三列
    - lookback: 回看窗口（交易日）
    - skip: 跳过最近 N 日

    返回：MultiIndex (code, date) -> momentum（float，NaN 表示不足窗口）

    性能：
    - 用 (id(bars), (lookback, skip)) 作 cache key；同一次 backtest 中
      engine 入口的预排序 bars 替换会让 id 变，但 bars 数据未变时仍命中。
    - 首次计算：单次 groupby + 两次 shift，向量化
    """
    if lookback <= skip:
        raise ValueError(f"lookback ({lookback}) must be > skip ({skip})")
    if bars.empty:
        return pd.Series(dtype=float, name="momentum")

    cache_key = id(bars)
    if cache_key in _MOMENTUM_CACHE:
        cached_bars, cached_lb, cached_skip, cached = _MOMENTUM_CACHE[cache_key]
        if cached_bars is bars and cached_lb == lookback and cached_skip == skip:
            return cached

    df = bars[[COL_CODE, COL_DATE, COL_ADJ_CLOSE]].copy()
    df[COL_DATE] = pd.to_datetime(df[COL_DATE])
    df = df.sort_values([COL_CODE, COL_DATE]).reset_index(drop=True)
    grp = df.groupby(COL_CODE, sort=False, observed=True)[COL_ADJ_CLOSE]
    p_recent = grp.shift(skip)
    p_old = grp.shift(lookback)
    mom = (p_recent / p_old) - 1.0
    out = pd.Series(
        mom.values,
        index=pd.MultiIndex.from_arrays(
            [df[COL_CODE].values, df[COL_DATE].values],
            names=[COL_CODE, COL_DATE],
        ),
        name="momentum",
    )
    _MOMENTUM_CACHE[cache_key] = (bars, lookback, skip, out)
    return out


def clear_momentum_cache() -> None:
    """清空动量缓存（测试或大对象回收时用）。"""
    _MOMENTUM_CACHE.clear()


def select_top_k(
    momentum: pd.Series,
    asof_date,
    top_k: int,
    candidate_codes: set[str] | None = None,
    tie_break_seed: int = 42,
) -> pd.DataFrame:
    """在 asof_date 取所有 (code, asof_date) 截面，按动量降序选 top_k。

    入参：
    - momentum: compute_momentum 的输出
    - asof_date: 截面日期
    - top_k: 选前 K
    - candidate_codes: 候选池；None 表示所有
    - tie_break_seed: 重复值的破平种子

    返回：DataFrame [code, momentum, rank]
    """
    asof = pd.Timestamp(asof_date)
    if momentum.empty:
        return pd.DataFrame(columns=[COL_CODE, "momentum", "rank"])

    # 优化：用 .loc[(slice(None), asof), :] 单次切片替 .xs() + reset_index()
    try:
        cross = momentum.loc[(slice(None), asof), ]
    except KeyError:
        return pd.DataFrame(columns=[COL_CODE, "momentum", "rank"])
    # cross 是 Series，index 是 code (MultiIndex 第 0 级)
    cross = cross.reset_index()
    cross.columns = [COL_CODE, COL_DATE, "momentum"]
    cross = cross[[COL_CODE, "momentum"]]
    cross = cross.dropna(subset=["momentum"])
    if candidate_codes is not None:
        cross = cross[cross[COL_CODE].astype(str).isin(candidate_codes)]
    if cross.empty:
        return pd.DataFrame(columns=[COL_CODE, "momentum", "rank"])

    # 破平：按动量降序，相等用 code 字典序（确定性，不需要 seed）
    cross = cross.sort_values(["momentum", COL_CODE], ascending=[False, True]).reset_index(drop=True)
    cross["rank"] = cross.index + 1
    return cross.head(top_k).reset_index(drop=True)


def weights_from_top(top: pd.DataFrame, top_k: int | None = None) -> dict[str, float]:
    """top 表 → 等权 dict[code, weight]。

    若 top 行数 < top_k，按实际行数等权（spec §4.2 第 4 条）。
    """
    if top.empty:
        return {}
    n = len(top) if top_k is None else min(len(top), top_k)
    w = 1.0 / n
    return {str(row[COL_CODE]): w for _, row in top.head(n).iterrows()}
