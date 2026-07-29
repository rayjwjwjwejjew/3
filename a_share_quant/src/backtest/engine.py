"""回测引擎（spec §8、§9）。

每日顺序：
  1. 取 ≤ T 日可获得的数据
  2. 更新 candidate_universe
  3. 判断是否调仓日
  4. 若是：生成目标权重
  5. 按当前持仓 vs 目标权重生成订单（整手取整 + 现金缓冲）
  6. 检查可成交性（涨跌停、停牌）
  7. 模拟成交于 T+1 开盘价
  8. 扣除成本
  9. 更新现金、持仓、净值
 10. 保存每日日志

最重要的原则：T 日信号 → T+1 开盘成交（不可假设自己已按 T 收盘价成交）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import load_config
from src.data.schema import (
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_OPEN,
    COL_SUSPENDED,
    COL_VOL,
    make_empty_bars,
)
from src.strategy.signal import generate_target_weights
from src.universe.stock_pool import build_tradable_universe
from src.backtest.broker import Order
from src.backtest.costs import calc_cost

logger = logging.getLogger(__name__)


@dataclass
class Portfolio:
    """当前组合状态。"""
    cash: float
    positions: dict[str, int] = field(default_factory=dict)   # code -> shares
    avg_cost: dict[str, float] = field(default_factory=dict)  # code -> 持仓均价（用于报告）
    nav: float = 0.0
    date: pd.Timestamp | None = None


@dataclass
class DailyLog:
    date: pd.Timestamp
    nav: float
    cash: float
    gross_position_value: float
    n_holdings: int
    n_orders: int
    n_filled: int
    n_rejected: int
    n_cancelled: int
    turnover: float  # 当日成交金额 / nav
    costs: float     # 当日总成本
    is_rebalance: bool


def _lot_round(shares: int, lot_size: int) -> int:
    """整手向下取整。买入必须为 lot_size 倍数；卖出允许 ≤ 持仓的零碎（V1 简化为同样取整）。"""
    if shares <= 0:
        return 0
    return (shares // lot_size) * lot_size


def _generate_orders(
    portfolio: Portfolio,
    target_weights: dict[str, float],
    nav: float,
    prices_t1: dict[str, float],  # T+1 开盘价
    lot_size: int,
    min_cash_buffer_pct: float,
) -> list[Order]:
    """根据当前持仓与目标权重，生成订单。

    算法：
    1. 计算每只股票目标股数 = target_weight × nav / T+1 开盘价，整手
    2. 实际下单股数 = target - current（BUY/SELL）
    3. 现金缓冲 = nav × min_cash_buffer_pct
    4. 若总买入金额 > cash - buffer，按比例缩股
    """
    if not target_weights:
        target_weights = {}
    if nav <= 0:
        return []

    target_shares: dict[str, int] = {}
    for code, w in target_weights.items():
        px = prices_t1.get(code)
        if px is None or px <= 0:
            continue
        raw = (w * nav) / px
        target_shares[code] = _lot_round(int(raw), lot_size)

    # 缩股逻辑：保证 cash 充足
    gross_buy = 0.0
    for code, ts in target_shares.items():
        cur = portfolio.positions.get(code, 0)
        delta = ts - cur
        if delta > 0:
            gross_buy += delta * prices_t1[code]
    cash_buffer = nav * min_cash_buffer_pct
    available = portfolio.cash - cash_buffer
    if gross_buy > available and gross_buy > 0:
        scale = available / gross_buy
        for code in list(target_shares):
            cur = portfolio.positions.get(code, 0)
            delta = target_shares[code] - cur
            if delta > 0:
                target_shares[code] = cur + int(delta * scale // lot_size * lot_size)

    # 产生订单
    all_codes = set(target_shares) | set(portfolio.positions)
    orders: list[Order] = []
    for code in all_codes:
        target = target_shares.get(code, 0)
        current = portfolio.positions.get(code, 0)
        delta = target - current
        if delta == 0:
            continue
        px = prices_t1.get(code)
        if px is None or px <= 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        order = Order(code=code, side=side, shares=abs(delta), price=px)
        order.submit()
        orders.append(order)
    return orders


def _check_tradable(
    order: Order,
    bars_t1_row: pd.Series | None,
) -> str | None:
    """检查 T+1 日是否可成交。返回 None=可成交；否则返回拒绝原因。

    spec §6.4:
    - 买入：T+1 未停牌 且 T+1 开盘价 < 涨停价
    - 卖出：T+1 未停牌 且 T+1 开盘价 > 跌停价
    """
    if bars_t1_row is None:
        return "no T+1 bar data"
    if bool(bars_t1_row.get(COL_SUSPENDED, True)):
        return "T+1 suspended"
    open_px = float(bars_t1_row.get(COL_OPEN, 0.0))
    if order.side == "BUY" and open_px >= float(bars_t1_row.get(COL_LIMIT_UP, open_px + 1)):
        return "T+1 at limit up"
    if order.side == "SELL" and open_px <= float(bars_t1_row.get(COL_LIMIT_DOWN, open_px - 1)):
        return "T+1 at limit down"
    return None


def run_backtest(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    initial_cash: float = 1_000_000.0,
) -> dict[str, Any]:
    """跑完整回测，返回 {nav_series, daily_logs, orders, portfolio}。

    V1 简化：调仓日固定每 20 个交易日（第 0、20、40...）。
    """
    cfg = load_config()
    rebalance_every = cfg.signal.rebalance_every

    # 交易日序列
    trading_days = pd.to_datetime(trade_calendar.loc[
        trade_calendar["is_trading_day"] == True, COL_DATE  # noqa: E712
    ]).sort_values().reset_index(drop=True)
    if trading_days.empty:
        raise ValueError("trade_calendar has no trading days")

    # 索引：code -> date -> row
    bars_idx = bars.set_index([COL_CODE, COL_DATE]).sort_index() if not bars.empty else pd.DataFrame()

    portfolio = Portfolio(cash=initial_cash, nav=initial_cash, date=trading_days.iloc[0])
    nav_records: list[dict] = []
    daily_logs: list[DailyLog] = []
    all_orders: list[Order] = []
    pending_orders: dict[str, Order] = {}  # code -> order，跨日执行
    target_weights: dict[str, float] = {}

    for i, asof in enumerate(trading_days):
        asof = pd.Timestamp(asof)
        portfolio.date = asof

        # ---- 步骤 A：执行昨日生成的 pending orders（按 T+1 开盘价）----
        if pending_orders:
            t1_bars_today = bars_idx.xs(asof, level=COL_DATE) if not bars_idx.empty else pd.DataFrame()
            for code, order in list(pending_orders.items()):
                row = t1_bars_today.loc[code] if code in t1_bars_today.index else None
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                reason = _check_tradable(order, row)
                if reason is None and row is not None:
                    px = float(row[COL_OPEN]) * (1 + cfg.execution.slippage_bps / 10000)
                    # 计算成本
                    cost = calc_cost(
                        code, order.side, order.shares * px,
                        cfg.cost.commission_rate, cfg.cost.commission_min,
                        cfg.cost.stamp_tax_rate, cfg.cost.transfer_fee_rate,
                    )
                    order.fill(px, cost_total=cost.total,
                               cost_detail={"commission": cost.commission,
                                            "stamp_tax": cost.stamp_tax,
                                            "transfer_fee": cost.transfer_fee})
                    # 现金 + 持仓（成本从现金再扣一次）
                    if order.side == "BUY":
                        portfolio.cash += order.cash_flow - cost.total
                        portfolio.positions[code] = portfolio.positions.get(code, 0) + order.filled_shares
                    elif order.side == "SELL":
                        portfolio.cash += order.cash_flow - cost.total
                        new = portfolio.positions.get(code, 0) - order.filled_shares
                        if new <= 0:
                            portfolio.positions.pop(code, None)
                            portfolio.avg_cost.pop(code, None)
                        else:
                            portfolio.positions[code] = new
                else:
                    order.reject(reason or "unknown")
            all_orders.extend(pending_orders.values())
            pending_orders = {}

        # ---- 步骤 B：调仓判断 ----
        is_rebalance = (i % rebalance_every == 0)

        if is_rebalance:
            # 用截至 T 日（不含 T+1，因为信号是 T 收盘后的）的数据
            # 这里 asof 是 T 日；target 用 T 日信号；pending orders 会在 T+1 成交
            target_weights = generate_target_weights(bars, stock_basic, asof)
            # T+1 开盘价 = 下一交易日的 open
            if i + 1 < len(trading_days):
                t1_date = pd.Timestamp(trading_days.iloc[i + 1])
                t1_bars = bars_idx.xs(t1_date, level=COL_DATE) if not bars_idx.empty else pd.DataFrame()
                prices_t1: dict[str, float] = {}
                for code in set(list(target_weights) + list(portfolio.positions)):
                    if code in t1_bars.index:
                        row = t1_bars.loc[code]
                        if isinstance(row, pd.DataFrame):
                            row = row.iloc[0]
                        prices_t1[code] = float(row[COL_OPEN])
                # 用当前 nav（用 close 重估）
                nav_now = _compute_nav(portfolio, bars_idx.xs(asof, level=COL_DATE) if not bars_idx.empty else pd.DataFrame(), cfg.factor.skip)
                pending_orders = {
                    o.code: o for o in _generate_orders(
                        portfolio, target_weights, nav_now, prices_t1,
                        cfg.execution.lot_size, cfg.execution.min_cash_buffer_pct / 100.0,
                    )
                }

        # ---- 步骤 C：每日 NAV 估值（用当日收盘价）----
        today_bars = bars_idx.xs(asof, level=COL_DATE) if not bars_idx.empty else pd.DataFrame()
        nav = _compute_nav(portfolio, today_bars, cfg.factor.skip)
        portfolio.nav = nav

        gross_pos = sum(
            portfolio.positions.get(c, 0) * float(today_bars.loc[c][COL_CLOSE])
            for c in portfolio.positions if c in today_bars.index
        )
        nav_records.append({"date": asof, "nav": nav, "cash": portfolio.cash, "position_value": gross_pos})

        # 日志
        n_orders = len(all_orders) if not is_rebalance else 0
        n_filled = sum(1 for o in all_orders if o.status == "FILLED")
        n_rejected = sum(1 for o in all_orders if o.status == "REJECTED")
        n_cancelled = sum(1 for o in all_orders if o.status == "CANCELLED")
        daily_logs.append(DailyLog(
            date=asof, nav=nav, cash=portfolio.cash,
            gross_position_value=gross_pos, n_holdings=len(portfolio.positions),
            n_orders=n_orders, n_filled=n_filled, n_rejected=n_rejected,
            n_cancelled=n_cancelled, turnover=0.0, costs=0.0, is_rebalance=is_rebalance,
        ))

    nav_df = pd.DataFrame(nav_records).set_index("date")
    return {
        "nav": nav_df,
        "daily_logs": daily_logs,
        "orders": all_orders,
        "final_portfolio": portfolio,
        "final_target_weights": target_weights,
    }


def _compute_nav(portfolio: Portfolio, today_bars: pd.DataFrame, skip: int) -> float:
    """用 T 日收盘价（跳过最近 skip 日用更早一日的价）计算 NAV。"""
    cash = portfolio.cash
    pos_value = 0.0
    for code, shares in portfolio.positions.items():
        if today_bars.empty or code not in today_bars.index:
            continue
        row = today_bars.loc[code]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        px = float(row[COL_CLOSE])
        pos_value += shares * px
    return cash + pos_value
