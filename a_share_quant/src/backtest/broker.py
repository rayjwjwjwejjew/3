"""订单状态机（spec §6.3, §6.2）。

订单生命周期：

    CREATED -> SUBMITTED -> PARTIALLY_FILLED -> FILLED
                                    \-> REJECTED
                                    \-> CANCELLED

每只目标持仓的股票在调仓日产生一条订单；订单有方向、股数、价格、状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from src.data.schema import (
    ORDER_CANCELLED,
    ORDER_CREATED,
    ORDER_FILLED,
    ORDER_PARTIALLY_FILLED,
    ORDER_REJECTED,
    ORDER_SUBMITTED,
)

Side = Literal["BUY", "SELL", "HOLD"]


@dataclass
class Order:
    code: str
    side: Side
    shares: int          # 整手后的股数（100 的整数倍；SELL 可为零碎但 V1 不产生零碎）
    price: float         # 计划成交价（T+1 开盘价）
    status: str = ORDER_CREATED
    filled_shares: int = 0
    filled_price: float = 0.0
    reject_reason: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: datetime | None = None
    cash_flow: float = 0.0  # 实际现金流（正=现金流出=买入，负=现金流入=卖出）
    cost_total: float = 0.0  # 累计费用（佣金+印花税+过户费）
    cost_detail: dict = field(default_factory=dict)  # {'commission': x, 'stamp_tax': y, 'transfer_fee': z}

    def submit(self) -> None:
        if self.status != ORDER_CREATED:
            raise RuntimeError(f"cannot submit order in status {self.status}")
        self.status = ORDER_SUBMITTED

    def fill(self, price: float, cost_total: float = 0.0, cost_detail: dict | None = None) -> None:
        """全部成交。cost_total/cost_detail 由调用方（engine）从 cost 模块算好传入。"""
        if self.status not in (ORDER_SUBMITTED, ORDER_PARTIALLY_FILLED):
            raise RuntimeError(f"cannot fill order in status {self.status}")
        self.filled_shares = self.shares
        self.filled_price = price
        self.filled_at = datetime.now()
        # 现金流：BUY 流出 -shares*price；SELL 流入 +shares*price；HOLD 0
        if self.side == "BUY":
            self.cash_flow = -self.shares * price
        elif self.side == "SELL":
            self.cash_flow = self.shares * price
        else:
            self.cash_flow = 0.0
        self.cost_total = float(cost_total)
        self.cost_detail = dict(cost_detail or {})
        self.status = ORDER_FILLED

    def reject(self, reason: str) -> None:
        """被拒。"""
        if self.status not in (ORDER_CREATED, ORDER_SUBMITTED):
            raise RuntimeError(f"cannot reject order in status {self.status}")
        self.reject_reason = reason
        self.status = ORDER_REJECTED

    def cancel(self, reason: str = "") -> None:
        self.reject_reason = reason or "cancelled by user/risk"
        self.status = ORDER_CANCELLED
