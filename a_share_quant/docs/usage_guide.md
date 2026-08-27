# V1 使用指南（用户视角）

> 目标：让本机用户能在 30 分钟内**用真实数据跑通 V1**，并能判断**回测结果是否可信**。
> 写于 2026-07-29，关联报告 [`iteration_report.md`](./iteration_report.md)。

---

## 0. 心态校准

**V1 不是一个"可以拿来实盘"的系统。** 它是一个**框架**——能跑通完整闭环，但**业务正确性、策略多样性、风控严谨性都还有差距**（详见 `iteration_report.md` §1）。

**用它做什么**：
- ✅ 验证系统骨架端到端通：数据→信号→订单→回测→报告
- ✅ 跑真实 baostock 数据，看 V1 的默认动量策略**在 A 股上是否挣钱**（**V1 是月度动量，学术上能跑赢，但实盘需要更多改造**）
- ✅ 用 Web App 调参数、对比 5/10/20 调仓频率、对比 110/120/130 lookback、看风险信号

**别用它做什么**：
- ❌ 直接拿 V1 的回测结果当真去做实盘
- ❌ 看到回测年化 20% 就以为找到了圣杯（**几乎一定是过拟合或数据窥探**）

---

## 1. 本机启动（30 分钟）

### 1.1 环境要求
- Python 3.10+（V1 用 3.11 测过）
- 网络（要拉 baostock 数据）
- 1-2 GB 磁盘

### 1.2 一键流程

```bash
# 1. 克隆 + 进入
git clone <repo-url> a_share_quant
cd a_share_quant

# 2. 安装依赖 + 注册 Jupyter kernel
make install

# 3. 拉真实 A 股日线（前复权，2018 至今，约 5000 只 × 2000 天 = 1000 万行，约 10-30 分钟）
# 沙箱会失败（无网），但你本机可以
.venv/bin/python -m src download --start-date 2018-01-01

# 4. 清洗（raw → processed）
.venv/bin/python -m src clean

# 5. 数据质量检查（asof 选最新交易日）
.venv/bin/python -m src validate --asof 2025-01-15

# 6. 跑回测（默认参数：动量 120-5，top 10，每 20 日调仓）
.venv/bin/python -m src backtest --initial-cash 1000000 --out results/nav.csv

# 7. 启动 Web App 看图表
make app
# 浏览器打开 http://localhost:8501
```

**沙箱内**（无网络）走 1.2.1 替代流程：

```bash
# 1-2 同上
# 3-4 跳过（无网络）
# 2alt. 用合成数据走完
.venv/bin/python -m src self-test --keep-raw   # 写 30 天合成数据到项目 data/
.venv/bin/python -m src backtest --initial-cash 1000000
```

### 1.3 跑出来应该看到

**回测日志**（节选）：
```
rebalances: 13
orders: 157 (filled=157, rejected=0)
NAV written to: results/nav.csv
Report written to: results/report.json

V1 回测报告
============================================================
区间: 2022-01-04 → 2025-01-27
初始资金: 1,000,000.00    终值 NAV: 689,736.01

── 收益 ──
总收益率:        -31.03%        ← 合成数据是随机的，负收益正常
年化收益率:      -11.42%
夏普比率 (rf=2%): -1.437
最大回撤:        -33.34%
成本 / 收益:     0.48%          ← 这是真实指标，成本模型工作正常
```

**Web App**：左侧 4 个 KPI 卡片、中部 Plotly 净值曲线（红/绿）、下方柱状 + 风险信号。

---

## 2. 看哪些数字、怎么判断 V1 是否可信

### 2.1 关键 4 个数字（先看这几个）

| 指标 | 期望范围（合理策略） | 红旗（数据/系统问题） |
|---|---|---|
| **总收益** | 取决于市场，2018-2024 A 股动量约 5-15%/年 | > 50%/年 **几乎一定过拟合** |
| **夏普比率** | 0.5 - 1.5 | > 2 强烈可疑，< 0 看是否买入错误 |
| **最大回撤** | 15-30% | > 50% 看是不是漏处理停牌 |
| **成本/收益** | < 30% | > 50% 看是不是调仓太频繁 |

**A 股 2018-2024 真实数据回测（动量 120-5）**：学术研究显示约 **8-12% 年化**、**夏普 0.3-0.6**、**最大回撤 20-30%**。**V1 跑出来如果偏离这个范围 2 倍以上，仔细看代码**。

### 2.2 数据可信度检查（重要！）

跑出来数字前，**先看 `data_quality_report.csv`**：
```bash
cat results/data_quality_report.csv
```

| check | 含义 | 正常 |
|---|---|---|
| `no_duplicate_keys` | (code, date) 唯一 | 0 findings |
| `price_positive` | OHLC > 0 | 0 findings |
| `volume_non_negative` | volume ≥ 0 | 0 findings |
| `adj_factor_jump` | 复权因子日变化 > 50% | 0 findings（少许可 WARNING） |
| `no_data_before_listing` | 无上市前数据 | 0 findings |
| `no_future_data` | date ≤ asof | 0 findings |
| `limit_price_consistency` | 涨跌停 = 昨收 × (1 ± 阈值) | 少数 WARNING（ST 股票阈值不同） |
| `calendar_continuity` | 日历连续 | 0 findings |

**如果有 ERROR，回测结果一定不可信**。先去 baostock 重拉。

### 2.3 订单状态分布（看成交率）

跑完回测后看 `results/report.json`：
```json
"order_status_distribution": {
  "FILLED": 157,        ← 几乎应该是 100%
  "REJECTED": 0,        ← 偶尔有 1-2 个是正常的（涨跌停）
  "CANCELLED": 0
}
```

