"""把 raw 目录的数据清洗为 processed 目录的统一表。

约定：
- `processed/bars.parquet`     —— 全 A 日线，schema 严格符合 `BARS`
- `processed/stock_basic.parquet` —— 股票列表
- `processed/trade_calendar.parquet` —— 交易日历
- `processed/dividend.parquet`  —— 分红送股事件

清洗流程是**幂等**的：同一份 raw 跑两次得到同一份 processed。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED
from src.data import downloader
from src.data.schema import (
    BARS,
    COL_ADJ_CLOSE,
    COL_ADJ_FACTOR,
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_HIGH,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_LOW,
    COL_OPEN,
    COL_ST,
    COL_SUSPENDED,
    COL_VOL,
    make_empty_bars,
)

logger = logging.getLogger(__name__)


# ===== 输出路径（每次访问时求值，方便测试 monkeypatch）=====
def PROC_BARS() -> Path:
    return DATA_PROCESSED / "bars.parquet"


def PROC_BARS_BY_CODE() -> Path:
    """分股票 processed 数据集；适用于内存有限的全市场导入。"""
    return DATA_PROCESSED / "bars_by_code"

def PROC_STOCK_BASIC() -> Path:
    return DATA_PROCESSED / "stock_basic.parquet"

def PROC_TRADE_CALENDAR() -> Path:
    return DATA_PROCESSED / "trade_calendar.parquet"

def PROC_DIVIDEND() -> Path:
    return DATA_PROCESSED / "dividend.parquet"


# ===== raw → 标准化（与 downloader 输出兼容）=====
def _normalize_raw_frame(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """把 baostock 原生列重命名/补全为 cleaner 后续处理所需的列。

    raw parquet 可能含 14 列（downloader 写出的），
    也可能含 baostock 原生列（如果用户用其他脚本下载）。
    本函数在两种输入下都给出确定的 14 列。
    """
    df = df.copy()
    if COL_CODE not in df.columns:
        df[COL_CODE] = code
    if COL_DATE not in df.columns and "date" in df.columns:
        df[COL_DATE] = pd.to_datetime(df["date"])

    # 若含 baostock 原始列，进行重命名/补全
    if "tradestatus" in df.columns and COL_SUSPENDED not in df.columns:
        df[COL_SUSPENDED] = df["tradestatus"].astype(str).map({"0": True, "1": False}).fillna(False)
    if "isST" in df.columns and COL_ST not in df.columns:
        df[COL_ST] = (df["isST"].astype(str) == "1")
    if COL_ADJ_CLOSE not in df.columns and COL_CLOSE in df.columns:
        df[COL_ADJ_CLOSE] = df[COL_CLOSE]
    if COL_ADJ_FACTOR not in df.columns:
        df[COL_ADJ_FACTOR] = 1.0
    if "limit_up" not in df.columns:
        df["limit_up"] = pd.NA
    if "limit_down" not in df.columns:
        df["limit_down"] = pd.NA
    return df


# ===== 涨跌停价计算 =====
def _is_st_or_star(df_row: pd.Series) -> bool:
    return bool(df_row.get(COL_ST, False))


def _is_kechuangban(code: str) -> bool:
    """科创板：688 开头"""
    return code.startswith("688")


def _is_chinext(code: str) -> bool:
    """创业板：30 开头"""
    return code.startswith("30")


def _limit_threshold(code: str, is_st: bool) -> float:
    """返回涨跌停阈值。"""
    if is_st:
        return 0.05
    if _is_kechuangban(code) or _is_chinext(code):
        return 0.20
    return 0.10


def add_limit_prices(df: pd.DataFrame) -> pd.DataFrame:
    """按昨收 × (1 ± 阈值) 计算每行涨跌停价。

    入参 df：必须含 code, date, close, is_st；按 (code, date) 排序。
    返回 df：新增 limit_up, limit_down 两列（已按 0.01 元取整）。
    """
    df = df.sort_values([COL_CODE, COL_DATE]).reset_index(drop=True)
    # 昨收 = 上一交易日 close
    prev_close = df.groupby(COL_CODE)[COL_CLOSE].shift(1)
    # 阈值按行计算
    threshold = df.apply(
        lambda r: _limit_threshold(str(r[COL_CODE]), _is_st_or_star(r)), axis=1
    )
    up = (prev_close * (1 + threshold)).round(2)
    dn = (prev_close * (1 - threshold)).round(2)
    # 首日无法计算 → NaN
    df[COL_LIMIT_UP] = up.where(prev_close.notna(), pd.NA)
    df[COL_LIMIT_DOWN] = dn.where(prev_close.notna(), pd.NA)
    return df


# ===== 清洗：日线 =====
def clean_bars() -> pd.DataFrame:
    """读取 raw/bars/*.parquet，合并、去重、算涨跌停、落盘 processed/bars.parquet。"""
    raw_dir = downloader.RAW_BARS_DIR
    if not raw_dir.exists():
        raise FileNotFoundError(f"raw bars dir missing: {raw_dir}")
    files = sorted(raw_dir.glob("*.parquet"))
    if not files:
        # 空 raw → 输出 schema 合规的空表（不报错，方便回测冷启动）
        logger.warning("no per-code parquet in %s; emitting empty bars table", raw_dir)
        empty = make_empty_bars()
        BARS.validate(empty)
        DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
        empty.to_parquet(PROC_BARS(), index=False)
        return empty

    pieces = []
    for f in files:
        try:
            d = pd.read_parquet(f)
        except Exception as e:  # noqa: BLE001
            logger.warning("skip unreadable %s: %s", f.name, e)
            continue
        if d.empty:
            continue
        # code = 文件名（不带扩展名）
        code = f.stem
        d = _normalize_raw_frame(d, code=code)
        pieces.append(d)
    if not pieces:
        # 输出空表（schema 合规）
        empty = make_empty_bars()
        BARS.validate(empty)
        DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
        empty.to_parquet(PROC_BARS(), index=False)
        return empty

    df = pd.concat(pieces, ignore_index=True)
    # 去重：同一 (code, date) 保留最后一条
    df = df.drop_duplicates(subset=[COL_CODE, COL_DATE], keep="last")
    # 排序
    df = df.sort_values([COL_CODE, COL_DATE]).reset_index(drop=True)
    # 类型
    df[COL_DATE] = pd.to_datetime(df[COL_DATE])
    for c in (COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_ADJ_CLOSE,
              COL_VOL, COL_AMOUNT, COL_ADJ_FACTOR, COL_LIMIT_UP, COL_LIMIT_DOWN):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 涨跌停补算
    df = add_limit_prices(df)

    # 严格满足 schema
    BARS.validate(df)
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PROC_BARS(), index=False)
    logger.info("cleaned %d bar rows → %s", len(df), PROC_BARS())
    return df


def clean_bars_partitioned() -> dict[str, int]:
    """逐股票清洗并写入分区文件，不在内存中拼接全市场 bars。

    每个 raw 文件只对应一个 code，因此去重、排序和涨跌停计算都可在单文件
    完成。返回汇总而非全量 DataFrame，供低内存导入命令报告进度。
    """
    raw_dir = downloader.RAW_BARS_DIR
    if not raw_dir.exists():
        raise FileNotFoundError(f"raw bars dir missing: {raw_dir}")
    files = sorted(raw_dir.glob("*.parquet"))
    out_dir = PROC_BARS_BY_CODE()
    out_dir.mkdir(parents=True, exist_ok=True)

    written_files = 0
    written_rows = 0
    skipped_files = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception as e:  # noqa: BLE001
            logger.warning("skip unreadable %s: %s", f.name, e)
            skipped_files += 1
            continue
        if df.empty:
            skipped_files += 1
            continue
        df = _normalize_raw_frame(df, code=f.stem)
        df = df.drop_duplicates(subset=[COL_CODE, COL_DATE], keep="last")
        df = df.sort_values([COL_CODE, COL_DATE]).reset_index(drop=True)
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
        for c in (
            COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_ADJ_CLOSE,
            COL_VOL, COL_AMOUNT, COL_ADJ_FACTOR, COL_LIMIT_UP, COL_LIMIT_DOWN,
        ):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = add_limit_prices(df)
        BARS.validate(df)
        df.to_parquet(out_dir / f.name, index=False)
        written_files += 1
        written_rows += len(df)

    logger.info(
        "partitioned %d bar rows across %d files → %s",
        written_rows,
        written_files,
        out_dir,
    )
    return {
        "files": written_files,
        "rows": written_rows,
        "skipped": skipped_files,
    }


# ===== 清洗：股票列表 =====
def clean_stock_basic() -> pd.DataFrame:
    raw_path = downloader.RAW_STOCK_BASIC
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)
    df = pd.read_parquet(raw_path)
    # 类型
    if "list_date" in df.columns:
        df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
    if "delist_date" in df.columns:
        df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce")
    df = df.drop_duplicates(subset=[COL_CODE], keep="last")
    df.to_parquet(PROC_STOCK_BASIC(), index=False)
    logger.info("cleaned %d stocks → %s", len(df), PROC_STOCK_BASIC())
    return df


# ===== 清洗：交易日历 =====
def clean_trade_calendar() -> pd.DataFrame:
    raw_path = downloader.RAW_TRADE_CALENDAR
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)
    df = pd.read_parquet(raw_path)
    df[COL_DATE] = pd.to_datetime(df[COL_DATE])
    df = df.drop_duplicates(subset=[COL_DATE], keep="last")
    df = df.sort_values(COL_DATE).reset_index(drop=True)
    df.to_parquet(PROC_TRADE_CALENDAR(), index=False)
    logger.info("cleaned %d calendar days → %s", len(df), PROC_TRADE_CALENDAR())
    return df


# ===== 入口 =====
def run_all() -> dict[str, pd.DataFrame]:
    """一次性跑完所有清洗。返回各表的 dict 引用，便于测试断言。"""
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    return {
        "bars": clean_bars(),
        "stock_basic": clean_stock_basic(),
        "trade_calendar": clean_trade_calendar(),
    }
