"""A-Share Quant V1 source root.

模块划分（与 strategy_spec.md 对齐）：

- `src.data`     : 行情下载、清洗、验证（阶段 4-5 实现）
- `src.universe` : 股票池（阶段 6 实现）
- `src.factors`  : 因子计算（阶段 7 实现）
- `src.strategy` : 信号与组合（阶段 7-8 实现）
- `src.backtest` : 回测引擎、撮合、成本（阶段 8-9 实现）
- `src.risk`     : 风控（阶段 10 实现）
- `src.reports`  : 报告生成（阶段 11 实现）

V1 阶段只定义模块边界与对外接口；不写业务逻辑。
"""

__version__ = "0.1.0"
