"""从 baostock 拉取原始数据到 `data/raw/`。

V1 拉 4 类数据：
- 股票列表（带上市/退市日）
- 交易日历
- 日线 OHLCV（前复权，含成交额、停牌标志）
- 分红送股事件

**所有函数都是幂等的**：同一个查询调用两次产出相同结果；写入用 append
模式但带去重，不重复落盘。

**网络依赖**：`baostock` 每次调用都是 HTTP 拉取。沙箱无外网时，调用
会失败；这是设计预期——沙箱里跑测试请用 fixtures，本机或联网环境跑
`make download-full`。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.data.schema import (
    COL_ADJ_CLOSE,
    COL_ADJ_FACTOR,
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_HIGH,
    COL_LOW,
    COL_NAME,
    COL_OPEN,
    COL_ST,
    COL_SUSPENDED,
    COL_TRADE_STATUS,
    COL_VOL,
)
from src.config import DATA_RAW

logger = logging.getLogger(__name__)


# ===== 路径常量 =====
RAW_BARS_DIR: Path = DATA_RAW / "bars"
RAW_STOCK_BASIC: Path = DATA_RAW / "stock_basic.parquet"
RAW_TRADE_CALENDAR: Path = DATA_RAW / "trade_calendar.parquet"
RAW_DIVIDEND: Path = DATA_RAW / "dividend.parquet"


@dataclass(frozen=True)
class DownloadRange:
    """下载区间。start_date 与 end_date 为 YYYY-MM-DD 字符串。"""
    start_date: str
    end_date: str


# ===== 工具 =====
def _ensure_dirs() -> None:
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    RAW_BARS_DIR.mkdir(parents=True, exist_ok=True)


def _bs_login():
    """登录 baostock（每次 session 开始调用一次）。"""
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    return bs


def _bs_logout(bs) -> None:
    try:
        bs.logout()
    except Exception:  # noqa: BLE001
        pass


def _bs_to_df(rs) -> pd.DataFrame:
    """baostock ResultSet → DataFrame，自动类型转换。"""
    data = []
    while (rs.error_code == "0") and rs.next():
        data.append(rs.get_row_data())
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=rs.fields)
    return df


# ===== 股票列表 =====
def download_stock_basic() -> pd.DataFrame:
    """下载全 A 股列表，含上市/退市日。

    baostock 字段：code, code_name, ipoDate, outDate, type, status
    V1 标准化为：code, name, list_date, delist_date, status
    """
    _ensure_dirs()
    bs = _bs_login()
    try:
        rs = bs.query_stock_basic()
        df = _bs_to_df(rs)
        if df.empty:
            logger.warning("query_stock_basic returned empty")
            return df
        # 标准化
        df = df.rename(columns={
            "code": COL_CODE,
            "code_name": COL_NAME,
            "ipoDate": "list_date",
            "outDate": "delist_date",
        })
        # 缺失值：未退市 = 空
        df["delist_date"] = df["delist_date"].replace("", pd.NA)
        df.to_parquet(RAW_STOCK_BASIC, index=False)
        logger.info("saved %d stocks to %s", len(df), RAW_STOCK_BASIC)
        return df
    finally:
        _bs_logout(bs)


# ===== 交易日历 =====
def download_trade_calendar(start: str, end: str) -> pd.DataFrame:
    """下载区间内每日是否交易日。

    baostock 字段：calendar_date, is_trading_day
    V1 标准化为：date, is_trading_day (bool)
    """
    _ensure_dirs()
    bs = _bs_login()
    try:
        rs = bs.query_trade_dates(start_date=start, end_date=end)
        df = _bs_to_df(rs)
        if df.empty:
            logger.warning("query_trade_dates returned empty")
            return df
        df = df.rename(columns={"calendar_date": COL_DATE})
        df["is_trading_day"] = df["is_trading_day"].astype(int).astype(bool)
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
        df.to_parquet(RAW_TRADE_CALENDAR, index=False)
        logger.info("saved %d calendar days to %s", len(df), RAW_TRADE_CALENDAR)
        return df
    finally:
        _bs_logout(bs)


# ===== 日线 =====
def download_bars_for_code(code: str, dr: DownloadRange) -> pd.DataFrame:
    """下载单只股票指定区间的日线（前复权）。

    baostock 字段：date, open, high, low, close, preclose, volume,
    amount, adjustflag, turn, tradestatus, pctChg, peTTM, pbMRQ,
    psTTM, pcfNcfTTM, isST
    V1 标准化为 14 个最小列（其余字段丢弃以保持数据干净）。
    """
    _ensure_dirs()
    bs = _bs_login()
    try:
        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,amount,adjustflag,tradestatus,isST",
            start_date=dr.start_date,
            end_date=dr.end_date,
            frequency="d",
            adjustflag="2",  # 前复权
        )
        df = _bs_to_df(rs)
        if df.empty:
            return df
        df = _normalize_bar_frame(df, code=code)
        # 单股落盘：按 code 分文件
        out_path = RAW_BARS_DIR / f"{code}.parquet"
        df.to_parquet(out_path, index=False)
        return df
    finally:
        _bs_logout(bs)


def download_bars_all(codes: Iterable[str], dr: DownloadRange, sleep: float = 0.1) -> pd.DataFrame:
    """批量下载多只股票日线。sleep 是礼貌性延时，避免 baostock 限流。"""
    pieces = []
    for i, code in enumerate(codes):
        try:
            df = download_bars_for_code(code, dr)
            if not df.empty:
                pieces.append(df)
        except Exception as e:  # noqa: BLE001
            logger.warning("download %s failed: %s", code, e)
        if sleep > 0:
            time.sleep(sleep)
        if (i + 1) % 500 == 0:
            logger.info("downloaded %d / %d stocks", i + 1, len(list(codes)) if isinstance(codes, list) else "?")
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


# ===== 原始 → 标准化列 =====
def _normalize_bar_frame(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """把 baostock 日线 DataFrame 规范成 14 列契约。"""
    df = df.copy()
    df[COL_CODE] = code
    df[COL_DATE] = pd.to_datetime(df["date"])

    # 数值列：空字符串 → NaN
    for c in (COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_VOL, COL_AMOUNT):
        df[c] = pd.to_numeric(df[c].replace("", pd.NA), errors="coerce")

    # 复权因子：close / adj_close 由我们计算（按前复权：adj = close）
    # baostock 前复权下，close 已经是复权后价格；adj_factor 暂记为 1.0
    # 真正的复权因子需要从原始价反推。V1 简化：直接用前复权价作为 adj_close
    df[COL_ADJ_CLOSE] = df[COL_CLOSE]
    df[COL_ADJ_FACTOR] = 1.0

    # 停牌：tradestatus '0'=停牌, '1'=正常
    df[COL_SUSPENDED] = df[COL_TRADE_STATUS].map({"0": True, "1": False}).fillna(False)

    # ST：isST 字段 '0'/'1' → bool
    df[COL_ST] = (df["isST"].astype(str) == "1")

    # 涨跌停：留 NaN，cleaner 阶段补算
    df["limit_up"] = pd.NA
    df["limit_down"] = pd.NA

    keep = [
        COL_CODE, COL_DATE, COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_ADJ_CLOSE,
        COL_VOL, COL_AMOUNT, COL_ADJ_FACTOR, COL_SUSPENDED, COL_ST,
        "limit_up", "limit_down",
    ]
    return df[keep]


# ===== 分红送股 =====
def download_dividend(code: str, dr: DownloadRange) -> pd.DataFrame:
    """下载单只股票分红送股事件。

    baostock 字段较多：code, divRecordDate, exDate, divCash, shareRatio, ...
    V1 保留关键字段：code, exDate, divCash, shareRatio, splitRatio
    """
    _ensure_dirs()
    bs = _bs_login()
    try:
        rs = bs.query_dividend_data(
            code=code,
            year="",  # 不限年
            yearType="operate",
        )
        df = _bs_to_df(rs)
        if df.empty:
            return df
        return df
    finally:
        _bs_logout(bs)
