# a_share_quant

A 股月频量化系统（V1 完整闭环 + Streamlit Web App）

## 当前状态

**V1 全部 14 阶段完成** + 性能优化（2.5x）+ 同花顺风格 Web App（多页 + K 线 + 基准对比 + PNG 导出 + 真实数据接入）。

- **152/152 测试通过**（含性能基线保护）
- **真实规模回测**：500×500 → 2.7s · 800×800 → 9.3s
- **Web App**：`streamlit run app.py` 启动同花顺风格 5 页交互面板

## 必读

- [`strategy_spec.md`](./strategy_spec.md) — 策略规范的**唯一事实来源**
- [`app.py`](./app.py) — Web App 入口

## 快速开始

```bash
make install        # 创建 venv + 装所有依赖
make test           # 跑全部 145 项测试
make app            # 启动 Web App → 浏览器打开 http://localhost:8501
```

## 14 阶段路线图（全部完成 ✅）

| # | 阶段 | 状态 | 关键产物 |
|---|---|---|---|
| 1 | 边界 | ✅ | `strategy_spec.md` + `config/strategy.yaml` |
| 2 | 环境 | ✅ | `.venv` (Py 3.11) + 9 核心包 + Jupyter kernel |
| 3 | 项目骨架 | ✅ | `src/data/schema.py` 字段契约 + `config.py` frozen dataclass |
| 4 | 数据 | ✅ | `downloader.py` (baostock) + `cleaner.py` (raw→processed) |
| 5 | 验证 | ✅ | `validator.py` 8 项 check + ERROR/WARNING 分级 |
| 6 | 股票池 | ✅ | `stock_pool.py` 双层 candidate + tradable |
| 7 | 信号 | ✅ | `momentum.py` (120-5) + `signal.py` 等权 top 10 |
| 8 | 订单 | ✅ | `broker.py` 6 状态机 + `costs.py` 佣金/印花税 |
| 9 | 引擎 | ✅ | `engine.py` T+1 开盘成交 + 整手 + 5% 现金缓冲 |
| 10 | 风控 | ✅ | `controls.py` 6 check + TRADING_ENABLED 总开关 |
| 11 | 报告 | ✅ | `performance.py` 13 指标 + rich TUI |
| 12 | 过拟合 | ✅ | `overfit.py` 5 项稳健性测试 |
| 13 | 模拟盘 | ✅ | `paper.py` 幂等每日任务 |
| 14 | 实盘 | ✅ | `broker/` 框架（V1 手动模式，不连券商） |

## 性能

- 性能优化：6.9s → 2.7s（500×500 数据，**2.5x 加速**）
- 真实规模（800×800 = 64万行）：9.3s
- 性能基线测试保护（`tests/test_performance.py`）

## Web App（同花顺风格，5 页）

```bash
make app
# 浏览器打开 http://localhost:8501
```

**5 个页面**：
- 📊 **概览**：4 个 KPI 大数字 + 净值曲线 vs 基准 + 风险信号 + 导出 PNG
- 📋 **回测**：详细指标 + 交易统计 + 年度收益 + 调仓时间线 + 下载 CSV
- 📊 **行情**：单只股票 **K 线**（红涨绿跌 + 成交量 + MA20）+ 导出 PNG
- 🔬 **过拟合**：调仓频率 5/10/20 + lookback 110/120/130 + 成本 ×2 压力测试
- 🏦 **实盘**：broker 状态 + emergency stop 入口（V1 手动模式）

**数据源**：
- 默认：合成数据（不联网）
- 真实：`data/processed/bars.parquet` 存在时自动用真实 baostock 数据（需先 `make self-test --keep-raw` 或本机 `make download`）
- 全市场低内存导入：`data/processed/bars_by_code/` 存在时 Web App 按选中股票读取分区数据。

真实数据和实验结果默认只留在本机。先运行 `make snapshot-data`，它会为当前
`processed/` 数据写入逐文件 SHA-256 快照；每次 CLI 回测/稳健性实验会自动写入
`results/run_manifests/`，记录数据快照、代码提交、配置哈希、参数、指标与制品路径。
这些文件与行情数据均被 Git 忽略，研究状态始终为 `RESEARCH_ONLY`。

**A 股惯例**：红涨绿跌、▲/▼ Unicode、HEAVY box 标题、ROUNDED 区块、暗色渐变 KPI 卡片。

## CLI

```bash
python -m src                       # 阶段 3 dry run
python -m src download              # 拉数据（需联网）
python -m src clean                 # raw → processed
python -m src self-test --keep-raw  # fixture 跑管道
python -m src validate --asof YYYY-MM-DD
python -m src snapshot-data          # 冻结本地 processed 数据快照
python -m src snapshot-data --verify data/metadata/data-xxxx.json
python -m src backtest              # 回测 + 同花顺风格 rich 报告
python -m src overfit --split-date YYYY-MM-DD
python -m src paper --asof YYYY-MM-DD
python -m src broker --action {status,emergency-stop,clear-stop}
```

## 边界

V1 明确**不做**：
- ❌ 机器学习预测涨跌
- ❌ 分钟级 / 高频
- ❌ 新闻、大模型选股
- ❌ 10+ 因子混合
- ❌ 自动实盘下单（broker 层只手动）
- ❌ 财务因子
- ❌ 融资融券 / 北交所 / ETF / 可转债

## 目录结构

```
a_share_quant/
├── app.py                     # Web App 入口（streamlit run app.py）
├── strategy_spec.md           # 唯一事实来源
├── config/strategy.yaml       # 与 spec §7 对齐的配置
├── requirements.txt
├── Makefile
├── src/
│   ├── __main__.py            # CLI 入口
│   ├── config.py              # frozen dataclass 配置加载
│   ├── data/                  # 下载/清洗/验证/字段契约
│   ├── universe/              # 股票池
│   ├── factors/               # 动量因子
│   ├── strategy/              # 信号
│   ├── backtest/              # 引擎/broker/cost/模拟盘
│   ├── broker/                # 实盘框架（V1 手动模式）
│   ├── risk/                  # 风控
│   └── reports/               # 报告（performance/rich/overfit）
└── tests/                     # 145 项
```
