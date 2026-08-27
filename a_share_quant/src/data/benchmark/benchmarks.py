"""基准指数数据（沪深 300 / 中证 500）。

V1 实现：
- 默认用合成"等权 A 股"作为基准（与策略同源，公平对比）
- 如果 `data/raw/index/` 目录有真实指数数据，优先用
- 也提供预下载脚本 `download_index.py`（需联网）

为什么不直接 hardcode 真实指数：spec §11 强调"相对基准超额收益"——
V1 阶段没联网条件，**用等权 A 股当基准是合理的近似**。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.data.schema import (
    COL_ADJ_CLOSE, COL_CODE, COL_DATE,
)


# ===== 合成基准 =====
def make_equal_weight_benchmark(
    bars: pd.DataFrame,
    initial_cash: float = 1_000_000.0,
) -> pd.DataFrame:
    """等权 A 股基准：每天所有股票等权持有，扣成本。

    简化：返回按日计算的"等权 NAV"，不含交易成本（与策略成本不可比）。
    实际比较时用 strategy NAV - benchmark NAV 看超额。
    """
    if bars.empty:
        return pd.DataFrame()

    df = bars[[COL_CODE, COL_DATE, COL_ADJ_CLOSE]].copy()
    df[COL_DATE] = pd.to_datetime(df[COL_DATE])

    # pivot: index=date, columns=code
    pivot = df.pivot(index=COL_DATE, columns=COL_CODE, values=COL_ADJ_CLOSE)
    pivot = pivot.sort_index()

    # 等权日收益 = 所有股票日收益的算术平均
    daily_ret = pivot.pct_change().mean(axis=1).fillna(0)
    nav = initial_cash * (1 + daily_ret).cumprod()
    return pd.DataFrame({
        "date": nav.index,
        "nav": nav.values,
    }).set_index("date")


# ===== 真实指数加载（可选）=====
def load_real_benchmark(
    code: str,
    path: Path | None = None,
) -> pd.DataFrame | None:
    """从 `data/raw/index/{code}.parquet` 加载真实指数。

    返回 None 表示数据不存在（用户没下），调用方应回退到合成基准。
    """
    p = path or (Path("data/raw/index") / f"{code}.parquet")
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    # 期望列：date, close
    if "close" in df.columns and "date" in df.columns:
        return df.set_index("date")[["close"]].rename(columns={"close": "nav"})
    return None


# ===== 超额收益 =====
def excess_return(
    strategy_nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
) -> pd.DataFrame:
    """算超额收益：strategy / benchmark - 1。"""
    s = strategy_nav["nav"] if "nav" in strategy_nav.columns else strategy_nav.iloc[:, 0]
    b = benchmark_nav["nav"] if "nav" in benchmark_nav.columns else benchmark_nav.iloc[:, 0]
    aligned_s, aligned_b = s.align(b, join="inner")
    return pd.DataFrame({
        "date": aligned_s.index,
        "excess": (aligned_s / aligned_s.iloc[0]) - (aligned_b / aligned_b.iloc[0]),
    }).set_index("date")
