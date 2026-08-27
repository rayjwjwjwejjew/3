"""BrokerAdapter 接口契约（spec §14）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class AccountInfo:
    """账户信息。"""
    cash: float
    total_asset: float
    positions: dict[str, int]   # code -> shares
    frozen: dict[str, int] = None  # 可选：冻结持仓


@dataclass(frozen=True)
class OrderRequest:
    """单个下单请求。"""
    code: str
    side: OrderSide
    shares: int
    price: float               # 限价；市价单传 0
    limit: float | None = None  # 限价单的价格
    client_order_id: str = ""  # 幂等性 key


@dataclass
class OrderResult:
    """券商回报的成交结果。"""
    code: str
    side: OrderSide
    requested_shares: int
    filled_shares: int
    filled_price: float
    status: str  # "FILLED" / "PARTIALLY_FILLED" / "REJECTED" / "PENDING"
    broker_order_id: str = ""
    reject_reason: str = ""


class BrokerAdapter(ABC):
    """所有券商适配器必须实现的接口。"""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """查询账户信息（spec §14 第一步：只读取，不操作）。"""

    @abstractmethod
    def submit_order(self, request: OrderRequest) -> OrderResult:
        """提交订单。V1 阶段 manual.py 会在此处阻塞等待人工确认。"""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """撤销订单。"""

    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接性。"""
