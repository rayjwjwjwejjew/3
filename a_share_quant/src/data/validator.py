"""数据质量验证（spec §5.1）。

八项必查：
1. 同一 (code, date) 无重复
2. 交易日历连续（允许长假空缺）
3. 价格 > 0
4. 成交量 ≥ 0
5. 复权因子无异常跳变（相邻日变化 > 50% 视为异常）
6. 股票不在上市日前出现数据
7. 数据日期 ≤ asof_date（不得使用未来信息）
8. 涨跌停价与昨收关系正确（误差 < 0.01）

设计要点：
- 每项 check 输出 `ValidationFinding` 列表
- DataValidator 统一聚合，写入 data_quality_report.csv
- 任何 ERROR 级 finding 抛 `DataQualityError`，中止回测
- WARNING 级只写报告，不中断
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from src.data.schema import (
    BARS,
    COL_ADJ_FACTOR,
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_HIGH,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_LOW,
    COL_OPEN,
    COL_ST,
    COL_VOL,
)
from src.data import downloader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationFinding:
    check: str
    severity: str  # "ERROR" | "WARNING"
    scope: str     # 检查范围描述
    detail: str    # 人可读详情
    n_affected: int = 0

    def to_row(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "scope": self.scope,
            "detail": self.detail,
            "n_affected": self.n_affected,
        }


class DataQualityError(RuntimeError):
    """数据验证发现 ERROR 级问题，应当中止回测。"""


# ===== 单项检查 =====
def check_no_duplicate_keys(bars: pd.DataFrame) -> list[ValidationFinding]:
    """检查 1：(code, date) 唯一性。"""
    if bars.empty:
        return []
    dup = bars.duplicated(subset=[COL_CODE, COL_DATE], keep=False)
    n = int(dup.sum())
    if n == 0:
        return []
    sample = bars.loc[dup, [COL_CODE, COL_DATE]].head(5).to_dict("records")
    return [ValidationFinding(
        check="no_duplicate_keys",
        severity="ERROR",
        scope="bars",
        detail=f"{n} duplicate (code, date) rows; sample={sample}",
        n_affected=n,
    )]


def check_price_positive(bars: pd.DataFrame) -> list[ValidationFinding]:
    """检查 3：open/high/low/close 严格 > 0。"""
    if bars.empty:
        return []
    out = []
    for c in (COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE):
        bad = (bars[c] <= 0) & bars[c].notna()
        n = int(bad.sum())
        if n > 0:
            out.append(ValidationFinding(
                check="price_positive",
                severity="ERROR",
                scope=f"bars.{c}",
                detail=f"{n} rows with non-positive {c}",
                n_affected=n,
            ))
    return out


def check_volume_non_negative(bars: pd.DataFrame) -> list[ValidationFinding]:
    """检查 4：volume >= 0。"""
    if bars.empty:
        return []
    bad = (bars[COL_VOL] < 0) & bars[COL_VOL].notna()
    n = int(bad.sum())
    if n == 0:
        return []
    return [ValidationFinding(
        check="volume_non_negative",
        severity="ERROR",
        scope="bars.volume",
        detail=f"{n} rows with negative volume",
        n_affected=n,
    )]


def check_adj_factor_jump(bars: pd.DataFrame, max_change: float = 0.5) -> list[ValidationFinding]:
    """检查 5：复权因子相邻日变化 > max_change 视为异常。

    按 (code) 分组；只对非空的因子值计算。
    """
    if bars.empty or COL_ADJ_FACTOR not in bars.columns:
        return []
    findings: list[ValidationFinding] = []
    for code, grp in bars.groupby(COL_CODE):
        g = grp.sort_values(COL_DATE)
        prev = g[COL_ADJ_FACTOR].shift(1)
        ratio = (g[COL_ADJ_FACTOR] / prev).abs() - 1.0
        bad = (ratio > max_change) & ratio.notna()
        n = int(bad.sum())
        if n > 0:
            findings.append(ValidationFinding(
                check="adj_factor_jump",
                severity="WARNING",
                scope=f"code={code}",
                detail=f"{n} adj_factor jumps > {max_change:.0%}",
                n_affected=n,
            ))
    return findings


def check_no_data_before_listing(bars: pd.DataFrame, stock_basic: pd.DataFrame) -> list[ValidationFinding]:
    """检查 6：股票不在上市日前出现数据。"""
    if bars.empty or stock_basic.empty:
        return []
    merged = bars.merge(
        stock_basic[[COL_CODE, "list_date"]],
        on=COL_CODE, how="left", validate="many_to_one",
    )
    # 仅看有 list_date 的行
    mask = merged["list_date"].notna() & merged[COL_DATE].notna()
    early = mask & (merged[COL_DATE] < merged["list_date"])
    n = int(early.sum())
    if n == 0:
        return []
    return [ValidationFinding(
        check="no_data_before_listing",
        severity="ERROR",
        scope="bars vs stock_basic.list_date",
        detail=f"{n} bar rows earlier than their stock's listing date",
        n_affected=n,
    )]


def check_no_future_data(bars: pd.DataFrame, asof_date) -> list[ValidationFinding]:
    """检查 7：所有数据日期 <= asof_date。

    asof_date 接受 date / str / Timestamp。
    """
    if bars.empty:
        return []
    asof = pd.Timestamp(asof_date)
    future = bars[COL_DATE] > asof
    n = int(future.sum())
    if n == 0:
        return []
    return [ValidationFinding(
        check="no_future_data",
        severity="ERROR",
        scope="bars.date",
        detail=f"{n} bar rows with date > asof_date={asof.date()}",
        n_affected=n,
    )]


def check_limit_price_consistency(bars: pd.DataFrame, tol: float = 0.01) -> list[ValidationFinding]:
    """检查 8：涨跌停价与昨收 × (1±阈值) 一致（容差 0.01 元）。

    对每行：|limit_up - prev_close * (1+threshold)| < tol
    threshold 由 code 与 is_st 推算（与 cleaner.add_limit_prices 同规则）。
    """
    if bars.empty:
        return []
    df = bars.sort_values([COL_CODE, COL_DATE]).reset_index(drop=True)
    prev_close = df.groupby(COL_CODE)[COL_CLOSE].shift(1)
    is_st = df["is_st"].astype(bool) if "is_st" in df.columns else pd.Series(False, index=df.index)
    threshold = is_st.map({True: 0.05, False: 0.10}).astype(float)
    # 创板 / 科创板覆盖 is_st=False 时的 0.20（与 cleaner 一致）
    code_str = df[COL_CODE].astype(str)
    kechuang = code_str.str.startswith("688")
    chinext = code_str.str.startswith("30")
    threshold = threshold.where(~((kechuang | chinext) & ~is_st), 0.20)

    expected_up = (prev_close * (1 + threshold)).round(2)
    expected_dn = (prev_close * (1 - threshold)).round(2)
    # 只对 limit_up/down 非空的行做检查
    mask = df[COL_LIMIT_UP].notna() & df[COL_LIMIT_DOWN].notna() & prev_close.notna()
    bad_up = mask & ((df[COL_LIMIT_UP] - expected_up).abs() > tol)
    bad_dn = mask & ((df[COL_LIMIT_DOWN] - expected_dn).abs() > tol)
    n = int((bad_up | bad_dn).sum())
    if n == 0:
        return []
    return [ValidationFinding(
        check="limit_price_consistency",
        severity="WARNING",
        scope="bars.limit_up/down",
        detail=f"{n} rows with limit prices inconsistent with prev close (tol={tol})",
        n_affected=n,
    )]


def check_calendar_continuity(calendar: pd.DataFrame) -> list[ValidationFinding]:
    """检查 2：交易日历连续性。

    不要求每个日历日都是交易日；只检查日历本身没有 (date) 重复、没有显著间隔。
    A 股长假可能造成 7-10 日的非交易间隔，超出 15 日视为异常（仅 WARNING）。
    """
    if calendar.empty:
        return [ValidationFinding(
            check="calendar_continuity",
            severity="ERROR",
            scope="trade_calendar",
            detail="calendar is empty",
            n_affected=0,
        )]
    findings = []
    if calendar[COL_DATE].duplicated().any():
        n = int(calendar[COL_DATE].duplicated().sum())
        findings.append(ValidationFinding(
            check="calendar_continuity",
            severity="ERROR",
            scope="trade_calendar",
            detail=f"{n} duplicate dates in calendar",
            n_affected=n,
        ))
    # 最大间隔
    sorted_dates = pd.to_datetime(calendar[COL_DATE]).sort_values()
    if len(sorted_dates) >= 2:
        gaps = sorted_dates.diff().dt.days
        max_gap = int(gaps.max())
        if max_gap > 15:
            findings.append(ValidationFinding(
                check="calendar_continuity",
                severity="WARNING",
                scope="trade_calendar",
                detail=f"max gap between consecutive dates = {max_gap} days",
                n_affected=1,
            ))
    return findings


# ===== 入口 =====
class DataValidator:
    """一次性跑完所有 check，生成报告 + 按 severity 决定是否抛错。"""

    DEFAULT_REPORT_PATH: Path = Path("data") / "data_quality_report.csv"

    def __init__(
        self,
        bars: pd.DataFrame,
        stock_basic: pd.DataFrame,
        trade_calendar: pd.DataFrame,
        asof_date,
        report_path: Path | None = None,
    ):
        BARS.validate(bars)
        self.bars = bars
        self.stock_basic = stock_basic
        self.trade_calendar = trade_calendar
        self.asof_date = asof_date
        self.report_path = Path(report_path) if report_path else self.DEFAULT_REPORT_PATH
        self.findings: list[ValidationFinding] = []

    def _run(self, check_fn: Callable[[], Iterable[ValidationFinding]]) -> None:
        try:
            self.findings.extend(check_fn())
        except Exception as e:  # noqa: BLE001
            # check 函数本身崩了：记为 ERROR，方便调试
            self.findings.append(ValidationFinding(
                check=getattr(check_fn, "__name__", "unknown"),
                severity="ERROR",
                scope="validator",
                detail=f"check raised: {type(e).__name__}: {e}",
                n_affected=0,
            ))

    def run_all(self) -> pd.DataFrame:
        self._run(lambda: check_no_duplicate_keys(self.bars))
        self._run(lambda: check_price_positive(self.bars))
        self._run(lambda: check_volume_non_negative(self.bars))
        self._run(lambda: check_adj_factor_jump(self.bars))
        self._run(lambda: check_no_data_before_listing(self.bars, self.stock_basic))
        self._run(lambda: check_no_future_data(self.bars, self.asof_date))
        self._run(lambda: check_limit_price_consistency(self.bars))
        self._run(lambda: check_calendar_continuity(self.trade_calendar))
        return self.report()

    def report(self) -> pd.DataFrame:
        """返回 findings 的 DataFrame 视图（不写盘）。"""
        if not self.findings:
            return pd.DataFrame(columns=["check", "severity", "scope", "detail", "n_affected"])
        return pd.DataFrame([f.to_row() for f in self.findings])

    def write_report(self) -> Path:
        df = self.report()
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.report_path, index=False)
        return self.report_path

    def assert_clean(self) -> None:
        """任何 ERROR 级 finding 抛 DataQualityError。"""
        errors = [f for f in self.findings if f.severity == "ERROR"]
        if errors:
            msg = "\n".join(f"  [{e.check}] {e.scope}: {e.detail}" for e in errors)
            raise DataQualityError(
                f"data validation found {len(errors)} ERROR(s):\n{msg}\n"
                f"see report: {self.report_path}"
            )


# ===== 便捷：从 processed 直接跑 =====
def validate_processed(asof_date, report_path: Path | None = None) -> pd.DataFrame:
    """读取 data/processed/* 全部跑验证，必要时写报告。"""
    from src.data.cleaner import (
        PROC_BARS,
        PROC_STOCK_BASIC,
        PROC_TRADE_CALENDAR,
    )
    bars_path = PROC_BARS() if callable(PROC_BARS) else PROC_BARS
    sb_path = PROC_STOCK_BASIC() if callable(PROC_STOCK_BASIC) else PROC_STOCK_BASIC
    tc_path = PROC_TRADE_CALENDAR() if callable(PROC_TRADE_CALENDAR) else PROC_TRADE_CALENDAR

    if not bars_path.exists():
        raise FileNotFoundError(f"bars not found: {bars_path}（请先跑 make self-test 或 make clean-data）")
    bars = pd.read_parquet(bars_path)
    sb = pd.read_parquet(sb_path) if sb_path.exists() else pd.DataFrame(columns=[COL_CODE, "list_date", "delist_date"])
    tc = pd.read_parquet(tc_path) if tc_path.exists() else pd.DataFrame(columns=[COL_DATE, "is_trading_day"])

    v = DataValidator(bars, sb, tc, asof_date=asof_date, report_path=report_path)
    df = v.run_all()
    v.write_report()
    v.assert_clean()
    return df
