"""分区行情回测：DuckDB 信号计算必须与内存回测保持同一因果结果。"""

from __future__ import annotations

import pandas as pd

from src.backtest.engine import run_backtest
from src.backtest.partitioned import prepare_partitioned_backtest, run_partitioned_backtest
from src.data.schema import (
    COL_ADJ_CLOSE,
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
    COL_SUSPENDED,
    COL_VOL,
)


def _panel(codes: list[str], n_days: int = 180) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2023-01-03", periods=n_days)
    rows = []
    for code_idx, code in enumerate(codes):
        for day_idx, day in enumerate(dates):
            close = 10.0 + code_idx + day_idx * (0.004 + code_idx * 0.0002)
            rows.append({
                COL_CODE: code,
                COL_DATE: day,
                COL_OPEN: close * 0.999,
                COL_HIGH: close * 1.005,
                COL_LOW: close * 0.995,
                COL_CLOSE: close,
                COL_ADJ_CLOSE: close,
                COL_VOL: 1_000_000,
                COL_AMOUNT: 200_000_000.0,
                COL_ADJ_FACTOR: 1.0,
                COL_SUSPENDED: False,
                COL_ST: False,
                COL_LIMIT_UP: round(close * 1.1, 2),
                COL_LIMIT_DOWN: round(close * 0.9, 2),
            })
    bars = pd.DataFrame(rows)
    basic = pd.DataFrame({
        COL_CODE: codes,
        "list_date": pd.to_datetime("2000-01-01"),
        "delist_date": pd.NaT,
    })
    calendar = pd.DataFrame({COL_DATE: dates, "is_trading_day": True})
    return bars, basic, calendar


def _write_partitioned(bars: pd.DataFrame, bars_dir) -> None:
    bars_dir.mkdir(parents=True)
    for code, group in bars.groupby(COL_CODE, sort=True):
        group.to_parquet(bars_dir / f"{code}.parquet", index=False)


def test_partitioned_backtest_matches_in_memory_engine(tmp_path):
    codes = [f"sh.6000{index:02d}" for index in range(6)]
    bars, basic, calendar = _panel(codes)
    bars_dir = tmp_path / "bars_by_code"
    _write_partitioned(bars, bars_dir)

    direct = run_backtest(bars, basic, calendar)
    partitioned = run_partitioned_backtest(bars_dir, basic, calendar)

    pd.testing.assert_frame_equal(direct["nav"], partitioned["nav"])
    assert [(order.code, order.side, order.status) for order in direct["orders"]] == [
        (order.code, order.side, order.status) for order in partitioned["orders"]
    ]
    assert partitioned["partitioned"]["selected_codes"] >= 5


def test_partitioned_prepare_keeps_only_selected_execution_rows(tmp_path):
    codes = [f"sh.6000{index:02d}" for index in range(12)]
    bars, basic, calendar = _panel(codes)
    bars_dir = tmp_path / "bars_by_code"
    _write_partitioned(bars, bars_dir)

    prepared = prepare_partitioned_backtest(bars_dir, basic, calendar)

    assert prepared.selected_codes < len(codes)
    assert set(prepared.bars.columns) == {
        COL_CODE, COL_DATE, COL_OPEN, COL_CLOSE, COL_LIMIT_UP, COL_LIMIT_DOWN, COL_SUSPENDED
    }
    assert len(prepared.bars) < len(bars)


def test_partitioned_execution_keeps_prior_prices_for_valuation(tmp_path):
    codes = [f"sh.6000{index:02d}" for index in range(12)]
    bars, basic, calendar = _panel(codes)
    bars_dir = tmp_path / "bars_by_code"
    _write_partitioned(bars, bars_dir)
    start = calendar[COL_DATE].iloc[80]

    prepared = prepare_partitioned_backtest(
        bars_dir,
        basic,
        calendar,
        start_date=str(start.date()),
        end_date=str(calendar[COL_DATE].iloc[120].date()),
    )

    assert not prepared.bars.empty
    assert prepared.bars[COL_DATE].min() < start
