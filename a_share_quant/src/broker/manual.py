"""手动确认模式（spec §14 起步阶段）。

任何"接通真实账户"的代码在 V1 阶段必须先经过人工确认。
本类生成订单后**打印**给操作者，**绝不直接调用券商 API**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.broker.interface import (
    AccountInfo,
    BrokerAdapter,
    OrderRequest,
    OrderResult,
)

logger = logging.getLogger(__name__)


@dataclass
class ManualBroker(BrokerAdapter):
    """手动模式：只生成订单不提交，等操作者人工在券商 App 内下单。"""

    pending_orders: list[OrderRequest] = field(default_factory=list)
    confirmed_orders: list[OrderResult] = field(default_factory=list)

    def generate_only(self, request: OrderRequest) -> OrderRequest:
        """把订单存到 pending 队列，由人工在券商 App 内执行。"""
        self.pending_orders.append(request)
        logger.warning("MANUAL MODE: order generated but NOT submitted:")
        logger.warning("  %s %s %d @ %.2f (limit=%s)", request.code, request.side,
                       request.shares, request.price, request.limit)
        return request

    def mark_confirmed(self, request: OrderRequest, result: OrderResult) -> None:
        """操作者在券商 App 内确认成交后调用此方法记录。"""
        self.confirmed_orders.append(result)
        if request in self.pending_orders:
            self.pending_orders.remove(request)

    def get_account(self) -> AccountInfo:
        """V1 阶段：返回 0 账户，要求操作者手动提供持仓。"""
        raise NotImplementedError(
            "manual mode requires the operator to provide AccountInfo manually; "
            "see src/broker/README.md"
        )

    def submit_order(self, request: OrderRequest) -> OrderResult:
        """手动模式不允许自动提交。"""
        raise RuntimeError(
            "ManualBroker does not support auto-submit. "
            "Use generate_only() and confirm manually in your broker's app."
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        return False

    def is_connected(self) -> bool:
        return False  # manual mode 始终未连接券商 API
