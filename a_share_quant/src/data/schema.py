"""数据契约：所有模块共享的字段名与类型。

**这是 V1 项目的单一字段事实来源。** 任何代码不得自行创造同义字段名
（如 `close` vs `adj_close`），如需新增字段必须先在本文件登记。

约定：
- 股票代码统一为 6 位字符串（`'600000'`，前导零不丢）；
- 日期统一为 `pandas.Timestamp` 或 `datetime.date`；
- 价格为 float，单位元；
- 成交额为 float，单位元；
- 成交量为 float，单位股（不复权、未拆分过的原始股数）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

# ===== 股票代码列 =====
COL_CODE: Final = "code"          # 6 位字符串，e.g. "600000"
COL_NAME: Final = "name"          # 股票名称
COL_DATE: Final = "date"          # 交易日期

# ===== 日线 OHLCV 列 =====
COL_OPEN: Final = "open"          # 开盘价，未复权
COL_HIGH: Final = "high"          # 最高价，未复权
COL_LOW: Final = "low"            # 最低价，未复权
COL_CLOSE: Final = "close"        # 收盘价，未复权
COL_ADJ_CLOSE: Final = "adj_close"  # 后复权收盘价
COL_VOL: Final = "volume"         # 成交量（股）
COL_AMOUNT: Final = "amount"      # 成交额（元）
COL_ADJ_FACTOR: Final = "adj_factor"  # 复权因子（后复权）

# ===== 状态列 =====
COL_SUSPENDED: Final = "is_suspended"  # bool，是否停牌
COL_ST: Final = "is_st"               # bool，是否 ST / *ST
COL_LIMIT_UP: Final = "limit_up"      # float，当日涨停价
COL_LIMIT_DOWN: Final = "limit_down"  # float，当日跌停价
COL_TRADE_STATUS: Final = "trade_status"  # str，baostock 原生状态码

# ===== 派生列 =====
COL_RET_1D: Final = "ret_1d"      # 日收益率（close-to-close）

# ===== 索引名 =====
INDEX_BAR: Final = "bar"          # 行情表的行索引名（MultiIndex: (code, date)）


@dataclass(frozen=True)
class BarColumns:
    """日线行情 DataFrame 的强制列集合。"""

    required: tuple[str, ...] = (
        COL_CODE, COL_DATE,
        COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_ADJ_CLOSE,
        COL_VOL, COL_AMOUNT, COL_ADJ_FACTOR,
        COL_SUSPENDED, COL_ST, COL_LIMIT_UP, COL_LIMIT_DOWN,
    )

    def validate(self, df: pd.DataFrame) -> None:
        """检查 DataFrame 是否满足最小字段集合，缺则抛 ValueError。"""
        missing = [c for c in self.required if c not in df.columns]
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")


# V1 唯一实例
BARS = BarColumns()


def make_empty_bars() -> pd.DataFrame:
    """生成符合 schema 的空 DataFrame（用于回测初始状态与单元测试）。"""
    return pd.DataFrame(columns=list(BARS.required))


# ===== 订单状态枚举（与 spec §6.3 对齐）=====
ORDER_CREATED: Final = "CREATED"
ORDER_SUBMITTED: Final = "SUBMITTED"
ORDER_PARTIALLY_FILLED: Final = "PARTIALLY_FILLED"
ORDER_FILLED: Final = "FILLED"
ORDER_REJECTED: Final = "REJECTED"
ORDER_CANCELLED: Final = "CANCELLED"

ALL_ORDER_STATES: Final = (
    ORDER_CREATED, ORDER_SUBMITTED, ORDER_PARTIALLY_FILLED,
    ORDER_FILLED, ORDER_REJECTED, ORDER_CANCELLED,
)
