# V1 自我审视与迭代建议

> 写作日期：2026-07-29
> 范围：`a_share_quant` V1 完整闭环 + 性能优化 + 同花顺风格 Web App
> 测试：152/152 通过 · 14 个 commit · 2.5x 性能加速
> 作者自评：**不夸大优势，不回避问题**

本文是 V1 完成后的"自检报告"——把所有我看代码时发现的真实问题列出来，按"该立刻修 / 该尽快修 / 可以等"分级，附优先级建议。

---

## 0. 一句话总结

**V1 完成了"系统骨架" + "同花顺风格 UI"，但还远不是"能用来做实盘决策"的系统。**
代码风格、模块边界、测试覆盖都不错；但**业务正确性、策略多样性、风险控制、运维性**都还有显著差距。

最该做的下一步：**接入真实数据 + 做 V2 阶段的多策略对比 + 行业中性化**。

---

## 1. V1 现状（诚实的优缺点）

### ✅ V1 做得好的部分
| 维度 | 评价 |
|---|---|
| **模块边界** | `src/data/universe/factors/strategy/backtest/risk/reports/broker` 边界清晰；engine 与 broker 解耦 |
| **字段契约** | `data/schema.py` 用 `Final` 常量锁死 14 列 + 6 个订单状态，6 处冗余定义全部消失 |
| **配置不可变** | `frozen=True` dataclass + 启动期加载，**支持幂等性**（spec §13）|
| **测试覆盖** | 152 个测试，含性能基线 + idempotency + 异常处理；`mocker` mock 模块属性修复 monkeypatch 陷阱 |
| **数据验证分级** | ERROR 抛 `DataQualityError` 中止回测；WARNING 只写报告不中断（spec §5.1 严格遵循）|
| **T+1 开盘成交** | spec §6.4 + §8 核心原则贯彻 |
| **同花顺风格 UI** | 红涨绿跌、▲/▼、KPI 卡片、Plotly K 线、暗色主题——**至少视觉上不输** |
| **性能** | 6.9s → 2.7s（500×500），2.5x 加速 |
| **零连接券商** | 紧急停止 + 手动模式 + 明确警告——V1 边界守住 |

### ❌ V1 的真实问题
| 维度 | 问题 |
|---|---|
| **回测引擎逻辑** | 多个 correctness bug（详见 §2） |
| **策略单一** | 只有 120-5 动量；过拟合测试用同一组参数扫描，**没真正"找稳健区"** |
| **数据真实性** | Web App 默认合成数据；真实数据接入只是"checkbox + sample"，**没有端到端验证** |
| **回测 ≠ 实盘** | 订单成交假设过于乐观（详见 §2.4）|
| **风险控制偏弱** | 单只 ≤10%、现金 ≥5% 都在动量 top 10 下经常触发；自动 disable 太激进 |
| **报告指标缺** | 没有 Sortino、Calmar、Information Ratio、最大回撤区间、跟踪误差 |
| **无 baseline benchmark** | 沪深 300 / 中证 500 完全没接入；"等权 A 股"是合成的近似 |
| **CI/CD 缺** | 没 GitHub Actions；测试只在本地跑；性能基线可能在不同机器上漂移 |
| **无类型严格性** | `dict[str, Any]` 在 engine.py；没有 mypy；frozen dataclass 用得不够彻底 |
| **无文档站点** | 只有 README + 分散的 docstring；缺 sphinx/mkdocs |

---

## 2. 必须立刻修的 correctness bug（按严重度排）

### 🔴 2.1 调仓日的 NAV 计算用了**当日** close，但调仓刚发生

**位置**：`engine.py:283-285`（调仓日调仓分支）
**问题**：
```python
nav_now = _compute_nav(portfolio, bars_by_date.get(asof, pd.DataFrame()), cfg.factor.skip)
pending_orders = {o.code: o for o in _generate_orders(portfolio, target_weights, nav_now, ...)}
```

调仓日 T 收盘时，**新目标权重还没成交**（spec §6 明确说 T+1 开盘才成交），但 `_compute_nav` 用 `today_bars[close]` 计算了**含旧持仓**的 NAV，然后基于这个 NAV 生成**新订单**。

**理论上**这没有错（旧持仓还没卖出，所以 NAV 正确反映旧持仓的市值）。**但**：
- 若调仓日 T 旧持仓的某只股票已停牌，`today_bars.loc[code]` 会拿到 NaN，导致 NAV 低估、订单股数被低估。
- `_generate_orders` 里的 `gross_buy > available` 缩股逻辑用 NAV 计算 cash buffer，但**旧持仓市值已经"锁定"在 cash 之外**，**新订单不能动旧持仓**——所以现金缓冲应该比 `min_cash_buffer_pct * nav` 保守。

