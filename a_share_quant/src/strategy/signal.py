"""策略信号（spec §4, §5）。

signal.py 只输出**目标权重** dict[code, weight]，不直接下单。
broker.py（第 8 阶段）会把权重转成订单。
"""

from __future__ import annotations

import pandas as pd

from src.config import load_config
from src.factors.momentum import compute_momentum, select_top_k, weights_from_top
from src.universe.stock_pool import build_candidate_universe


def generate_target_weights(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    asof_date,
    lookback: int | None = None,
    skip: int | None = None,
    top_k: int | None = None,
) -> dict[str, float]:
    """T 日生成目标权重。

    步骤（spec §4.2）：
    1. 用 candidate_universe 限制参与排名的 code
    2. 在 T 日截面按 momentum 降序
    3. 取前 top_k，等权
    4. 不足 top_k → 按实际数量等权
    5. 不足 min_holdings → 空仓

    lookback / skip / top_k 可选覆盖 yaml（V1 阶段用于过拟合测试）。
    """
    cfg = load_config()
    asof = pd.Timestamp(asof_date)
    lookback = lookback if lookback is not None else cfg.factor.lookback
    skip = skip if skip is not None else cfg.factor.skip
    top_k = top_k if top_k is not None else cfg.signal.top_k

    # 1) 候选池
    cand = build_candidate_universe(bars, stock_basic, asof)
    if cand.empty:
        return {}
    cand_codes = set(cand["code"].astype(str))

    # 2) 计算截面动量
    mom = compute_momentum(bars, lookback=lookback, skip=skip)
    top = select_top_k(mom, asof, top_k=top_k)
    # 限制到候选池
    if not top.empty:
        top = top[top["code"].astype(str).isin(cand_codes)]

    # 3) 不足则空仓
    if len(top) < cfg.signal.min_holdings:
        return {}
    # 4) 等权
    return weights_from_top(top, top_k=top_k)
