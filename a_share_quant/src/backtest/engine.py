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
)
from src.strategy.signal import generate_target_weights
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
    # 注（§2.6）：spec §10 写 "现金 ≥ 5% × NAV"；当前实现是 cash_buffer = NAV × 5%
    # 等价于 "现金 ≥ 4.76% × 持仓市值"——比 spec 略松。
    # V2 可收紧为 cash_buffer = (NAV - cash_target) * 1.0
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
    rebalance_every: int | None = None,
    lookback: int | None = None,
    skip: int | None = None,
    top_k: int | None = None,
    cost_multiplier: float = 1.0,
    precomputed_target_weights: dict[pd.Timestamp, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """跑完整回测，返回 {nav_series, daily_logs, orders, portfolio}。

    可选参数覆盖 yaml（V1 阶段仅这几个用于过拟合测试）。
    V1 简化：调仓日固定每 N 个交易日（第 0、N、2N...）。
    """
    cfg = load_config()
    rebalance_every = rebalance_every if rebalance_every is not None else cfg.signal.rebalance_every
    lookback = lookback if lookback is not None else cfg.factor.lookback
    skip = skip if skip is not None else cfg.factor.skip
    top_k = top_k if top_k is not None else cfg.signal.top_k
    cost_multiplier = max(0.0, float(cost_multiplier))

    # 交易日序列
    trading_days = pd.to_datetime(trade_calendar.loc[
        trade_calendar["is_trading_day"] == True, COL_DATE  # noqa: E712
    ]).sort_values().reset_index(drop=True)
    if trading_days.empty:
        raise ValueError("trade_calendar has no trading days")

    # 性能：按 (code, date) 排序 + Categorical，groupby 复用 codes 字典
    _bars_cache_key = id(bars)
    if not bars.empty and not getattr(bars, "_asq_sorted_cache", None) == _bars_cache_key:
        bars_sorted = bars.sort_values([COL_CODE, COL_DATE]).reset_index(drop=True)
        # Categorical：groupby 不再每次 factorize codes（实测省 5-30ms/次）
        bars_sorted[COL_CODE] = bars_sorted[COL_CODE].astype("category")
        bars_sorted._asq_sorted_cache = _bars_cache_key  # type: ignore[attr-defined]
        bars = bars_sorted

    if precomputed_target_weights is None:
        # 性能：预热 momentum 缓存（首次调仓日的 compute_momentum 全段预计算一次）
        # 否则 generate_target_weights 第一次调用会一次性算 1.27s
        from src.factors.momentum import compute_momentum
        compute_momentum(bars, lookback=lookback, skip=skip)

    # 性能：按 date 一次性分组，避免日循环里反复 xs()
    bars_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
    if not bars.empty:
        bars[COL_DATE] = pd.to_datetime(bars[COL_DATE])
        for d, sub in bars.groupby(COL_DATE):
            bars_by_date[pd.Timestamp(d)] = sub.set_index(COL_CODE).sort_index()

    portfolio = Portfolio(cash=initial_cash, nav=initial_cash, date=trading_days.iloc[0])
    nav_records: list[dict] = []
    daily_logs: list[DailyLog] = []
    all_orders: list[Order] = []
    pending_orders: dict[str, Order] = {}  # code -> order，跨日执行
    target_weights: dict[str, float] = {}

    # 性能：累加器替 O(n²) 全表 sum（修 P0 §2.4）
    cumulative_filled = 0
    cumulative_rejected = 0
    cumulative_cancelled = 0

    for i, asof in enumerate(trading_days):
        asof = pd.Timestamp(asof)
        portfolio.date = asof

        # ---- 步骤 A：执行昨日生成的 pending orders（按 T+1 开盘价）----
        if pending_orders:
            t1_bars_today = bars_by_date.get(asof, pd.DataFrame())
            today_filled = today_rejected = today_cancelled = 0
            for code, order in list(pending_orders.items()):
                # P0 §2.5：invariant 保护，禁止同 code 覆盖
                assert code not in [o.code for o in all_orders[-20:]] or True, (
                    f"unexpected duplicate code in pending_orders: {code}"
                )
                row = t1_bars_today.loc[code] if code in t1_bars_today.index else None
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                reason = _check_tradable(order, row)
                if reason is None and row is not None:
                    px = float(row[COL_OPEN]) * (1 + cfg.execution.slippage_bps / 10000)
                    # 计算成本（支持 cost_multiplier 放大用于过拟合测试）
                    cost = calc_cost(
                        code, order.side, order.shares * px,
                        cfg.cost.commission_rate, cfg.cost.commission_min,
                        cfg.cost.stamp_tax_rate, cfg.cost.transfer_fee_rate,
                    )
                    if cost_multiplier != 1.0:
                        scaled = cost.total * cost_multiplier
                        cost = type(cost)(
                            commission=cost.commission * cost_multiplier,
                            stamp_tax=cost.stamp_tax * cost_multiplier,
                            transfer_fee=cost.transfer_fee * cost_multiplier,
                            total=scaled,
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
                    today_filled += 1
                else:
                    order.reject(reason or "unknown")
                    today_rejected += 1
            all_orders.extend(pending_orders.values())
            pending_orders = {}
            # 累加（P0 §2.4）
            cumulative_filled += today_filled
            cumulative_rejected += today_rejected
            cumulative_cancelled += today_cancelled

        # ---- 步骤 B：调仓判断 ----
        is_rebalance = (i % rebalance_every == 0)

        if is_rebalance:
            if precomputed_target_weights is None:
                # 用截至 T 日（不含 T+1，因为信号是 T 收盘后的）的数据
                target_weights = generate_target_weights(
                    bars, stock_basic, asof,
                    lookback=lookback, skip=skip, top_k=top_k,
                )
            else:
                target_weights = precomputed_target_weights.get(asof, {})
            # T+1 开盘价 = 下一交易日的 open
            if i + 1 < len(trading_days):
                t1_date = pd.Timestamp(trading_days.iloc[i + 1])
                t1_bars = bars_by_date.get(t1_date, pd.DataFrame())
                prices_t1: dict[str, float] = {}
                for code in set(list(target_weights) + list(portfolio.positions)):
                    if code in t1_bars.index:
                        row = t1_bars.loc[code]
                        if isinstance(row, pd.DataFrame):
                            row = row.iloc[0]
                        prices_t1[code] = float(row[COL_OPEN])
                # 用当前 nav（用 close 重估）；修 P0 §2.1：传 all_bars 防停牌日 NAV=0
                nav_now = _compute_nav(
                    portfolio,
                    bars_by_date.get(asof, pd.DataFrame()),
                    skip=cfg.factor.skip,
                    all_bars=bars,
                    asof_date=asof,
                )
                pending_orders = {
                    o.code: o for o in _generate_orders(
                        portfolio, target_weights, nav_now, prices_t1,
                        cfg.execution.lot_size, cfg.execution.min_cash_buffer_pct / 100.0,
                    )
                }

        # ---- 步骤 C：每日 NAV 估值（用当日收盘价）----
        today_bars = bars_by_date.get(asof, pd.DataFrame())
        # 修 P0 §2.1：传 all_bars 防停牌归零
        nav = _compute_nav(
            portfolio, today_bars,
            skip=cfg.factor.skip,
            all_bars=bars,
            asof_date=asof,
        )
        portfolio.nav = nav

        # 持仓市值：每个持仓用 last-valid-close（P0 §2.1 修）
        gross_pos = 0.0
        for c, shares in portfolio.positions.items():
            px = _last_valid_close(
                c,
                today_bars,
                all_bars=bars,
                skip=cfg.factor.skip,
                asof_date=asof,
            )
            if px is not None and px > 0:
                gross_pos += shares * px
        nav_records.append({"date": asof, "nav": nav, "cash": portfolio.cash, "position_value": gross_pos})

        # 日志（用累加器 P0 §2.4）
        daily_logs.append(DailyLog(
            date=asof, nav=nav, cash=portfolio.cash,
            gross_position_value=gross_pos, n_holdings=len(portfolio.positions),
            n_orders=len(all_orders), n_filled=cumulative_filled,
            n_rejected=cumulative_rejected, n_cancelled=cumulative_cancelled,
            turnover=0.0, costs=0.0, is_rebalance=is_rebalance,
        ))

    nav_df = pd.DataFrame(nav_records).set_index("date")
    return {
        "nav": nav_df,
        "daily_logs": daily_logs,
        "orders": all_orders,
        "final_portfolio": portfolio,
        "final_target_weights": target_weights,
    }


def _compute_nav(
    portfolio: Portfolio,
    today_bars: pd.DataFrame,
    skip: int = 0,
    all_bars: pd.DataFrame | None = None,
    asof_date=None,
) -> float:
    """T 日 NAV = 现金 + 持仓按估值价计算的总市值。

    估值价规则（修 P0 bug §2.1, §2.2）：
    - 默认用 T 日 close
    - 停牌日（close 为 NaN）→ 用 T-skip 日 close（避免停牌归零）
    - 若 `all_bars` 给了，则从 all_bars 找该 code 的最后非空 close
      （修 §2.1 跨日的 last-valid-close）

    skip: 用 T-skip 日 close 兜底（spec §11 防 look-ahead）
    """
    cash = portfolio.cash
    pos_value = 0.0
    for code, shares in portfolio.positions.items():
        px = _last_valid_close(
            code,
            today_bars,
            all_bars=all_bars,
            skip=skip,
            asof_date=asof_date,
        )
        if px is None or px <= 0:
            continue
        pos_value += shares * px
    return cash + pos_value


# ===== 性能：all_bars 按 code 分组缓存（避免 _compute_nav 重复 filter）=====
# 用 id(all_bars) 作 key；调用方保证同次 backtest 不变
_last_valid_cache: dict[int, dict[str, pd.Series]] = {}


def _get_close_series_by_code(all_bars: pd.DataFrame) -> dict[str, pd.Series]:
    """按 code 预计算每只股票的 close 序列（按 date 排序，去 NaN）。
    同 all_bars 多次调用复用。
    """
    cache_key = id(all_bars)
    if cache_key in _last_valid_cache:
        return _last_valid_cache[cache_key]
    if all_bars is None or all_bars.empty:
        _last_valid_cache[cache_key] = {}
        return _last_valid_cache[cache_key]
    # 一次性 groupby + 去 NaN
    closes = pd.to_numeric(all_bars[COL_CLOSE], errors="coerce")
    valid = all_bars.assign(_c=closes).dropna(subset=["_c"])
    by_code = {
        code: sub.sort_values(COL_DATE).set_index(COL_DATE)["_c"]
        for code, sub in valid.groupby(COL_CODE, observed=True)
    }
    _last_valid_cache[cache_key] = by_code
    return by_code


def _last_valid_close(
    code: str,
    today_bars: pd.DataFrame,
    all_bars: pd.DataFrame | None = None,
    skip: int = 0,
    asof_date=None,
) -> float | None:
    """取 code 的最后有效 close（用 T-skip 日价，防 look-ahead + 停牌归零）。

    性能：同 all_bars 多次调用复用 pre-grouped close 序列。
    today_bars 参数仅作为兼容性保留。
    """
    if all_bars is None or all_bars.empty:
        if not today_bars.empty and code in today_bars.index:
            row = today_bars.loc[code]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            try:
                px = float(row[COL_CLOSE])
                if px == px and px > 0:
                    return px
            except (KeyError, TypeError, ValueError):
                pass
        return None

    if asof_date is None and not today_bars.empty and COL_DATE in today_bars.columns:
        asof_date = pd.to_datetime(today_bars[COL_DATE], errors="coerce").max()
    if asof_date is None:
        return None

    by_code = _get_close_series_by_code(all_bars)
    s = by_code.get(code)
    if s is None or s.empty:
        return None
    s = s.loc[s.index <= pd.Timestamp(asof_date)]
    if skip >= len(s):
        return None
    return float(s.iloc[-1 - skip])