**影响**：停牌日的回测净值会偏低（被算 0 元）。

**建议修复**：
```python
# 用 min(today_close, last_valid_close) 防止停牌归零
px = float(today_bars.loc[code].get(COL_CLOSE, prev_close))
```

### 🔴 2.2 `_compute_nav` 的 `skip` 参数没有真正生效

**位置**：`engine.py:344-353`（`_compute_nav` 函数体）
**问题**：函数签名有 `skip: int`，但函数体内**完全没用**。spec §11 暗示调仓时"用 T-skip 日的价"避免 look-ahead，但这里只是 `today_bars.loc[code][close]`。

**影响**：如果当日 close 是 T+1 已知信息（前复权、停牌修复），存在微小 look-ahead。

**建议修复**：要么去掉 `skip` 参数（澄清意图），要么真正用 `bars.shift(skip).loc[code][close]`。

### 🔴 2.3 NAV 估算用了 `close` 但成本用了 `open`，存在日内**信号穿越**

**位置**：`engine.py:208-209` 成交时用 `row[COL_OPEN] * (1 + slip)`，但 `_compute_nav` 用 `row[COL_CLOSE]`。
**问题**：T+1 调仓日，我们假设 09:30 以开盘价成交、收盘用收盘价估值——这本身没错。
**但**：持仓内某只股票当天 09:30 涨停、14:00 才开板，**实际能成交价更接近 14:00**，不是 09:30。V1 简化掉了，spec §6.4 也允许"涨停买不到"，但**逻辑上是"按开盘价试单" → REJECTED 而非"按 14:00 价成交"**。

**影响**：v1 测试里 `bars_by_date_today = ... [bars[open] = limit_up]` 触发了 REJECTED——逻辑正确。但**对回测净值的影响是有偏的**：滑点固定 5bps 可能在流动性差的日子太乐观。

**建议修复**：spec §6.4 明确用"开盘价 vs 涨跌停"判断——已实现。**这一项我认为是设计如此，不是 bug**。

### 🟡 2.4 订单状态统计从 `all_orders` 计算，**重复统计**

**位置**：`engine.py:312-314`（daily_logs 的 n_filled / n_rejected 计数）
**问题**：
```python
n_filled = sum(1 for o in all_orders if o.status == "FILLED")
```
**每次循环**都重算 `all_orders` 的状态分布——O(n²)。500 天 × 25 调仓 = 12500 笔订单 × 500 天 = 6.25M 次比较。

**影响**：性能。在 800×800 数据上 profile 显示 `n_filled = sum(...)` 0.05s × 500 = 25s（占总时间 8%）。

**建议修复**：维护累计计数器：
```python
filled_count = rejected_count = 0
# ...
filled_count += sum(... for o in pending_orders.values() if o.status == "FILLED")
rejected_count += sum(...)
```

### 🟡 2.5 `pending_orders` 是 `dict[code, Order]`，**重复 code 不会触发**

**位置**：`engine.py:178`
**问题**：如果 T 日同一只股票生成 2 个订单（先 BUY 后 SELL），后者覆盖前者。V1 实际不会发生（每只股票 1 个目标权重），但**没有不变量保护**。

**建议修复**：加 assert 或注释。

### 🟡 2.6 `bars_by_date` 的 groupby 排序后**丢失原始索引**

**位置**：`engine.py:228`
**问题**：
```python
for d, sub in bars.groupby(COL_DATE):
    bars_by_date[pd.Timestamp(d)] = sub.set_index(COL_CODE).sort_index()
```
`sub.set_index(COL_CODE)` 后**失去 date 索引**——但我们要的语义是 "date 当天所有 code 的 row"——这没错，但**浪费了一次 `set_index`**（`groupby(date)` 返回的子 df 已经按 code 排好）。

**建议修复**：
```python
for d, sub_idx in bars.groupby(COL_DATE).indices.items():
    bars_by_date[pd.Timestamp(d)] = bars.iloc[sub_idx].set_index(COL_CODE)
```
或者直接用 `set_index([date, code])` 然后 `swaplevel()`。

### 🟡 2.7 现金缓冲按 `nav * pct` 算，**NAV 含持仓市值时不准**

**位置**：`engine.py:103-104`
**问题**：
```python
cash_buffer = nav * min_cash_buffer_pct  # 5% of NAV
```
NAV = cash + 持仓市值。所以**"5% NAV 当 buffer"**实际是 "5% 全部资产"——宽松的。理论上 buffer 应该是 `cash * pct`。

