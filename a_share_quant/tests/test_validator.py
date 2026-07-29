"""validator 测试：8 项 check 全部覆盖 + 严重性分级。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

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
    COL_VOL,
    COL_ST,
    COL_SUSPENDED,
)
from src.data.validator import (
    DataQualityError,
    DataValidator,
    ValidationFinding,
    check_adj_factor_jump,
    check_calendar_continuity,
    check_limit_price_consistency,
    check_no_data_before_listing,
    check_no_duplicate_keys,
    check_no_future_data,
    check_price_positive,
    check_volume_non_negative,
)


def _make_bars(rows: list[dict]) -> pd.DataFrame:
    """辅助：造一个 schema 合规的 bars DataFrame。"""
    base = {
        COL_CODE: "600000",
        COL_DATE: pd.to_datetime("2024-01-02"),
        COL_OPEN: 10.0, COL_HIGH: 10.5, COL_LOW: 9.8, COL_CLOSE: 10.2,
        "adj_close": 10.2, COL_VOL: 1_000_000, COL_AMOUNT: 10_200_000.0,
        COL_ADJ_FACTOR: 1.0,
        COL_SUSPENDED: False, COL_ST: False,
        COL_LIMIT_UP: 11.22, COL_LIMIT_DOWN: 9.18,
    }
    out = []
    for r in rows:
        row = base.copy()
        row.update(r)
        out.append(row)
    df = pd.DataFrame(out)
    if not df.empty:
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
    return df


def _make_basic(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _make_calendar(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        COL_DATE: pd.to_datetime(dates),
        "is_trading_day": True,
    })


# ===== 单项 check =====
def test_check_no_duplicate_keys_clean():
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_CLOSE: 10.0},
        {COL_DATE: "2024-01-03", COL_CLOSE: 10.1},
    ])
    assert check_no_duplicate_keys(bars) == []


def test_check_no_duplicate_keys_finds_dup():
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_CLOSE: 10.0},
        {COL_DATE: "2024-01-02", COL_CLOSE: 10.5},  # 重复
    ])
    findings = check_no_duplicate_keys(bars)
    assert len(findings) == 1
    assert findings[0].severity == "ERROR"
    assert findings[0].n_affected == 2


def test_check_price_positive_finds_zero():
    bars = _make_bars([{COL_DATE: "2024-01-02", COL_LOW: 0.0}])
    findings = check_price_positive(bars)
    assert any(f.scope == f"bars.{COL_LOW}" and f.severity == "ERROR" for f in findings)


def test_check_volume_non_negative_finds_negative():
    bars = _make_bars([{COL_DATE: "2024-01-02", COL_VOL: -1}])
    findings = check_volume_non_negative(bars)
    assert len(findings) == 1
    assert findings[0].severity == "ERROR"


def test_check_adj_factor_jump_clean():
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_ADJ_FACTOR: 1.0},
        {COL_DATE: "2024-01-03", COL_ADJ_FACTOR: 1.0},
    ])
    assert check_adj_factor_jump(bars) == []


def test_check_adj_factor_jump_warns_on_huge_change():
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_ADJ_FACTOR: 1.0},
        {COL_DATE: "2024-01-03", COL_ADJ_FACTOR: 2.0},  # +100%
    ])
    findings = check_adj_factor_jump(bars, max_change=0.5)
    assert len(findings) == 1
    assert findings[0].severity == "WARNING"


def test_check_no_data_before_listing_clean():
    bars = _make_bars([{COL_DATE: "2020-01-02", COL_CLOSE: 10.0}])
    basic = _make_basic([{COL_CODE: "600000", "list_date": pd.to_datetime("1999-11-10")}])
    assert check_no_data_before_listing(bars, basic) == []


def test_check_no_data_before_listing_finds_early():
    bars = _make_bars([{COL_DATE: "1990-01-02", COL_CLOSE: 10.0}])
    basic = _make_basic([{COL_CODE: "600000", "list_date": pd.to_datetime("1999-11-10")}])
    findings = check_no_data_before_listing(bars, basic)
    assert len(findings) == 1
    assert findings[0].severity == "ERROR"


def test_check_no_future_data_finds_future():
    bars = _make_bars([{COL_DATE: "2099-01-02", COL_CLOSE: 10.0}])
    findings = check_no_future_data(bars, asof_date=date(2024, 6, 1))
    assert len(findings) == 1
    assert findings[0].severity == "ERROR"


def test_check_no_future_data_clean_when_at_asof():
    bars = _make_bars([{COL_DATE: "2024-06-01", COL_CLOSE: 10.0}])
    assert check_no_future_data(bars, asof_date=date(2024, 6, 1)) == []


def test_check_limit_price_consistency_main_10pct():
    # 昨收 10.00 → 涨 10% = 11.00，跌 10% = 9.00
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_CLOSE: 10.00, COL_LIMIT_UP: pd.NA, COL_LIMIT_DOWN: pd.NA},
        {COL_DATE: "2024-01-03", COL_CLOSE: 10.50, COL_LIMIT_UP: 11.00, COL_LIMIT_DOWN: 9.00},
    ])
    assert check_limit_price_consistency(bars) == []


def test_check_limit_price_consistency_finds_off():
    # 应当是 11.00，但实际写了 12.00 → WARNING
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_CLOSE: 10.00, COL_LIMIT_UP: pd.NA, COL_LIMIT_DOWN: pd.NA},
        {COL_DATE: "2024-01-03", COL_CLOSE: 10.50, COL_LIMIT_UP: 12.00, COL_LIMIT_DOWN: 9.00},
    ])
    findings = check_limit_price_consistency(bars)
    assert len(findings) == 1
    assert findings[0].severity == "WARNING"


def test_check_calendar_continuity_clean():
    cal = _make_calendar(["2024-01-02", "2024-01-03", "2024-01-04"])
    assert check_calendar_continuity(cal) == []


def test_check_calendar_continuity_warns_on_huge_gap():
    cal = _make_calendar(["2024-01-02", "2024-02-20"])  # 49 日间隔
    findings = check_calendar_continuity(cal)
    assert any(f.severity == "WARNING" for f in findings)


# ===== DataValidator 聚合 =====
def test_validator_runs_all_and_writes_report(tmp_path):
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_CLOSE: 10.00, COL_LIMIT_UP: pd.NA, COL_LIMIT_DOWN: pd.NA},
        {COL_DATE: "2024-01-03", COL_CLOSE: 10.50, COL_LIMIT_UP: 11.00, COL_LIMIT_DOWN: 9.00},
    ])
    basic = _make_basic([{COL_CODE: "600000", "list_date": pd.to_datetime("1999-11-10")}])
    cal = _make_calendar(["2024-01-02", "2024-01-03"])

    report_path = tmp_path / "dqr.csv"
    v = DataValidator(bars, basic, cal, asof_date=date(2024, 1, 31), report_path=report_path)
    df = v.run_all()
    v.write_report()
    v.assert_clean()

    assert isinstance(df, pd.DataFrame)
    assert report_path.exists()
    # 干净数据：findings 应为空
    assert len(v.findings) == 0


def test_validator_raises_on_error(tmp_path):
    """含 ERROR 级 finding 时 assert_clean 必须抛。"""
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_CLOSE: -1.0},  # 价 < 0
    ])
    basic = _make_basic([{COL_CODE: "600000", "list_date": pd.to_datetime("1999-11-10")}])
    cal = _make_calendar(["2024-01-02"])

    v = DataValidator(bars, basic, cal, asof_date=date(2024, 1, 31), report_path=tmp_path / "dqr.csv")
    v.run_all()
    with pytest.raises(DataQualityError) as ei:
        v.assert_clean()
    msg = str(ei.value)
    assert "ERROR" in msg
    assert "price_positive" in msg


def test_validator_warning_does_not_raise(tmp_path):
    """WARNING 级不抛。"""
    bars = _make_bars([
        {COL_DATE: "2024-01-02", COL_CLOSE: 10.0, COL_ADJ_FACTOR: 1.0},
        {COL_DATE: "2024-01-03", COL_CLOSE: 10.5, COL_ADJ_FACTOR: 2.0,  # 跳变
         COL_LIMIT_UP: 11.55, COL_LIMIT_DOWN: 9.45},
    ])
    basic = _make_basic([{COL_CODE: "600000", "list_date": pd.to_datetime("1999-11-10")}])
    cal = _make_calendar(["2024-01-02", "2024-01-03"])

    v = DataValidator(bars, basic, cal, asof_date=date(2024, 1, 31), report_path=tmp_path / "dqr.csv")
    v.run_all()
    # 应当只有 WARNING，不抛
    v.assert_clean()
    # 检查 WARNING 写进了报告
    assert any(f.severity == "WARNING" for f in v.findings)


def test_validator_check_exception_recorded_as_error(tmp_path):
    """单个 check 抛异常：记为 ERROR，不让整个 validator 崩。"""
    bars = _make_bars([{COL_DATE: "2024-01-02", COL_CLOSE: 10.0}])
    basic = _make_basic([{COL_CODE: "600000", "list_date": pd.to_datetime("1999-11-10")}])
    cal = _make_calendar(["2024-01-02"])

    v = DataValidator(bars, basic, cal, asof_date=date(2024, 1, 31), report_path=tmp_path / "dqr.csv")

    def boom():
        raise ValueError("simulated check failure")

    v._run(boom)  # noqa: SLF001
    v.run_all()
    assert any(f.detail.startswith("check raised") for f in v.findings)


def test_validator_empty_bars_calendar_only():
    """bars 为空：大多数 check 不会报错；calendar 必须存在。"""
    from src.data.schema import make_empty_bars
    empty_bars = make_empty_bars()  # schema 合规的空表
    basic = _make_basic([{COL_CODE: "600000", "list_date": pd.to_datetime("1999-11-10")}])
    cal = _make_calendar(["2024-01-02"])
    v = DataValidator(empty_bars, basic, cal, asof_date=date(2024, 1, 31))
    df = v.run_all()
    # 没有 ERROR 才会通过
    v.assert_clean()
    assert isinstance(df, pd.DataFrame)
