# Broker 接入层

⚠️ **V1 阶段：本目录只提供接口契约和手动模式，**绝不连接任何真实券商**。**

## 当前状态

- ✅ `interface.py`：所有券商适配器必须实现的抽象基类
- ✅ `manual.py`：手动确认模式（生成订单 → 打印 → 人工在券商 App 内下单）
- ✅ `emergency.py`：紧急停止（写文件即停）
- ❌ 任何真实券商（华泰/中信/东财等）适配器：**V1 不实现**

## 启用实盘前必须满足

按 spec §14：

1. V2 阶段过拟合测试全部通过
2. V3 阶段模拟盘稳定运行 ≥ 30 个交易日
3. 已向开户券商完成程序化交易报告（监管要求，证监会《证券市场程序化交易管理规定》）
4. API 密钥通过环境变量提供（**禁止写入 Git**）
5. 已阅读并同意 `docs/risk_disclosure.md`（V1 阶段占位）

## 紧急停止

任何时刻（包括回测、模拟盘、实盘）调用：

```python
from src.broker import emergency_stop
emergency_stop("reason here")
```

会在 `~/.a_share_quant_emergency` 写标记文件，并自动 disable 风控总开关。
任何后续的实盘提交都会被拒绝。

清除：

```python
from src.broker import clear_emergency_stop
clear_emergency_stop()
```

## 接入新券商的标准流程

1. 实现 `BrokerAdapter` 子类（`get_account` / `submit_order` / `cancel_order` / `is_connected`）
2. 添加进 `src/broker/<券商名>.py`
3. CLI 加 `--broker <券商名>` 参数
4. 在小规模（建议首笔 ≤ 1w 元）上跑通
5. 通过 `emergency_stop` 测试：手动触发后能正确停止
