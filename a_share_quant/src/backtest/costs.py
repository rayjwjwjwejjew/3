"""成本模型（spec §6.5）。

- 佣金：0.025%，最低 5 元（买卖各一次）
- 印花税：0.1%（仅卖出收取）
- 过户费：0.001%（仅沪市股票；买卖各收）
- 滑点：单边 5 bps（按方向不利方向加价，V1 在 generate_orders 时处理）
"""

from __future__ import annotations

from dataclasses import dataclass


def _is_shanghai(code: str) -> bool:
    """6 开头（沪 A，含科创板 688）。"""
    return code.startswith("6")


@dataclass
class CostBreakdown:
    commission: float
    stamp_tax: float
    transfer_fee: float
    total: float


def calc_cost(code: str, side: str, gross_amount: float,
              commission_rate: float, commission_min: float,
              stamp_tax_rate: float, transfer_fee_rate: float) -> CostBreakdown:
    """计算单笔交易成本。

    入参：
    - code: 股票代码
    - side: "BUY" / "SELL"
    - gross_amount: 成交金额（正数）
    """
    if gross_amount <= 0:
        return CostBreakdown(0, 0, 0, 0)
    commission = max(gross_amount * commission_rate, commission_min)
    stamp_tax = gross_amount * stamp_tax_rate if side == "SELL" else 0.0
    transfer_fee = gross_amount * transfer_fee_rate if _is_shanghai(code) else 0.0
    return CostBreakdown(
        commission=commission,
        stamp_tax=stamp_tax,
        transfer_fee=transfer_fee,
        total=commission + stamp_tax + transfer_fee,
    )
