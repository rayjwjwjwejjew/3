"""风控（spec §10）。

- 单只最大仓位：max_single_weight
- 单一行业最大：max_industry_weight（V1 暂用占位字段，无行业数据时为 None）
- 最大持仓数：max_holdings
- 最低现金：min_cash_pct
- 数据异常禁交易
- 持仓不一致禁下单
- 总开关：TRADING_ENABLED

任何严重异常自动置 TRADING_ENABLED = False。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.config import load_config
from src.data.schema import COL_CODE

logger = logging.getLogger(__name__)


# 模块级总开关（spec §10）
TRADING_ENABLED: bool = True


@dataclass
class RiskViolation:
    rule: str
    severity: str  # "ERROR" | "WARNING"
    detail: str
    scope: str = ""


@dataclass
class RiskReport:
    violations: list[RiskViolation] = field(default_factory=list)
    trading_enabled: bool = True

    @property
    def errors(self) -> list[RiskViolation]:
        return [v for v in self.violations if v.severity == "ERROR"]

    @property
    def has_blocking(self) -> bool:
        return any(v.severity == "ERROR" for v in self.violations) or not self.trading_enabled


def disable_trading(reason: str) -> None:
    """紧急停止交易。"""
    global TRADING_ENABLED
    if TRADING_ENABLED:
        logger.error("TRADING_ENABLED set to False: %s", reason)
    TRADING_ENABLED = False


# ===== 持仓权重校验 =====
def check_max_single_weight(
    weights: dict[str, float],
    limit: float,
) -> list[RiskViolation]:
    """单只股票权重 ≤ limit。"""
    out = []
    for code, w in weights.items():
        if w > limit + 1e-9:
            out.append(RiskViolation(
                rule="max_single_weight",
                severity="ERROR",
                scope=f"code={code}",
                detail=f"weight {w:.2%} > limit {limit:.2%}",
            ))
    return out


def check_max_holdings(
    weights: dict[str, float],
    limit: int,
) -> list[RiskViolation]:
    """持仓数 ≤ limit。"""
    n = len(weights)
    if n > limit:
        return [RiskViolation(
            rule="max_holdings",
            severity="ERROR",
            detail=f"holdings {n} > limit {limit}",
        )]
    return []


def check_min_holdings(
    weights: dict[str, float],
    floor: int,
) -> list[RiskViolation]:
    """持仓数过少 = WARNING（不阻塞）。"""
    n = len(weights)
    if 0 < n < floor:
        return [RiskViolation(
            rule="min_holdings",
            severity="WARNING",
            detail=f"holdings {n} < floor {floor}; consider going to cash",
        )]
    return []


def check_weights_sum(
    weights: dict[str, float],
    tol: float = 0.01,
) -> list[RiskViolation]:
    """权重之和 ≤ 1 + tol。"""
    s = sum(weights.values())
    if s > 1.0 + tol:
        return [RiskViolation(
            rule="weights_sum",
            severity="ERROR",
            detail=f"sum of weights {s:.4f} > 1 + tol {tol}",
        )]
    return []


def check_cash_ratio(
    cash: float,
    nav: float,
    min_pct: float,
) -> list[RiskViolation]:
    """现金 / nav ≥ min_pct。"""
    if nav <= 0:
        return [RiskViolation(
            rule="cash_ratio",
            severity="ERROR",
            detail=f"nav is {nav}; cannot evaluate cash ratio",
        )]
    ratio = cash / nav
    if ratio < min_pct - 1e-9:
        return [RiskViolation(
            rule="cash_ratio",
            severity="ERROR",
            detail=f"cash {ratio:.2%} < min {min_pct:.2%}",
        )]
    return []


def check_portfolio_consistency(
    positions: dict[str, int],
    weights: dict[str, float],
) -> list[RiskViolation]:
    """目标权重 = 0 但持仓 > 0 是合法的（要卖出）；
    目标权重 > 0 但不在持仓里也合法（要买入）。
    真正的"不一致"是同一 code 在两处用了不同形式但语义冲突——V1 仅做最弱检查。"""
    # 暂无可一致性冲突，return empty
    return []


# ===== 入口 =====
def run_pre_trade_checks(
    weights: dict[str, float],
    cash: float,
    nav: float,
) -> RiskReport:
    """调仓前一次性跑完所有风控 check，生成报告。

    任何 ERROR：自动 disable_trading，trading_enabled=False。
    """
    cfg = load_config()
    global TRADING_ENABLED

    report = RiskReport(trading_enabled=TRADING_ENABLED)
    report.violations.extend(check_max_single_weight(weights, cfg.risk.max_single_weight))
    report.violations.extend(check_max_holdings(weights, cfg.risk.max_holdings))
    report.violations.extend(check_min_holdings(weights, cfg.signal.min_holdings))
    report.violations.extend(check_weights_sum(weights))
    report.violations.extend(check_cash_ratio(cash, nav, cfg.risk.min_cash_pct))
    report.violations.extend(check_portfolio_consistency({}, weights))

    if report.has_blocking:
        disable_trading(reason="; ".join(f"[{v.rule}] {v.detail}" for v in report.errors))
        report.trading_enabled = False

    return report


def apply_post_fill_safety(
    portfolio: Any,
    weights_target: dict[str, float],
) -> list[RiskViolation]:
    """成交后再次校验实际持仓是否与目标一致（V1 简化：仅报，不回滚）。"""
    # V1 阶段：仅返回空（spec §10 标注的"持仓不一致时禁止继续下单"在 V2 阶段实装）
    return []
