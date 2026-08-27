# 分区行情研究架构

## 目的

全市场日线数据按股票拆分后，研究流程不再需要把所有行情合并进一个 Pandas
DataFrame。这个路径面向内存受限的本地研究，仍然是 `RESEARCH_ONLY`，不产生下单权。

## 执行边界

```text
raw/bars/<code>.parquet
        │  单文件清洗、原子替换、立即复读
        ▼
processed/bars_by_code/<code>.parquet
        │  DuckDB 仅以当日及历史窗口计算候选
        ▼
每个调仓日的 top-K 目标权重
        │  仅加载入选代码的 open/close/涨跌停/停牌列
        ▼
既有撮合引擎 + 运行清单
```

`partitioned.py` 中信号 SQL 的窗口按 `code, date` 升序排列，`lag` 只访问前序行；
撮合引擎的估值也按 `asof_date` 截断价格序列。因此该路径不会为了信号或估值读取
调仓日后的价格。

## 数据质量契约

1. 原始行情是修复权威源；处理分区坏掉时，先运行 `validate --partitioned`。
2. 只从报告中列出的 `partition_read/ERROR` 分区修复：

   ```bash
   python -m src repair-partitions --report data/data_quality_report_partitioned.csv
   ```

3. 重建写入同目录临时文件，立即复读，再以原子替换发布。源分区不可读或缺失时修复
   必须失败，不能把不完整修复标成成功。
4. 再次验证为零个 `ERROR` 后，才创建新的快照和运行回测：

   ```bash
   python -m src validate --asof YYYY-MM-DD --partitioned --report data/data_quality_report_partitioned.csv
   python -m src snapshot-data
   python -m src backtest --partitioned --start-date YYYY-MM-DD --end-date YYYY-MM-DD
   ```

质量报告、数据快照、结果均是本地研究产物，不纳入 Git。

## 当前限制

- DuckDB 每次运行都会扫描分区行情；完整过拟合套件会重复扫描，适合离线批跑而非 Web
  请求路径。
- 快照校验的是数据文件内容，不包含“质量报告与该快照是否同一版本”的绑定。下一阶段应
  将零错误验证结果和数据摘要一同写入快照，回测只接受已验证快照。
- 原始源若已损坏，定点修复需要重新下载对应代码；不应使用旧的处理分区或旧快照继续回测。
