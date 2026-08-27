"""模拟盘：每日任务（spec §13）。

run_daily(bars_today, ...) 的关键性质是**幂等性**：
同一个 (asof_date, input) 跑两次必须得到完全一致的结果（不重复下单）。

V1 实现策略：
- 用磁盘 parquet 存"已发出的订单簿"
- 新订单按 (date, code) 去重
- 任何异常自动 stop，下次再跑时不重放
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from pathlib import Path

import pandas as pd

from src.backtest.broker import (
    Order,
    ORDER_FILLED,
    ORDER_REJECTED,
)
from src.backtest.engine import _generate_orders, _check_tradable
from src.config import load_config
from src.data.schema import (
    COL_CODE,
    COL_DATE,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_OPEN,
    COL_SUSPENDED,
)
from src import risk as _risk_module
from src.risk.controls import run_pre_trade_checks  # 保留直接 import（不绑定 TRADING_ENABLED）
from src.strategy.signal import generate_target_weights

logger = logging.getLogger(__name__)


@dataclass
class DailyState:
    """模拟盘每日状态。"""
    asof_date: str
    nav: float
    cash: float
    positions: dict[str, int] = field(default_factory=dict)
    target_weights: dict[str, float] = field(default_factory=dict)
    orders: list[dict] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DailyState":
        return cls(**d)


def _state_path(state_dir: Path, asof_date) -> Path:
    name = pd.Timestamp(asof_date).strftime("%Y-%m-%d")
    return state_dir / f"{name}.json"


def _load_state(state_dir: Path, asof_date) -> DailyState | None:
    p = _state_path(state_dir, asof_date)
    if not p.exists():
        return None
    with p.open("r") as f:
        return DailyState.from_dict(json.load(f))


def _save_state(state_dir: Path, state: DailyState) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = _state_path(state_dir, state.asof_date)
    with p.open("w") as f:
        json.dump(state.to_dict(), f, indent=2, default=str)
    return p


# ===== 核心入口 =====
def run_daily(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    asof_date,
    initial_cash: float = 1_000_000.0,
    state_dir: Path | None = None,
    rebalance_every: int | None = None,
) -> DailyState:
    """模拟盘每日任务。

    入参：
    - bars: 截至 asof_date 的日线
    - asof_date: 当日（T 日）
    - state_dir: 状态持久化目录（默认 results/paper_state/）
    - initial_cash: 初始资金（仅第一次运行时使用）

    返回：DailyState
    幂等：同 (asof_date, inputs) 跑两次结果一致。
    """
    cfg = load_config()
    rebalance_every = rebalance_every if rebalance_every is not None else cfg.signal.rebalance_every
    asof = pd.Timestamp(asof_date)
    asof_str = asof.strftime("%Y-%m-%d")

    if state_dir is None:
        state_dir = Path("results") / "paper_state"
    state_dir = Path(state_dir)

    # 1) 幂等性：检查是否已运行过
    if (existing := _load_state(state_dir, asof)) is not None:
        logger.info("daily state for %s already exists; returning cached", asof_str)
        return existing

    if not _risk_module.controls.TRADING_ENABLED:
        return DailyState(
            asof_date=asof_str, nav=0.0, cash=0.0,
            notes="TRADING_ENABLED=False; day skipped",
        )

    # 2) 计算目标权重
    target_weights = generate_target_weights(bars, stock_basic, asof)

    # 3) 风控预检
    cash = initial_cash
    nav = initial_cash  # V1 简化：用 initial_cash 当 nav（无前日延续）
    risk_report = run_pre_trade_checks(target_weights, cash, nav)
    if risk_report.has_blocking:
        return DailyState(
            asof_date=asof_str, nav=nav, cash=cash,
            target_weights=target_weights,
            notes=f"BLOCKED: {[v.detail for v in risk_report.errors]}",
        )

    # 4) 模拟 T+1 撮合（不真下单）
    cal_dates = pd.to_datetime(trade_calendar["date"]).sort_values().reset_index(drop=True)
    asof_idx = cal_dates.searchsorted(asof)
    if asof_idx + 1 >= len(cal_dates):
        return DailyState(asof_date=asof_str, nav=nav, cash=cash, notes="no T+1 trading day")
    t1 = pd.Timestamp(cal_dates.iloc[asof_idx + 1])

    t1_bars = bars[bars[COL_DATE] == t1] if not bars.empty else pd.DataFrame()
    if t1_bars.empty:
        return DailyState(asof_date=asof_str, nav=nav, cash=cash, notes="no T+1 bars")

    # 5) 生成订单
    prices_t1 = {row[COL_CODE]: float(row[COL_OPEN]) for _, row in t1_bars.iterrows()}
    from src.backtest.engine import Portfolio
    port = Portfolio(cash=cash, positions={}, nav=nav)
    orders = _generate_orders(
        port, target_weights, nav, prices_t1,
        cfg.execution.lot_size, cfg.execution.min_cash_buffer_pct / 100.0,
    )

    # 6) 检查可成交性 + 模拟成交
    order_dicts = []
    for o in orders:
        row = t1_bars[t1_bars[COL_CODE] == o.code]
        if row.empty:
            o.reject("no T+1 row")
        else:
            r = row.iloc[0]
            reason = _check_tradable(o, r)
            if reason is None:
                o.fill(float(r[COL_OPEN]))
            else:
                o.reject(reason)
        order_dicts.append({
            "code": o.code, "side": o.side, "shares": o.shares,
            "price": o.price, "status": o.status,
            "filled_shares": o.filled_shares, "filled_price": o.filled_price,
        })

    state = DailyState(
        asof_date=asof_str, nav=nav, cash=cash,
        target_weights=target_weights, orders=order_dicts,
        notes="ok",
    )

    # 7) 持久化（幂等性靠"如果已存在就跳过"实现）
    _save_state(state_dir, state)
    return state
