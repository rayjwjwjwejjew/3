"""紧急停止（spec §14 必备）。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 紧急停止标记文件路径（环境变量可覆盖）
EMERGENCY_FLAG = Path(
    os.environ.get("ASQ_EMERGENCY_FLAG", Path.home() / ".a_share_quant_emergency")
)


def is_emergency_stopped() -> bool:
    return EMERGENCY_FLAG.exists()


def emergency_stop(reason: str = "") -> None:
    """一键停止所有自动下单。"""
    EMERGENCY_FLAG.parent.mkdir(parents=True, exist_ok=True)
    with EMERGENCY_FLAG.open("w") as f:
        json.dump({
            "stopped": True,
            "reason": reason,
            "source": "a_share_quant",
        }, f, indent=2)
    # 同时关闭风控总开关
    from src.risk.controls import disable_trading
    disable_trading(reason=f"emergency_stop: {reason}")
    logger.error("EMERGENCY STOP TRIGGERED: %s", reason)


def clear_emergency_stop() -> None:
    """清除紧急停止（仅当操作者主动调用）。"""
    if EMERGENCY_FLAG.exists():
        EMERGENCY_FLAG.unlink()
        logger.warning("emergency stop cleared")
