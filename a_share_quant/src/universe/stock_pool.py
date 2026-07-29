"""股票池：candidate_universe 与 tradable_universe（spec §3）。

双层定义：
- candidate_universe  : T 日"理论上应当被纳入选择范围"的股票
- tradable_universe   : T 日"实际能成交"的股票（停牌/涨停/流动性）

候选条件（spec §3.2）：
1. 已上市 ≥ min_listing_days
2. 非 ST/*ST
3. 当日未停牌
4. 过去 20 日均成交额 ≥ min_avg_amount_20d
5. 数据完整：过去 120 日 OHLCV 与复权因子无缺失
6. 不使用未来信息

可交易条件（额外）：
- T+1 开盘价 < 涨停价（不能在涨停板买入，候选池的"可买入"过滤）
- T+1 开盘价 > 跌停价（不能在跌停板卖出，候选池的"可卖出"过滤）
- 当日未停牌
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from src.config import load_config
from src.data.schema import (
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_ST,
    COL_SUSPENDED,
)


@dataclass(frozen=True)
class UniverseSnapshot:
    """某一日的股票池快照。"""
    asof_date: pd.Timestamp
    candidate: pd.DataFrame  # 列: [code]，未排序
    tradable_buy: pd.DataFrame  # 列: [code]，可买入
    tradable_sell: pd.DataFrame  # 列: [code]，可卖出

    @property
    def candidate_codes(self) -> set[str]:
        return set(self.candidate[COL_CODE].astype(str))

    @property
    def tradable_buy_codes(self) -> set[str]:
        return set(self.tradable_buy[COL_CODE].astype(str))

    @property
    def tradable_sell_codes(self) -> set[str]:
        return set(self.tradable_sell[COL_CODE].astype(str))


# ===== 工具 =====
def _codes_only(df: pd.DataFrame) -> pd.DataFrame:
    """仅保留 code 列，dedup。"""
    if df.empty:
        return pd.DataFrame(columns=[COL_CODE])
    out = df[[COL_CODE]].drop_duplicates().reset_index(drop=True)
    out[COL_CODE] = out[COL_CODE].astype(str)
    return out


# ===== 候选池（spec §3.2）=====
def build_candidate_universe(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    asof_date,
) -> pd.DataFrame:
    """T 日 candidate_universe。

    入参：
    - bars: 截至 ≤ asof_date 的日线（含 adj_factor、is_st、is_suspended、amount）
    - stock_basic: 股票列表（含 list_date、delist_date）
    - asof_date: 当日

    返回：仅含 [code] 列的 DataFrame。
    """
    cfg = load_config()
    asof = pd.Timestamp(asof_date)

    if bars.empty or stock_basic.empty:
        return _codes_only(pd.DataFrame({COL_CODE: []}))

    # 1) 上市 ≥ min_listing_days
    basic = stock_basic.copy()
    basic[COL_CODE] = basic[COL_CODE].astype(str)
    basic["list_date"] = pd.to_datetime(basic["list_date"], errors="coerce")
    basic["delist_date"] = pd.to_datetime(basic.get("delist_date"), errors="coerce")
    # 上市满 N 个交易日 → 简单按自然日兜底（精确交易日计数交给数据层做）
    min_list_date = asof - pd.Timedelta(days=cfg.universe.min_listing_days)
    listed_long = basic[basic["list_date"].notna() & (basic["list_date"] <= min_list_date)]

    # 2) 未退市
    not_delisted = listed_long[listed_long["delist_date"].isna() | (listed_long["delist_date"] > asof)]
    active_codes = set(not_delisted[COL_CODE].astype(str))
    if not active_codes:
        return _codes_only(pd.DataFrame({COL_CODE: []}))

    # 取 T 日 bars（每个 code 当日是否停牌、ST）
    today_bars = bars[bars[COL_DATE] == asof].copy()
    today_bars[COL_CODE] = today_bars[COL_CODE].astype(str)
    # 只看仍在 active 集合里的 code
    today_bars = today_bars[today_bars[COL_CODE].isin(active_codes)]

    # 3) 非 ST
    not_st = today_bars[today_bars[COL_ST].astype(bool) == False]  # noqa: E712

    # 4) 当日未停牌
    not_suspended = not_st[not_st[COL_SUSPENDED].astype(bool) == False]  # noqa: E712

    # 5) 过去 20 日均成交额 ≥ 阈值（spec §3.2 第 4 条）
    #   允许把"过去 20 日"放宽为"过去 20 个有成交的交易日"；这里直接取最近 20 行
    bars_active = bars[bars[COL_CODE].astype(str).isin(active_codes)]
    recent = bars_active[bars_active[COL_DATE] <= asof].sort_values([COL_CODE, COL_DATE])
    last_20 = recent.groupby(COL_CODE).tail(20)
    avg_amt = last_20.groupby(COL_CODE)[COL_AMOUNT].mean()
    liquid_codes = set(avg_amt[avg_amt >= cfg.universe.min_avg_amount_20d].index.astype(str))

    # 6) 数据完整：过去 120 日无缺失（与 spec §3.2 第 5 条对齐）
    lookback = max(cfg.factor.lookback, 120)
    last_120 = recent.groupby(COL_CODE).tail(lookback)
    # 每只股票在最近 lookback 行的非空 close 数量
    cnt = last_120.groupby(COL_CODE)[COL_CLOSE].apply(lambda s: s.notna().sum())
    complete_codes = set(cnt[cnt >= lookback].index.astype(str))

    # 合并所有筛选
    keep = not_suspended[not_suspended[COL_CODE].isin(liquid_codes & complete_codes)]
    return _codes_only(keep)


# ===== 可交易池（spec §3.3）=====
def build_tradable_universe(
    bars: pd.DataFrame,
    asof_date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (tradable_buy, tradable_sell) 各自的 code 表。

    可买入：当日未停牌 且 T+1 开盘价 < 当日涨停价（避免涨停买不到）
    可卖出：当日未停牌 且 T+1 开盘价 > 当日跌停价（避免跌停卖不出）

    V1 注：baostock 不直接给 T+1 开盘价；这里采用
    "T+1 日是交易日 且 T 日未停牌 且 T 日不在涨跌停板上" 的简化口径：
    - T 日 close 已经在 limit_up：明天很可能继续涨停或一字板，买入大概率失败
    - T 日 close 已经在 limit_down：同理卖出大概率失败

    这种简化在月频策略里足够（回测仅看是否能"按计划成交"，不预测开盘）。
    """
    asof = pd.Timestamp(asof_date)
    if bars.empty:
        empty = pd.DataFrame(columns=[COL_CODE])
        return empty, empty.copy()

    today = bars[bars[COL_DATE] == asof].copy()
    if today.empty:
        empty = pd.DataFrame(columns=[COL_CODE])
        return empty, empty.copy()

    today[COL_CODE] = today[COL_CODE].astype(str)
    not_suspended = today[today[COL_SUSPENDED].astype(bool) == False]  # noqa: E712
    # 涨停：close >= limit_up（带 0.01 容差）
    up_diff = (not_suspended[COL_CLOSE] - not_suspended[COL_LIMIT_UP]).fillna(-1.0)
    dn_diff = (not_suspended[COL_LIMIT_DOWN] - not_suspended[COL_CLOSE]).fillna(-1.0)
    not_at_up = up_diff < -0.005
    not_at_dn = dn_diff < -0.005

    buyable = not_suspended[not_at_up]
    sellable = not_suspended[not_at_dn]
    return _codes_only(buyable), _codes_only(sellable)


# ===== 入口 =====
def build_universe_snapshot(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    asof_date,
) -> UniverseSnapshot:
    """一次性生成 T 日的双层股票池快照。"""
    cand = build_candidate_universe(bars, stock_basic, asof_date)
    buy, sell = build_tradable_universe(bars, asof_date)
    return UniverseSnapshot(
        asof_date=pd.Timestamp(asof_date),
        candidate=cand,
        tradable_buy=buy,
        tradable_sell=sell,
    )
