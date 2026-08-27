"""配置加载器。

V1 阶段从 `config/strategy.yaml` 加载一次，构造不可变 dataclass。
所有模块不接受运行时改配置——这是为了"同一任务运行两次结果一致"
（spec §13 幂等性要求）服务的。

不要在本文件写业务逻辑；只做 yaml → 对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ===== 路径常量（项目根 = 本文件上两级）=====
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
CONFIG_PATH: Path = PROJECT_ROOT / "config" / "strategy.yaml"
DATA_RAW: Path = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED: Path = PROJECT_ROOT / "data" / "processed"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
RESULTS_DIR: Path = PROJECT_ROOT / "results"


@dataclass(frozen=True)
class UniverseConfig:
    start_date: str
    end_date: str | None
    min_listing_days: int
    min_avg_amount_20d: float
    include_board: tuple[str, ...]


@dataclass(frozen=True)
class FactorConfig:
    name: str
    lookback: int
    skip: int
    tie_break_seed: int


@dataclass(frozen=True)
class SignalConfig:
    rebalance_every: int
    check_every: int
    top_k: int
    min_holdings: int


@dataclass(frozen=True)
class ExecutionConfig:
    price_basis: str
    lot_size: int
    min_cash_buffer_pct: float
    slippage_bps: float


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float
    commission_min: float
    stamp_tax_rate: float
    transfer_fee_rate: float


@dataclass(frozen=True)
class RiskConfig:
    trading_enabled: bool
    max_single_weight: float
    max_industry_weight: float
    max_holdings: int
    min_cash_pct: float


@dataclass(frozen=True)
class LoggingConfig:
    log_dir: str
    result_dir: str


@dataclass(frozen=True)
class StrategyConfig:
    """顶层不可变配置对象。"""
    universe: UniverseConfig
    factor: FactorConfig
    signal: SignalConfig
    execution: ExecutionConfig
    cost: CostConfig
    risk: RiskConfig
    logging: LoggingConfig
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def paths(self) -> dict[str, Path]:
        return {
            "project_root": PROJECT_ROOT,
            "config": CONFIG_PATH,
            "data_raw": DATA_RAW,
            "data_processed": DATA_PROCESSED,
            "logs": LOGS_DIR,
            "results": RESULTS_DIR,
        }


def _as_tuple(x: Any) -> tuple[str, ...]:
    """yaml 列表 → tuple（frozen dataclass 需要）。"""
    if x is None:
        return ()
    if isinstance(x, (list, tuple)):
        return tuple(x)
    raise TypeError(f"expected list/tuple, got {type(x).__name__}: {x!r}")


def load_config(path: Path | str | None = None) -> StrategyConfig:
    """从 yaml 加载配置。缺文件则抛 FileNotFoundError。"""
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"strategy config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"config yaml must be a mapping, got {type(raw).__name__}")

    uni = raw["universe"]
    fac = raw["factor"]
    sig = raw["signal"]
    exe = raw["execution"]
    cost = raw["cost"]
    risk = raw["risk"]
    log = raw["logging"]

    return StrategyConfig(
        universe=UniverseConfig(
            start_date=uni["start_date"],
            end_date=uni.get("end_date"),
            min_listing_days=int(uni["min_listing_days"]),
            min_avg_amount_20d=float(uni["min_avg_amount_20d"]),
            include_board=_as_tuple(uni["include_board"]),
        ),
        factor=FactorConfig(
            name=str(fac["name"]),
            lookback=int(fac["lookback"]),
            skip=int(fac["skip"]),
            tie_break_seed=int(fac["tie_break_seed"]),
        ),
        signal=SignalConfig(
            rebalance_every=int(sig["rebalance_every"]),
            check_every=int(sig["check_every"]),
            top_k=int(sig["top_k"]),
            min_holdings=int(sig["min_holdings"]),
        ),
        execution=ExecutionConfig(
            price_basis=str(exe["price_basis"]),
            lot_size=int(exe["lot_size"]),
            min_cash_buffer_pct=float(exe["min_cash_buffer_pct"]),
            slippage_bps=float(exe["slippage_bps"]),
        ),
        cost=CostConfig(
            commission_rate=float(cost["commission_rate"]),
            commission_min=float(cost["commission_min"]),
            stamp_tax_rate=float(cost["stamp_tax_rate"]),
            transfer_fee_rate=float(cost["transfer_fee_rate"]),
        ),
        risk=RiskConfig(
            trading_enabled=bool(risk["TRADING_ENABLED"]),
            max_single_weight=float(risk["max_single_weight"]),
            max_industry_weight=float(risk["max_industry_weight"]),
            max_holdings=int(risk["max_holdings"]),
            min_cash_pct=float(risk["min_cash_pct"]),
        ),
        logging=LoggingConfig(
            log_dir=str(log["log_dir"]),
            result_dir=str(log["result_dir"]),
        ),
        raw=raw,
    )