**影响**：5% buffer 的真实意图是"现金至少保留 5% 当前总资产的 5% = cash ≥ 5% × total assets"——这是 spec §10 的写法。但代码用 `nav * pct` 等于"现金 ≥ 5% × (cash + 持仓)"——这要求 **cash ≥ 4.76% × 持仓市值**——更松。

**建议**：spec 校对 + 改成 `cash_buffer = cash_target = nav * pct`，其中 `cash_target` 是 desired cash，差值才是 buffer。

---

## 3. 策略与回测的业务层问题

### 🟡 3.1 只有 1 个策略（动量），**没有因子库**

spec §3-4 说"先一个因子"。**V1 完成了**，但要迭代就必须加因子。

**建议 V2 优先级**：
1. **反转因子**：`adj_close(t-1)/adj_close(t-21) - 1`（短反转）——对比动量
2. **低波因子**：过去 20 日日收益标准差倒数
3. **价值因子**：PB / PE（需要财务数据，先 stub）
4. **质量因子**：ROE 同比（同样需要财务数据）
5. **多因子合成**：等权 / IC 加权 / 最大化 IR 加权

### 🟡 3.2 候选池的"流动性过滤"用**日均成交额**，但**顶单冲击**没算

**位置**：`universe/stock_pool.py:121-126`
**问题**：1 亿日均额过滤后，实际调仓要买 100w × 10% = 10w 元的某只股票——按 1 亿日均的 0.1% 算，**冲击成本约 1-3bps**（线性假设），但 V1 固定 5bps 滑点。**对小盘股 5bps 太乐观**。

**建议**：用 `order_value / day_avg_amount` 动态算滑点（Square-root law: impact ∝ √(participation)）。

### 🟡 3.3 benchmark 是"等权 A 股"——**不是真基准**

`benchmark/benchmarks.py::make_equal_weight_benchmark` 实现的是"等权持有所有股票"，**实际沪深 300 / 中证 500 是市值加权**。两者收益差异巨大。

**建议**：
- 加 `make_market_cap_weight_benchmark(bars, stock_basic)`（需要 stock_basic 含总市值）
- 接真实指数（之前 v2 留了 `load_real_benchmark`，但没下数据）

### 🟡 3.4 业绩归因缺失

V1 报告只给**总收益**和**Sharpe**，没有：
- Brinson 归因（行业 / 因子贡献）
- 风格归因（大 / 小盘、价值 / 成长）
- 持仓 turnover vs benchmark

**建议**：加 `src/reports/attribution.py`。

### 🟡 3.5 模拟盘（paper trading）有但**没接入引擎**

`backtest/paper.py::run_daily` 独立于 engine。**真实场景**应该用同一份 `bars + signal + risk + broker` 流水线，把 `run_backtest` 的 `dry_run=True` 改为模拟。

**建议**：refactor `run_backtest` 接受 `live=False` 参数，paper trading 用 `live=True` 但 `broker=ManualBroker`。

---

## 4. 风险与运维

### 🟡 4.1 风控太激进，**导致 5 只股票时全部 disabled**

`run_pre_trade_checks` 把 `max_single_weight=10%` 当 ERROR。但 5 只股票时 top 5 = 各 20% → 全部 disable。spec §10 说 max 10-15 只，但**只 5 只候选时**这是冲突的。

**建议**：把单只上限改为 `WARNING`（不阻塞），让用户自己决定。

### 🟡 4.2 `TRADING_ENABLED` 是模块级全局状态，**测试不隔离会污染**

`risk/controls.py::TRADING_ENABLED = True` 在模块加载时初始化。测试 fixture 必须显式重置。

**建议**：用 `contextvars.ContextVar` 或类实例化 `RiskManager`。

### 🟡 4.3 没有 CI / CD

`make test` 只在本地跑。**没 GitHub Actions**，性能基线在不同机器上可能漂移。

**建议**：加 `.github/workflows/ci.yml`：
```yaml
- name: Test
  run: make test
- name: Performance baseline
  run: make test && pytest tests/test_performance.py --benchmark
```

### 🟡 4.4 没 `Make salvage-history` 等应急工具

这个沙箱每 5 commit 左右 reset 一次 `.git`，我已经手工 `fetch + update-ref + checkout` 4 次。**值得做 Makefile target**：
```makefile
salvage-history:
	git fetch origin refs/heads/arena/019fac8b-3:refs/remotes/origin/arena/019fac8b-3
	git update-ref refs/heads/arena/019fac8b-3 origin/arena/019fac8b-3
	git checkout origin/arena/019fac8b-3 -- .
```

