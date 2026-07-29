"""过拟合 / 稳健性测试（spec §10 第 12 阶段、§11）。

7 项检查：
1. 样本内 → 样本外
2. 滚动窗口
3. 调仓频率对比（5/10/20）
4. lookback 敏感性（110/120/130）
5. 成本 ×2
6. 删除表现最好年份后重测
7. 更换调仓日（V1 简化：跳过——调仓日固定为 N 个交易日，无法换"周一/周五"）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

from src.backtest.engine import run_backtest
from src.reports.performance import build_report, PerformanceReport

logger = logging.getLogger(__name__)


@dataclass
class OverfitResult:
    label: str
    report: PerformanceReport
    params: dict = field(default_factory=dict)


@dataclass
class OverfitSummary:
    results: list[OverfitResult] = field(default_factory=list)

    def add(self, label: str, report: PerformanceReport, **params) -> None:
        self.results.append(OverfitResult(label=label, report=report, params=dict(params)))

    def to_dataframe(self) -> pd.DataFrame:
        if not self.results:
            return pd.DataFrame(columns=["label"])
        rows = []
        for r in self.results:
            d = r.report.to_dict()
            row = {"label": r.label, **r.params}
            for k in ("total_return", "annualized_return", "annualized_vol", "sharpe",
                      "max_drawdown", "filled_trades", "annualized_turnover", "cost_ratio"):
                row[k] = d.get(k)
            rows.append(row)
        return pd.DataFrame(rows)

    def pretty(self) -> str:
        df = self.to_dataframe()
        cols = ["label"] + [c for c in ["rebalance_every", "lookback", "cost_multiplier",
                                          "excluded_year", "window", "is_oos"] if c in df.columns]
        cols += ["total_return", "annualized_return", "sharpe", "max_drawdown"]
        cols = [c for c in cols if c in df.columns]
        return df[cols].to_string(index=False, float_format=lambda x: f"{x:.4f}")


# ===== 测试 1: 样本内 → 样本外 =====
def run_in_out_sample(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    split_date: str,
    **run_kwargs,
) -> OverfitSummary:
    """按 split_date 切两段：is / oos。"""
    split = pd.Timestamp(split_date)
    cal = trade_calendar.copy()
    cal["date"] = pd.to_datetime(cal["date"])
    is_cal = cal[pd.to_datetime(cal["date"]) < split]
    oos_cal = cal[pd.to_datetime(cal["date"]) >= split]

    s = OverfitSummary()
    for label, sub_cal, tag in [("IS", is_cal, False), ("OOS", oos_cal, True)]:
        if sub_cal.empty:
            continue
        result = run_backtest(bars, stock_basic, sub_cal, **run_kwargs)
        rep = build_report(result["nav"], result["orders"], result.get("daily_logs"))
        s.add(label, rep, is_oos=tag)
    return s


# ===== 测试 2: 滚动窗口 =====
def run_rolling_windows(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    window_years: int = 3,
    step_years: int = 1,
    **run_kwargs,
) -> OverfitSummary:
    """滚动 window_years 年窗口，每次向前 step_years 年。"""
    cal_dates = pd.to_datetime(trade_calendar["date"]).sort_values()
    start, end = cal_dates.iloc[0], cal_dates.iloc[-1]
    s = OverfitSummary()
    cur = start
    idx = 0
    while cur + pd.DateOffset(years=window_years) <= end:
        win_end = cur + pd.DateOffset(years=window_years)
        sub = trade_calendar[
            (pd.to_datetime(trade_calendar["date"]) >= cur)
            & (pd.to_datetime(trade_calendar["date"]) < win_end)
        ]
        if sub.empty:
            break
        result = run_backtest(bars, stock_basic, sub, **run_kwargs)
        rep = build_report(result["nav"], result["orders"], result.get("daily_logs"))
        s.add(f"window_{idx}", rep, window=f"{cur.date()}__{win_end.date()}")
        cur = cur + pd.DateOffset(years=step_years)
        idx += 1
    return s


# ===== 测试 3: 调仓频率对比 =====
def run_rebalance_frequency(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    freqs: Iterable[int] = (5, 10, 20),
    **run_kwargs,
) -> OverfitSummary:
    s = OverfitSummary()
    for f in freqs:
        result = run_backtest(bars, stock_basic, trade_calendar, rebalance_every=f, **run_kwargs)
        rep = build_report(result["nav"], result["orders"], result.get("daily_logs"))
        s.add(f"rebal={f}", rep, rebalance_every=f)
    return s


# ===== 测试 4: lookback 敏感性 =====
def run_lookback_sensitivity(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    lookbacks: Iterable[int] = (110, 120, 130),
    **run_kwargs,
) -> OverfitSummary:
    s = OverfitSummary()
    for lb in lookbacks:
        result = run_backtest(bars, stock_basic, trade_calendar, lookback=lb, **run_kwargs)
        rep = build_report(result["nav"], result["orders"], result.get("daily_logs"))
        s.add(f"lookback={lb}", rep, lookback=lb)
    return s


# ===== 测试 5: 成本 ×2 =====
def run_cost_stress(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    multipliers: Iterable[float] = (1.0, 2.0),
    **run_kwargs,
) -> OverfitSummary:
    s = OverfitSummary()
    for m in multipliers:
        result = run_backtest(bars, stock_basic, trade_calendar, cost_multiplier=m, **run_kwargs)
        rep = build_report(result["nav"], result["orders"], result.get("daily_logs"))
        s.add(f"cost_x{m}", rep, cost_multiplier=m)
    return s


# ===== 测试 6: 删除表现最好年份 =====
def run_remove_best_year(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    **run_kwargs,
) -> OverfitSummary:
    """先跑一遍找出收益最高年份，剔除后重跑。"""
    result = run_backtest(bars, stock_basic, trade_calendar, **run_kwargs)
    base = build_report(result["nav"], result["orders"], result.get("daily_logs"))
    s = OverfitSummary()
    s.add("base", base)
    if base.yearly_returns.empty:
        return s
    best_year = base.yearly_returns.idxmax()
    # 剔除该年
    sub_cal = trade_calendar[
        pd.to_datetime(trade_calendar["date"]).dt.year != best_year.year
    ]
    if sub_cal.empty:
        return s
    result2 = run_backtest(bars, stock_basic, sub_cal, **run_kwargs)
    rep2 = build_report(result2["nav"], result2["orders"], result2.get("daily_logs"))
    s.add(f"excl_{best_year.year}", rep2, excluded_year=int(best_year.year))
    return s


# ===== 入口 =====
def run_all_overfit_tests(
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    split_date: str,
    **run_kwargs,
) -> dict[str, OverfitSummary]:
    """一次性跑完 6 项测试（spec §10 第 7 项"更换调仓日"暂未实现）。"""
    out = {
        "in_out_sample": run_in_out_sample(
            bars, stock_basic, trade_calendar, split_date, **run_kwargs),
        "rebalance_freq": run_rebalance_frequency(
            bars, stock_basic, trade_calendar, **run_kwargs),
        "lookback_sensitivity": run_lookback_sensitivity(
            bars, stock_basic, trade_calendar, **run_kwargs),
        "cost_stress": run_cost_stress(
            bars, stock_basic, trade_calendar, **run_kwargs),
        "remove_best_year": run_remove_best_year(
            bars, stock_basic, trade_calendar, **run_kwargs),
    }
    return out
