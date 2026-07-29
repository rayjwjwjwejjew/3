"""数据层：下载、清洗、验证。

计划在阶段 4-5 实现以下模块：
- `downloader.py` : 从 baostock 拉取原始数据到 `data/raw/`
- `cleaner.py`    : 原始数据 → `data/processed/`，统一字段名
- `validator.py`  : 数据质量检查，输出 `data_quality_report.csv`
- `schema.py`     : ✅ 已实现，定义字段契约
"""

from src.data.schema import (
    ALL_ORDER_STATES,
    BARS,
    BarColumns,
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
    COL_NAME,
    COL_OPEN,
    COL_RET_1D,
    COL_ST,
    COL_SUSPENDED,
    COL_TRADE_STATUS,
    COL_VOL,
    INDEX_BAR,
    ORDER_CANCELLED,
    ORDER_CREATED,
    ORDER_FILLED,
    ORDER_PARTIALLY_FILLED,
    ORDER_REJECTED,
    ORDER_SUBMITTED,
    make_empty_bars,
)

__all__ = [
    "ALL_ORDER_STATES",
    "BARS",
    "BarColumns",
    "COL_ADJ_CLOSE",
    "COL_ADJ_FACTOR",
    "COL_AMOUNT",
    "COL_CLOSE",
    "COL_CODE",
    "COL_DATE",
    "COL_HIGH",
    "COL_LIMIT_DOWN",
    "COL_LIMIT_UP",
    "COL_LOW",
    "COL_NAME",
    "COL_OPEN",
    "COL_RET_1D",
    "COL_ST",
    "COL_SUSPENDED",
    "COL_TRADE_STATUS",
    "COL_VOL",
    "INDEX_BAR",
    "ORDER_CANCELLED",
    "ORDER_CREATED",
    "ORDER_FILLED",
    "ORDER_PARTIALLY_FILLED",
    "ORDER_REJECTED",
    "ORDER_SUBMITTED",
    "make_empty_bars",
]
