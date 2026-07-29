"""券商接入层（spec §14）。

V1 阶段：**只写框架 + 接口契约，不连接任何真实券商**。

任何要开启实盘前必须满足（spec §14）：
- 已通过 V2/V3 阶段的过拟合测试
- 已稳定运行模拟盘至少 30 个交易日
- 已向开户券商完成程序化交易报告（监管要求）
- 已启用 emergency_stop 机制
- API 密钥已在环境变量中（**禁止写入 Git**）

模块结构：
- `interface.py`     : BrokerAdapter 抽象基类（接口契约）
- `manual.py`        : 手动确认模式（生成订单 → 打印 → 等用户确认 → 提交）
- `emergency.py`     : 紧急停止（停止所有自动下单）

启用任何实盘子命令前会自动检查 TRADING_ENABLED 与 credentials，
不满足时拒绝执行。
"""

from src.broker.interface import (
    BrokerAdapter,
    AccountInfo,
    OrderRequest,
    OrderResult,
    OrderSide as AdapterOrderSide,
)
from src.broker.manual import ManualBroker
from src.broker.emergency import emergency_stop, is_emergency_stopped, clear_emergency_stop

__all__ = [
    "BrokerAdapter",
    "AccountInfo",
    "OrderRequest",
    "OrderResult",
    "AdapterOrderSide",
    "ManualBroker",
    "emergency_stop",
    "is_emergency_stopped",
    "clear_emergency_stop",
]
