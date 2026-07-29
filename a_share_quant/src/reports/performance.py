"""回测报告（spec §9）。

必含项：
- 总收益率、年化收益率
- 最大回撤（绝对值、起止日、修复天数）
- 年化波动率
- 夏普比率（无风险利率默认 2%）
- 换手率（年化）
- 总交易次数、买入/卖出/被拒次数
- 成本占收益比例
- 相对基准（V1 暂用沪深 300 近似 = 同区间 NAV-等权做参考；规范基准在 V2 接 baostock index）
- 每年收益柱状图数据
- 最长亏损周期（连续亏损月份数）
- 订单状态分布
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.broker import (
    ORDER_CANCELLED,
    ORDER_FILLED,
    ORDER_REJECTED,
)


# ===== 风险指标 =====
def _max_drawdown(nav: pd.Series) -> dict:
    s = nav.dropna()
    if s.empty:
        return {"max_drawdown": 0.0, "start": None, "end": None, "recovery_days": None}
    running_max = s.cummax()
    dd = s / running_max - 1.0
    end = dd.idxmin()
    start = s.loc[:end].idxmax()
    # 修复日：之后第一次回到 start 处的值
    after_end = s.loc[end:]
    recovered = after_end[after_end >= s.loc[start]]
    if not recovered.empty:
        rec_date = recovered.index[0]
        recovery_days = int((rec_date - end).days)
    else:
        recovery_days = None
    return {
        "max_drawdown": float(dd.min()),
        "start": start,
        "end": end,
        "recovery_days": recovery_days,
    }


def _sharpe(daily_ret: pd.Series, rf_annual: float = 0.02, periods_per_year: int = 252) -> float:
    r = daily_ret.dropna()
    if r.std() == 0 or len(r) < 2:
        return 0.0
    rf_daily = (1 + rf_annual) ** (1 / periods_per_year) - 1
    excess = r - rf_daily
    return float(excess.mean() / r.std() * math.sqrt(periods_per_year))


def _annualized_return(nav: pd.Series, periods_per_year: int = 252) -> float:
    s = nav.dropna()
    if len(s) < 2 or s.iloc[0] <= 0:
        return 0.0
    n_days = (s.index[-1] - s.index[0]).days
    if n_days <= 0:
        return 0.0
    years = n_days / 365.25
    return float((s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1)


def _annualized_vol(daily_ret: pd.Series, periods_per_year: int = 252) -> float:
    r = daily_ret.dropna()
    if r.empty:
        return 0.0
    return float(r.std() * math.sqrt(periods_per_year))


def _longest_losing_streak_months(daily_ret: pd.Series) -> int:
    """连续亏损月数：月频计算每月收益，统计最长连续负收益月数。"""
    if daily_ret.empty:
        return 0
    monthly = (1 + daily_ret).resample("ME").prod() - 1
    longest = 0
    cur = 0
    for v in monthly:
        if v < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return longest


def _yearly_returns(nav: pd.Series) -> pd.Series:
    """按年汇总收益。"""
    if nav.empty:
        return pd.Series(dtype=float)
    yearly = nav.resample("YE").last()
    ret = yearly.pct_change()
    ret.iloc[0] = (yearly.iloc[0] / nav.iloc[0]) - 1.0 if nav.iloc[0] > 0 else 0.0
    return ret


def _turnover(orders: list, nav_mean: float) -> float:
    """年化换手率：sum(|成交金额|) / mean_nav。"""
    gross = 0.0
    for o in orders:
        if o.status == ORDER_FILLED:
            gross += abs(o.cash_flow)
    if nav_mean <= 0:
        return 0.0
    return float(gross / nav_mean)


# ===== 主报告 =====
@dataclass
class PerformanceReport:
    start_date: pd.Timestamp | None
    end_date: pd.Timestamp | None
    initial_cash: float
    final_nav: float
    total_return: float
    annualized_return: float
    annualized_vol: float
    sharpe: float
    max_drawdown: float
    max_dd_start: pd.Timestamp | None
    max_dd_end: pd.Timestamp | None
    max_dd_recovery_days: int | None
    total_trades: int
    filled_trades: int
    rejected_trades: int
    cancelled_trades: int
    annualized_turnover: float
    total_costs: float
    cost_ratio: float
    longest_losing_streak_months: int
    yearly_returns: pd.Series = field(default_factory=pd.Series)
    order_status_distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["yearly_returns"] = {str(k.date()): float(v) for k, v in self.yearly_returns.items()}
        return d

    def pretty(self) -> str:
        lines = [
            "=" * 60,
            "V1 回测报告",
            "=" * 60,
            f"区间: {self.start_date.date() if self.start_date else '?'} → "
            f"{self.end_date.date() if self.end_date else '?'}",
            f"初始资金: {self.initial_cash:,.2f}    终值 NAV: {self.final_nav:,.2f}",
            "",
            "── 收益 ──",
            f"总收益率:        {self.total_return:.2%}",
            f"年化收益率:      {self.annualized_return:.2%}",
            f"年化波动率:      {self.annualized_vol:.2%}",
            f"夏普比率 (rf=2%): {self.sharpe:.3f}",
            "",
            "── 风险 ──",
            f"最大回撤:        {self.max_drawdown:.2%}",
            f"  起始日: {self.max_dd_start.date() if self.max_dd_start else '?'}",
            f"  结束日: {self.max_dd_end.date() if self.max_dd_end else '?'}",
            f"  修复天数: {self.max_dd_recovery_days if self.max_dd_recovery_days is not None else '未修复'}",
            f"最长亏损周期:    {self.longest_losing_streak_months} 月",
            "",
            "── 交易 ──",
            f"调仓/订单总数:   {self.total_trades}",
            f"  FILLED:    {self.filled_trades}",
            f"  REJECTED:  {self.rejected_trades}",
            f"  CANCELLED: {self.cancelled_trades}",
            f"年化换手率:      {self.annualized_turnover:.2%}",
            f"总成本:          {self.total_costs:,.2f}",
            f"成本 / 收益:     {self.cost_ratio:.2%}",
            "",
            "── 年度收益 ──",
        ]
        for y, r in self.yearly_returns.items():
            lines.append(f"  {y.date() if hasattr(y, 'date') else y}: {r:.2%}")
        if not self.yearly_returns.any():
            lines.append("  (无)")
        return "\n".join(lines)


def build_report(
    nav: pd.DataFrame,
    orders: list,
    daily_logs: list | None = None,
    rf_annual: float = 0.02,
) -> PerformanceReport:
    """根据回测输出生成完整报告。

    入参：
    - nav: run_backtest 返回的 nav DataFrame，索引为 date，列含 nav / cash / position_value
    - orders: run_backtest 返回的 orders 列表
    - daily_logs: 可选，用于年度收益等
    """
    if nav.empty or "nav" not in nav.columns:
        # 全空报告
        return PerformanceReport(
            start_date=None, end_date=None, initial_cash=0.0, final_nav=0.0,
            total_return=0.0, annualized_return=0.0, annualized_vol=0.0,
            sharpe=0.0, max_drawdown=0.0, max_dd_start=None, max_dd_end=None,
            max_dd_recovery_days=None,
            total_trades=0, filled_trades=0, rejected_trades=0, cancelled_trades=0,
            annualized_turnover=0.0, total_costs=0.0, cost_ratio=0.0,
            longest_losing_streak_months=0, yearly_returns=pd.Series(dtype=float),
            order_status_distribution={},
        )

    s = nav["nav"].dropna()
    daily_ret = s.pct_change().dropna()
    initial = float(s.iloc[0])
    final = float(s.iloc[-1])
    total_return = (final / initial - 1) if initial > 0 else 0.0

    dd = _max_drawdown(s)
    sharpe = _sharpe(daily_ret, rf_annual=rf_annual)
    ann_ret = _annualized_return(s)
    ann_vol = _annualized_vol(daily_ret)
    longest_losing = _longest_losing_streak_months(daily_ret)
    yearly = _yearly_returns(s)

    # 订单统计
    status_dist: dict[str, int] = {}
    for o in orders:
        status_dist[o.status] = status_dist.get(o.status, 0) + 1
    filled = status_dist.get(ORDER_FILLED, 0)
    rejected = status_dist.get(ORDER_REJECTED, 0)
    cancelled = status_dist.get(ORDER_CANCELLED, 0)

    # 换手率
    nav_mean = float(s.mean())
    turnover = _turnover(orders, nav_mean)

    # 总成本
    total_costs = sum(
        abs(getattr(o, "cost_total", 0.0)) for o in orders
        if o.status == ORDER_FILLED
    )
    # 成本/收益
    pnl = final - initial
    cost_ratio = abs(total_costs) / abs(pnl) if pnl != 0 else 0.0

    return PerformanceReport(
        start_date=s.index[0],
        end_date=s.index[-1],
        initial_cash=initial,
        final_nav=final,
        total_return=total_return,
        annualized_return=ann_ret,
        annualized_vol=ann_vol,
        sharpe=sharpe,
        max_drawdown=dd["max_drawdown"],
        max_dd_start=dd["start"],
        max_dd_end=dd["end"],
        max_dd_recovery_days=dd["recovery_days"],
        total_trades=len(orders),
        filled_trades=filled,
        rejected_trades=rejected,
        cancelled_trades=cancelled,
        annualized_turnover=turnover,
        total_costs=total_costs,
        cost_ratio=cost_ratio,
        longest_losing_streak_months=longest_losing,
        yearly_returns=yearly,
        order_status_distribution=status_dist,
    )