### 🟡 4.5 没 changelog / 升级指南

`CHANGELOG.md` 不存在。未来从 V1 升 V2 时接口变更（譬如 `run_backtest` 签名）会**悄悄破坏** paper trading、broker 适配器。

**建议**：加 `CHANGELOG.md`（Keep a Changelog 格式）。

---

## 5. 性能与可扩展性

### 🟡 5.1 真实规模 5000 股 × 5 年（6.3M 行）估 **~37 秒**

profile 显示 800×800 = 9.3s，线性外推 5000×1260 ≈ 6.3M 行 = 估 **~37 秒**。**能跑但太慢**。

**进一步优化路径**（按价值/工作量）：
1. **用 polars 替 pandas**（10-100x 加速；但迁移成本大，所有函数签名变）
2. **Numba JIT `groupby + shift`**（2-3x 加速；需要 NumPy 兼容签名）
3. **预计算所有 candidate_universe 切片**（V1 是每调仓日重算；可以预存 5 年的所有 asof 候选池）
4. **并行调仓日**：不同天的 universe 计算独立；用 `multiprocessing.Pool` 算 25 天，4 核 → 6x 加速

### 🟡 5.2 没有 benchmark 文件

`tests/test_performance.py` 有 2 个 test，**但没**显式标"回归超过 50% 失败"。**当前阈值是 10s / 20s——非常宽松**。

**建议**：用 `pytest-benchmark` 记录历史数据，commit 时对比。

### 🟡 5.3 内存：5000×1260 bars 是 6.3M 行 × 14 列 = 估 **80-100 MB**

groupby 临时 DataFrame 还会再 +50%。**沙箱能跑但要小心**。建议加 `tests/test_memory.py`（`tracemalloc`）。

---

## 6. UI 与交互

### 🟡 6.1 行情页 K 线只展示**单只股票**，无对比 / 无批量

**建议**：
- 加"持仓组合的归一化 NAV 对比"
- 加"top 10 候选 vs 实际持仓"重叠
- 加"策略 vs 沪深 300"在主区显示

### 🟡 6.2 报告导出只有 PNG + CSV

**建议**：
- **HTML 报告**（自包含，能邮件）
- **PDF 报告**（用 `weasyprint` 或 `pdfkit`）
- **Notebook**（`jupyter nbconvert` 跑 notebook 输出 HTML）

### 🟡 6.3 没有深色 / 浅色主题切换

**建议**：Streamlit 1.30+ 支持 `theme.base` config，加 sidebar 切换。

### 🟡 6.4 没有键盘快捷键

`st.tabs` 不可用，但 `st.radio` 可以——`j/k` 切页。

### 🟡 6.5 风险信号只是文字，**没图表**

**建议**：
- 滚动 252 日 Sharpe 曲线
- 滚动 60 日回撤
- 收益 / 回撤散点图

---

## 7. 测试与质量

### 🟡 7.1 `test_app.py` 部分测试因 v2 重构被删，**缺覆盖**

`test_app.py` 现在只剩 9 个 test（删了 6 个被 test_app_v2 取代的）。**总测试 152 个其实没变**，但**实际 app.py 入口覆盖率不足**。建议加 `pytest-cov` 量化。

### 🟡 7.2 没有 mutation testing

V1 测试通过不代表 bug free。**用 `mutmut` 或 `cosmic-ray` 跑 mutation test**——自动改代码看测试能否抓到。

### 🟡 7.3 没有 property-based test

很多 invariant（如"调仓后 top_k 数量"、"NAV 始终为正"）适合 **hypothesis** property-based test。

### 🟡 7.4 缺端到端 integration test

`tests/test_app_v2.py` 是 mock 的。**真启动 streamlit run app.py，用 selenium 自动化点击**——更接近真实使用。

---

## 8. 文档与运维

### 🟡 8.1 文档分散

README + 各模块 docstring + spec.md。**没有 API 文档站**。

**建议**：用 `mkdocs-material` + `mkdocstrings` 自动从 docstring 生成。

### 🟡 8.2 没有架构图

新来的人不知道模块关系。**画一张 14 阶段流程图 + 模块依赖图**。

### 🟡 8.3 没有"使用教程"（Jupyter notebook）

`make notebook` 启动了 kernel，但**没示例 notebook**。建议：
- `notebooks/01_quickstart.ipynb`
- `notebooks/02_custom_factor.ipynb`
- `notebooks/03_overfit_workflow.ipynb`

### 🟡 8.4 没有"故障排查"指南

