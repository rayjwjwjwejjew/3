"""导入测试：每个 src 子包必须能独立 import。

V1 第三阶段验收：项目骨架完整、所有模块边界已建立。
"""

import importlib
import sys


MODULES = [
    "src",
    "src.data",
    "src.data.schema",
    "src.universe",
    "src.factors",
    "src.strategy",
    "src.backtest",
    "src.risk",
    "src.reports",
    "src.config",
]


def test_all_modules_importable():
    failed = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001
            failed.append((name, repr(e)))
    assert not failed, f"failed to import: {failed}"


def test_package_version():
    import src
    assert src.__version__ == "0.1.0"


def test_config_loads():
    """strategy.yaml 必须可加载并满足 spec §7。"""
    from src.config import load_config
    cfg = load_config()
    # spec §7 必含字段
    assert cfg.universe.min_listing_days == 60
    assert cfg.factor.lookback == 120
    assert cfg.factor.skip == 5
    assert cfg.signal.rebalance_every == 20
    assert cfg.signal.check_every == 10
    assert cfg.signal.top_k == 10
    assert cfg.execution.lot_size == 100
    assert cfg.execution.price_basis == "open_t_plus_1"


def test_schema_bar_columns_complete():
    """字段契约必须覆盖 spec §2.1 所需的所有最小列。"""
    from src.data import BARS
    required = {
        "code", "date", "open", "high", "low", "close", "adj_close",
        "volume", "amount", "adj_factor",
        "is_suspended", "is_st", "limit_up", "limit_down",
    }
    assert set(BARS.required) >= required


def test_order_states_complete():
    """订单状态机必须覆盖 spec §6.3 全部 6 个状态。"""
    from src.data import ALL_ORDER_STATES
    assert set(ALL_ORDER_STATES) == {
        "CREATED", "SUBMITTED", "PARTIALLY_FILLED",
        "FILLED", "REJECTED", "CANCELLED",
    }


def test_dry_run_entrypoint():
    """`python -m src` 端到端必须可运行。"""
    import subprocess
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "src"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"dry run failed: stderr={result.stderr!r}"
    )
    assert "V1 phase-3 dry run" in result.stdout
