# a_share_quant

A 股月频量化系统（V1：数据 + 简单策略 + 基础回测）

## 当前阶段

**第一阶段进行中**：确定第一版边界。

## 必读

- [`strategy_spec.md`](./strategy_spec.md) — 策略规范的**唯一事实来源**。在写任何代码前，请先读它。

## 目录结构

```
a_share_quant/
├── README.md
├── strategy_spec.md        # 策略规范（先读这个）
├── requirements.txt        # 待第二阶段填写
├── config/
│   └── strategy.yaml       # 与 spec §7 对齐的配置
├── data/
│   ├── raw/                # 原始数据（不提交 Git）
│   └── processed/          # 清洗后数据
├── src/
│   ├── data/               # 下载、清洗、验证
│   ├── universe/           # 股票池
│   ├── factors/            # 因子
│   ├── strategy/           # 信号、组合
│   ├── backtest/           # 回测引擎、撮合、成本
│   ├── risk/               # 风控
│   └── reports/            # 报告
├── tests/
├── logs/
└── results/
```

## 推进顺序

严格按 `strategy_spec.md` 末尾的 14 阶段推进。当前**只完成第一阶段**，尚未写代码。

## 阶段目标回顾

| 阶段 | 状态 | 产物 |
|---|---|---|
| 1. 边界 | ✅ 完成 | `strategy_spec.md` + `config/strategy.yaml` |
| 2. 环境 | ✅ 完成 | `.venv` + `requirements.txt` + Jupyter kernel + 4 项冒烟测试通过 |
| 3. 项目骨架 | ✅ 完成 | `src/` 各包 `__init__.py` + `data/schema.py` + `config.py` + `python -m src` 端到端通过 + 10 项测试 |
| 4. 数据 | ✅ 代码完成 | `downloader.py` + `cleaner.py` + 11 项管道测试通过；实际拉全量需联网 |
| 4. 数据 | ⏳ 待办 | 选数据源、写 `downloader.py` |
| 5. 数据验证 | ⏳ 待办 | `validator.py` + `data_quality_report.csv` |
| 6. 股票池 | ⏳ 待办 | `universe/stock_pool.py` |
| 7. 简单策略 | ⏳ 待办 | `factors/momentum.py` + `strategy/signal.py` |
| 8. 订单 | ⏳ 待办 | `backtest/broker.py` + 状态机 |
| 9. 回测引擎 | ⏳ 待办 | `backtest/engine.py` |
| 10. 风控 | ⏳ 待办 | `risk/controls.py` |
| 11. 报告 | ⏳ 待办 | `reports/performance.py` |
| 12. 过拟合测试 | ⏳ 待办 | 参数敏感性 + 样本外 |
| 13. 模拟盘 | ⏳ 待办 | 每日定时任务 |
| 14. 实盘 | ⏳ 待办 | 券商接口 + 人工确认 |