**如果 `REJECTED > 5%`**，说明**涨跌停过滤太严**或**数据问题**。V1 默认不会触发，但如果你的 top_k 太大（> 30）可能出现。

### 2.4 调仓次数 vs 总订单

`rebalances × top_k` 应当 ≈ `total_trades`。

- 如果 `total_trades` 远大于 `rebalances × top_k`：持仓有部分**没换**，是正常的（动量变化小）
- 如果 `total_trades` 远小于：检查是否被 `max_holdings` 限制或现金不足

---

## 3. 调参数看什么

Web App 左侧滑块。**建议顺序**：

### 3.1 先调"调仓频率"（5/10/20）
- **5 日**：每周调一次。交易成本高，但适应快。**期望**：低收益、高换手
- **20 日（默认）**：月度调仓。学术经典窗口。
- **60 日**：季度调仓。**期望**：高收益（如果市场有效）、低换手

**怎么判断**：用 V1 的"过拟合"页（侧栏切换）对比三个频率的总收益 / Sharpe。**如果 5 日最好，几乎一定过拟合**（真实策略不会是最高频最优）。

### 3.2 再调 "lookback"（60/120/250）
- **60 日**：短期动量。牛市有效，熊市差
- **120 日（默认）**：中期动量。学术经典
- **250 日**：长期动量（约 1 年）。熊市更稳

**怎么判断**：调 110/120/130 三个，看总收益是否稳定。**如果 ±10 的小变化就让总收益翻倍，策略不稳**。

### 3.3 最后调 "top_k"（5/10/20）
- **5 只**：集中持仓。交易成本低，但分散度差
- **10 只（默认）**：平衡
- **20 只**：分散。接近指数

### 3.4 不要调的东西
- `initial_cash` 不影响收益百分比
- `seed` 只影响合成数据

---

## 4. 决策时回看的代码位置

**当你在 V1 跑出的数字上有疑问时，按此表查代码**：

| 你看到的问题 | 先看代码位置 |
|---|---|
| 总收益远低于期望（熊市也亏很多） | `src/factors/momentum.py` —— 单因子策略本身的能力 |
| 调仓日净值突然掉 | `src/backtest/engine.py:289-298`（调仓分支）—— 调仓日用了 close 估值 |
| 停牌股票算成 0 元 | `src/backtest/engine.py:_last_valid_close()` —— 已修 |
| 涨跌停订单 REJECTED | `src/backtest/engine.py:_check_tradable()` —— 涨跌停校验 |
| 换手率看起来不对 | `src/reports/performance.py:_turnover()` —— turnover 算法 |
| 最大回撤日期不对 | `src/reports/performance.py:_max_drawdown()` —— start/end 选择 |
| 5 只股票时全 disabled | `src/risk/controls.py:check_max_single_weight` —— 单只 10% 上限 |

---

## 5. 暂停期间的建议

### 5.1 跑真实数据（最重要）

1. 在本机按 §1.1 走完
2. 看 §2.1 关键 4 个数字
3. 与"学术研究"基准对比（§2.1 表）

### 5.2 玩 Web App 1-2 小时

试 5/10/20 调仓、110/120/130 lookback、各种 top_k。
**记下来**：
- 哪个组合总收益最高
- 哪个组合最稳（Sharpe 高）
- 风险信号触发了吗

### 5.3 决定 V2 方向（3 个选择）

| 选项 | 适合 | 工作量 |
|---|---|---|
| **继续修 P1 bug** | 你跑出来发现"数字不对劲"想验证 | 1-2 天 |
| **加因子库** | 你想研究"动量之外还有啥" | 3-5 天 |
| **接真实券商** | 你准备小资金实盘 | 1-2 周 |

**我个人建议**：先修 P1 bug（特别是 `make salvage-history` 和"5 只股票时风控过严"），然后加 1-2 个因子（反转 + 低波）做对比。**真实券商等你跑了 30 天模拟盘再考虑**。

---

## 6. 沙箱环境特殊说明

本仓库是在沙箱内开发的，**沙箱特性**：
- **无网络**——`make download` 会失败。回测只能用 `make self-test --keep-raw`（合成数据）
- **`.git` 每 5 commit 左右会被重置**——已加 `make salvage-history` 备用
- **`.venv` 也可能丢失**——`make install` 重建

**你本机不会有这些问题**。这是沙箱的特殊清理。

---

## 7. 故障排查

### 7.1 `make backtest` 报 "bars not found"
```bash
.venv/bin/python -m src self-test --keep-raw   # 用合成数据
# 或
.venv/bin/python -m src download && .venv/bin/python -m src clean   # 真实数据
```

### 7.2 `make test` 挂
```bash
# 99% 是 venv 坏了
rm -rf .venv && make install && make test
```

### 7.3 git log 只剩 initial commit（沙箱特有）
```bash
make salvage-history
```

### 7.4 Web App 启动后空白
```bash
# Streamlit 缓存问题
.venv/bin/streamlit cache clear
make app
```

### 7.5 回测很慢（> 5 分钟）
```bash
# 看性能
.venv/bin/pytest tests/test_performance.py -v
# 如果 800×800 超过 20s，报告 issue
```

---

## 8. 后续步骤（暂停结束之后）

跑熟 V1 之后告诉我：
- 哪些数字"看起来对"
- 哪些"明显不对"
- 想往哪个方向走（A/B/C 三个）

我会有针对性地继续。

---

**享受跑 V1 吧。** 🎈
