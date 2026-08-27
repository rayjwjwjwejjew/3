"""造一份"小型 raw 数据"，让 downloader/cleaner 管道不联网也能跑通。

3 只股票，每只 30 个交易日的数据。覆盖：
- 主板（10% 涨跌停）
- 科创板（20% 涨跌停）
- ST（5% 涨跌停）
- 1 只含停牌日
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.config import DATA_RAW
from src.data import downloader
from src.data.schema import (
    COL_ADJ_CLOSE,
    COL_ADJ_FACTOR,
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_HIGH,
    COL_LOW,
    COL_OPEN,
    COL_ST,
    COL_SUSPENDED,
    COL_TRADE_STATUS,
    COL_VOL,
)

# 30 个连续交易日
DATES = pd.date_range("2024-01-02", periods=30, freq="B")


def _make_bars(code: str, base_price: float, is_st: bool = False,
               has_suspend: bool = False) -> pd.DataFrame:
    rows = []
    price = base_price
    for i, d in enumerate(DATES):
        # 简单线性 + 噪声
        price = price * (1.001 if not is_st else 0.999)
        susp = i == 10 and has_suspend
        if susp:
            o = h = l = c = price  # 停牌时 OHLC 留空也无妨
            amount = 0.0
            vol = 0
        else:
            o = round(price * 0.999, 2)
            h = round(price * 1.005, 2)
            l = round(price * 0.995, 2)
            c = round(price, 2)
            amount = c * 1_000_000
            vol = 1_000_000
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": vol,
            "amount": amount,
            "adjustflag": "2",
            "tradestatus": "0" if susp else "1",
            "isST": "1" if is_st else "0",
        })
    df = pd.DataFrame(rows)
    df[COL_CODE] = code
    df[COL_DATE] = pd.to_datetime(df["date"])
    for c in (COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_VOL, COL_AMOUNT):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df[COL_ADJ_CLOSE] = df[COL_CLOSE]
    df[COL_ADJ_FACTOR] = 1.0
    df[COL_SUSPENDED] = (df["tradestatus"] == "0")
    df[COL_ST] = (df["isST"] == "1")
    df["limit_up"] = pd.NA
    df["limit_down"] = pd.NA
    keep = [
        COL_CODE, COL_DATE, COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_ADJ_CLOSE,
        COL_VOL, COL_AMOUNT, COL_ADJ_FACTOR, COL_SUSPENDED, COL_ST,
        "limit_up", "limit_down",
    ]
    return df[keep]


def build_fixture() -> None:
    """写入 3 只股票的 raw/bars/{code}.parquet + raw/stock_basic.parquet + raw/trade_calendar.parquet。"""
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    downloader.RAW_BARS_DIR.mkdir(parents=True, exist_ok=True)

    # 3 只股票：主板、科创板、ST
    _make_bars("600000", 10.0).to_parquet(downloader.RAW_BARS_DIR / "600000.parquet", index=False)  # 主板
    _make_bars("688001", 50.0).to_parquet(downloader.RAW_BARS_DIR / "688001.parquet", index=False)  # 科创板
    _make_bars("000001", 8.0, is_st=True, has_suspend=True).to_parquet(downloader.RAW_BARS_DIR / "000001.parquet", index=False)  # ST + 停牌

    # stock_basic
    sb = pd.DataFrame([
        {"code": "600000", "name": "浦发银行", "list_date": "1999-11-10", "delist_date": pd.NA, "status": "1"},
        {"code": "688001", "name": "华兴源创", "list_date": "2019-07-22", "delist_date": pd.NA, "status": "1"},
        {"code": "000001", "name": "ST平安", "list_date": "1991-04-03", "delist_date": pd.NA, "status": "1"},
    ])
    sb.to_parquet(downloader.RAW_STOCK_BASIC, index=False)

    # trade_calendar：30 个工作日全是交易日
    tc = pd.DataFrame({
        "date": DATES,
        "is_trading_day": True,
    })
    tc.to_parquet(downloader.RAW_TRADE_CALENDAR, index=False)