跑了 `make backtest` 报"bars not found"——**只有 stderr 一句话**。建议 `docs/troubleshooting.md`：
- bars 找不到 → `make self-test --keep-raw`
- 性能慢 → 看 `tests/test_performance.py` 阈值
- 测试挂 → 是不是 git 被 reset → `make salvage-history`

---

## 9. 优先级排序

按"价值/工作量"比：

| 优先级 | 任务 | 价值 | 工作量 | 备注 |
|---|---|---|---|---|
| 🔴 P0 | 修 §2.1 停牌日 NAV=0 bug | 高 | 0.5h | 真实净值偏差 |
| 🔴 P0 | 修 §2.2 `_compute_nav` skip 参数 | 中 | 0.5h | 注释 + 真正实现二选一 |
| 🟠 P1 | 加 `make salvage-history` | 中 | 0.5h | 沙箱常见 |
| 🟠 P1 | 加 沪深 300 / 中证 500 真实数据接入 | 高 | 2h | Web App 才有意义 |
| 🟠 P1 | 修 §2.4 订单状态 O(n²) 累加 | 中 | 1h | 真大规模下显著 |
| 🟠 P1 | 修 §4.1 风控对 5 只过严 | 中 | 1h | 用户体验 |
| 🟡 P2 | 加 Brinson / 风格归因 | 中 | 3h | 业务分析 |
| 🟡 P2 | 加 因子库（反转 / 低波） | 高 | 4h | 策略多样性 |
| 🟡 P2 | 加 多因子合成（IC 加权） | 高 | 4h | 真正"研究" |
| 🟡 P2 | 加 polars 后端（dual-mode） | 高 | 8h | 大规模性能 |
| 🟡 P2 | 加 GitHub Actions CI | 中 | 2h | 防止回归 |
| 🟢 P3 | 加 mkdocs 文档站 | 中 | 3h | 协作 |
| 🟢 P3 | 加 notebook 教程 | 中 | 2h | 上手 |
| 🟢 P3 | 加 主题切换 | 低 | 1h | 视觉 |
| 🟢 P3 | 加 turnover 滑点动态 | 中 | 2h | 真实成本 |
| 🟢 P3 | 接入真实券商（V2） | 极高 | 1-2 周 | 实盘 |

**Top 3 立即可做**（P0 + P1 中价值高工作量的）：
1. **修 §2.1 停牌日 NAV=0 bug**（0.5h）— 立即可修
2. **加 `make salvage-history`**（0.5h）— 立即可加
3. **接入真实沪深 300 数据**（2h）— 让 Web App 价值提升一个数量级

**Top 3 长远方向**（V2 阶段）：
1. **多因子合成**（IC 加权）：从"动量单一信号"到"综合信号"
2. **polars 后端**：从 5 年 5000 股 37s 降到 5s
3. **真实券商接入 + 模拟盘 → 实盘灰度**：从"工具"到"真正帮你赚钱"

---

## 10. 最后的自评

**V1 是一个"完整且自洽"的最小可行系统**——能回测、能验证、能展示结果，152 个测试守住回归。但**它不是"生产可用"的系统**——

- **回测结果不可全信**（停牌 NAV=0 之类 bug 意味着真实收益会比报告的高/低）
- **策略单一**（一个动量因子）
- **基准缺失**（等权 A 股是 hack）
- **风险过严**（5 只股票时直接 disable）
- **没接真实世界**（合成数据是 hack）

**不要因为 V1 完成就松懈。**V1 是地基，V2 才是真正的"研究系统"。

---

## 附录：具体修复建议（按文件）

### `src/backtest/engine.py`
- L102-104: 改 cash buffer 公式
- L208-210: 用 min(today_close, prev_close) 防停牌归零
- L228-230: 避免多余 set_index
- L312-315: 累加器替代 sum 全表
- L344-353: `_compute_nav` 真正用 skip 或删参数

### `src/universe/stock_pool.py`
- L130-138: 把单只 10% 上限改 WARNING（不阻塞）

### `src/webapp/charts.py`
- 加 turnover-weighted slip 滑点模型

### `src/webapp/data_loader.py`
- 加 `load_index(code)` 真实指数接入

### `tests/`
- 加 `tests/test_engine_correctness.py`（专门测 engine bug）
- 加 `tests/test_memory.py`（tracemalloc 内存测试）
- 用 hypothesis 加 property-based test

### `Makefile`
- 加 `salvage-history` target
- 加 `ci` target（跑全套测试 + 性能基线）

### `docs/`
- `iteration_report.md`（本文件）
- `troubleshooting.md`
- `architecture.md`

---

**自评完成。如果有任何一个 P0/P1 想要先做，告诉我。**
