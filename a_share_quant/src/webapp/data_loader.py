"""从 data/processed/ 加载真实 baostock 数据。

Web App 优先使用真实数据；无数据时回退到合成数据 + UI 警告。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.config import DATA_PROCESSED

PROC_BARS_PATH = DATA_PROCESSED / "bars.parquet"
PROC_STOCK_BASIC_PATH = DATA_PROCESSED / "stock_basic.parquet"
PROC_TRADE_CALENDAR_PATH = DATA_PROCESSED / "trade_calendar.parquet"


def has_real_data() -> bool:
    """检查项目目录下是否有真实 baostock 拉取的数据。"""
    return PROC_BARS_PATH.exists()


def load_real_bars() -> pd.DataFrame | None:
    """加载真实 bars；不存在返回 None。"""
    if not has_real_data():
        return None
    try:
        df = pd.read_parquet(PROC_BARS_PATH)
        return df
    except Exception:
        return None


def load_real_stock_basic() -> pd.DataFrame | None:
    if not (DATA_PROCESSED / "stock_basic.parquet").exists():
        return None
    try:
        return pd.read_parquet(DATA_PROCESSED / "stock_basic.parquet")
    except Exception:
        return None


def load_real_calendar() -> pd.DataFrame | None:
    if not (DATA_PROCESSED / "trade_calendar.parquet").exists():
        return None
    try:
        return pd.read_parquet(DATA_PROCESSED / "trade_calendar.parquet")
    except Exception:
        return None


def sample_real_data(
    n_stocks: int = 200,
    n_days: int = 500,
    seed: int = 42,
) -> pd.DataFrame | None:
    """从真实数据中随机采样 n_stocks 只股票 × 最近 n_days 天。

    用随机种子确保结果可复现（Streamlit cache）。
    """
    bars = load_real_bars()
    if bars is None or bars.empty:
        return None
    rng = np_random(seed)
    codes = bars["code"].unique()
    if len(codes) > n_stocks:
        codes = rng.choice(codes, size=n_stocks, replace=False)
    sub = bars[bars["code"].isin(codes)].copy()
    sub["date"] = pd.to_datetime(sub["date"])
    last_date = sub["date"].max()
    cutoff = last_date - pd.Timedelta(days=int(n_days * 1.5))
    sub = sub[sub["date"] >= cutoff]
    return sub


# 延迟 import 避免 streamlit 启动时强制依赖 numpy
def np_random(seed: int):
    import numpy as np
    return np.random.default_rng(seed)
