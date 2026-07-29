"""V1 阶段零业务入口：仅证明包结构可被 import 与运行。

用法（在 a_share_quant/ 目录下）：

    .venv/bin/python -m src

输出：
    项目版本号
    关键包版本号
    已加载的 strategy.yaml 配置摘要
    字段契约样例

后续阶段会替换为：
    python -m src download        # 阶段 4
    python -m src validate         # 阶段 5
    python -m src backtest         # 阶段 9
"""

from __future__ import annotations

import sys

import pandas as pd

from src import __version__
from src.config import CONFIG_PATH, load_config
from src.data import BARS, COL_CODE, COL_DATE, make_empty_bars


def main() -> int:
    print(f"a_share_quant V{__version__}")
    print(f"config: {CONFIG_PATH}")

    cfg = load_config()
    print(f"universe.start_date   = {cfg.universe.start_date}")
    print(f"universe.min_amount   = {cfg.universe.min_avg_amount_20d:,.0f}")
    print(f"factor.name           = {cfg.factor.name} (lookback={cfg.factor.lookback}, skip={cfg.factor.skip})")
    print(f"signal.rebalance      = every {cfg.signal.rebalance_every} trading days")
    print(f"signal.top_k          = {cfg.signal.top_k}")
    print(f"execution.price_basis = {cfg.execution.price_basis}")
    print(f"risk.TRADING_ENABLED  = {cfg.risk.trading_enabled}")

    # 字段契约冒烟
    empty = make_empty_bars()
    assert COL_CODE in empty.columns and COL_DATE in empty.columns
    BARS.validate(empty)
    print(f"schema: {len(BARS.required)} required columns OK")

    # 确认 pandas / numpy 工作正常
    s = pd.Series([1, 2, 3]).sum()
    assert int(s) == 6
    print("pandas: ok")

    print("V1 phase-3 dry run: all imports and config loading succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
