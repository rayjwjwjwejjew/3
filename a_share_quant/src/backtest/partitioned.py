"""低内存的分区行情回测入口。

全市场行情按股票分区保存时，不能把全部 1,500 万行拼进 Pandas。这里先用
DuckDB 在磁盘上计算每个调仓日的合规动量候选，只保留 top-K；再只加载这些
曾入选股票的执行/估值列，交给既有撮合引擎。窗口函数只使用当前及历史行，
不读取信号日之后的价格。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.backtest.engine import run_backtest
from src.config import load_config
from src.data.schema import (
    COL_AMOUNT,
    COL_CLOSE,
    COL_CODE,
    COL_DATE,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_OPEN,
    COL_SUSPENDED,
)


@dataclass(frozen=True)
class PreparedPartitionedBacktest:
    """DuckDB 预计算后的最小执行面板与调仓目标。"""

    bars: pd.DataFrame
    trade_calendar: pd.DataFrame
    target_weights: dict[pd.Timestamp, dict[str, float]]
    rebalance_dates: int
    selected_codes: int


def _connect() -> duckdb.DuckDBPyConnection:
    """限制本机 DuckDB 内存，避免与 Streamlit/Pandas 争抢 8GB 内存。"""
    return duckdb.connect(database=":memory:", config={"threads": "2", "memory_limit": "2GB"})


def _trading_days(
    trade_calendar: pd.DataFrame,
    *,
    start_date: str | None,
    end_date: str | None,
) -> pd.DataFrame:
    calendar = trade_calendar.copy()
    calendar[COL_DATE] = pd.to_datetime(calendar[COL_DATE])
    calendar = calendar[calendar["is_trading_day"].astype(bool)]
    if start_date:
        calendar = calendar[calendar[COL_DATE] >= pd.Timestamp(start_date)]
    if end_date:
        calendar = calendar[calendar[COL_DATE] <= pd.Timestamp(end_date)]
    calendar = calendar[[COL_DATE, "is_trading_day"]].drop_duplicates().sort_values(COL_DATE).reset_index(drop=True)
    if calendar.empty:
        raise ValueError("no trading days remain after date filter")
    return calendar


def _rebalance_dates(calendar: pd.DataFrame, rebalance_every: int) -> pd.DataFrame:
    dates = pd.to_datetime(calendar[COL_DATE]).reset_index(drop=True)
    return pd.DataFrame({COL_DATE: dates.iloc[::rebalance_every].tolist()})


def _normalise_stock_basic(stock_basic: pd.DataFrame) -> pd.DataFrame:
    required = {COL_CODE, "list_date", "delist_date"}
    missing = required - set(stock_basic.columns)
    if missing:
        raise ValueError(f"stock_basic missing columns: {sorted(missing)}")
    basic = stock_basic[[COL_CODE, "list_date", "delist_date"]].copy()
    basic[COL_CODE] = basic[COL_CODE].astype(str)
    basic["list_date"] = pd.to_datetime(basic["list_date"], errors="coerce")
    basic["delist_date"] = pd.to_datetime(basic["delist_date"], errors="coerce")
    return basic.drop_duplicates(subset=[COL_CODE], keep="last")


def _query_target_weights(
    con: duckdb.DuckDBPyConnection,
    *,
    bars_glob: str,
    stock_basic: pd.DataFrame,
    rebalance_dates: pd.DataFrame,
    lookback: int,
    skip: int,
    top_k: int,
) -> dict[pd.Timestamp, dict[str, float]]:
    """用历史窗口计算每个调仓日 top-K，不把候选全表传回 Pandas。"""
    cfg = load_config()
    basic = _normalise_stock_basic(stock_basic)
    con.register("stock_basic_input", basic)
    con.register("rebalance_dates_input", rebalance_dates)

    history_rows = max(lookback, 120)
    sql = f"""
        WITH feature_panel AS (
            SELECT
                {COL_CODE}, {COL_DATE}, adj_close, {COL_AMOUNT}, close, is_st, is_suspended,
                lag(adj_close, {skip}) OVER stock_window AS recent_adj_close,
                lag(adj_close, {lookback}) OVER stock_window AS old_adj_close,
                avg({COL_AMOUNT}) OVER (
                    PARTITION BY {COL_CODE} ORDER BY {COL_DATE}
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS avg_amount_20d,
                count(close) OVER (
                    PARTITION BY {COL_CODE} ORDER BY {COL_DATE}
                    ROWS BETWEEN {history_rows - 1} PRECEDING AND CURRENT ROW
                ) AS valid_close_count
            FROM read_parquet(?)
            WINDOW stock_window AS (PARTITION BY {COL_CODE} ORDER BY {COL_DATE})
        ),
        eligible AS (
            SELECT
                panel.{COL_DATE} AS date,
                panel.{COL_CODE} AS code,
                count(*) OVER (PARTITION BY panel.{COL_DATE}) AS candidate_count,
                row_number() OVER (
                    PARTITION BY panel.{COL_DATE}
                    ORDER BY (panel.recent_adj_close / panel.old_adj_close - 1.0) DESC, panel.{COL_CODE} ASC
                ) AS candidate_rank
            FROM feature_panel AS panel
            INNER JOIN rebalance_dates_input AS rebalance
                ON panel.{COL_DATE} = rebalance.{COL_DATE}
            INNER JOIN stock_basic_input AS basic
                ON panel.{COL_CODE} = basic.{COL_CODE}
            WHERE basic.list_date IS NOT NULL
              AND basic.list_date <= panel.{COL_DATE} - (? * INTERVAL '1 day')
              AND (basic.delist_date IS NULL OR basic.delist_date > panel.{COL_DATE})
              AND coalesce(panel.is_st, false) = false
              AND coalesce(panel.is_suspended, false) = false
              AND panel.avg_amount_20d >= ?
              AND panel.valid_close_count >= ?
              AND panel.recent_adj_close IS NOT NULL
              AND panel.old_adj_close IS NOT NULL
              AND panel.old_adj_close > 0
        )
        SELECT date, code, candidate_count, candidate_rank
        FROM eligible
        WHERE candidate_rank <= ?
        ORDER BY date, candidate_rank
    """
    ranked = con.execute(
        sql,
        [bars_glob, cfg.universe.min_listing_days, cfg.universe.min_avg_amount_20d, history_rows, top_k],
    ).fetchdf()

    targets = {pd.Timestamp(day): {} for day in rebalance_dates[COL_DATE]}
    if ranked.empty:
        return targets
    ranked["date"] = pd.to_datetime(ranked["date"])
    for day, group in ranked.groupby("date", sort=False):
        candidate_count = int(group["candidate_count"].iloc[0])
        if candidate_count < cfg.signal.min_holdings:
            continue
        codes = group["code"].astype(str).tolist()
        targets[pd.Timestamp(day)] = {code: 1.0 / len(codes) for code in codes}
    return targets


def _query_execution_bars(
    con: duckdb.DuckDBPyConnection,
    *,
    bars_glob: str,
    target_weights: dict[pd.Timestamp, dict[str, float]],
    calendar: pd.DataFrame,
    valuation_start: pd.Timestamp,
) -> pd.DataFrame:
    codes = sorted({code for weights in target_weights.values() for code in weights})
    columns = [COL_CODE, COL_DATE, COL_OPEN, COL_CLOSE, COL_LIMIT_UP, COL_LIMIT_DOWN, COL_SUSPENDED]
    if not codes:
        return pd.DataFrame(columns=columns)
    con.register("selected_codes_input", pd.DataFrame({COL_CODE: codes}))
    rows = con.execute(
        f"""
        SELECT bars.{COL_CODE}, bars.{COL_DATE}, bars.{COL_OPEN}, bars.{COL_CLOSE},
               bars.{COL_LIMIT_UP}, bars.{COL_LIMIT_DOWN}, bars.{COL_SUSPENDED}
        FROM read_parquet(?) AS bars
        INNER JOIN selected_codes_input AS selected USING ({COL_CODE})
        WHERE bars.{COL_DATE} >= ? AND bars.{COL_DATE} <= ?
        ORDER BY bars.{COL_CODE}, bars.{COL_DATE}
        """,
        [bars_glob, valuation_start, calendar[COL_DATE].max()],
    ).fetchdf()
    rows[COL_DATE] = pd.to_datetime(rows[COL_DATE])
    return rows


def prepare_partitioned_backtest(
    bars_dir: Path,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    *,
    rebalance_every: int | None = None,
    lookback: int | None = None,
    skip: int | None = None,
    top_k: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> PreparedPartitionedBacktest:
    """把分区行情缩减为本次回测所需的信号和执行面板。"""
    bars_dir = Path(bars_dir)
    if not bars_dir.is_dir() or not any(bars_dir.glob("*.parquet")):
        raise FileNotFoundError(f"partitioned bars missing: {bars_dir}")
    cfg = load_config()
    rebalance_every = rebalance_every if rebalance_every is not None else cfg.signal.rebalance_every
    lookback = lookback if lookback is not None else cfg.factor.lookback
    skip = skip if skip is not None else cfg.factor.skip
    top_k = top_k if top_k is not None else cfg.signal.top_k
    if rebalance_every <= 0:
        raise ValueError("rebalance_every must be positive")
    if lookback <= skip:
        raise ValueError("lookback must be greater than skip")

    all_calendar = _trading_days(trade_calendar, start_date=None, end_date=end_date)
    calendar = _trading_days(trade_calendar, start_date=start_date, end_date=end_date)
    first_day = calendar[COL_DATE].min()
    prior_days = all_calendar.loc[all_calendar[COL_DATE] < first_day, COL_DATE].tail(skip)
    valuation_start = pd.Timestamp(prior_days.min()) if not prior_days.empty else first_day
    rebalances = _rebalance_dates(calendar, rebalance_every)
    bars_glob = str((bars_dir / "*.parquet").resolve())
    con = _connect()
    try:
        targets = _query_target_weights(
            con,
            bars_glob=bars_glob,
            stock_basic=stock_basic,
            rebalance_dates=rebalances,
            lookback=lookback,
            skip=skip,
            top_k=top_k,
        )
        execution_bars = _query_execution_bars(
            con,
            bars_glob=bars_glob,
            target_weights=targets,
            calendar=calendar,
            valuation_start=valuation_start,
        )
    finally:
        con.close()
    return PreparedPartitionedBacktest(
        bars=execution_bars,
        trade_calendar=calendar,
        target_weights=targets,
        rebalance_dates=len(rebalances),
        selected_codes=len({code for weights in targets.values() for code in weights}),
    )


def run_partitioned_backtest(
    bars_dir: Path,
    stock_basic: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    initial_cash: float = 1_000_000.0,
    rebalance_every: int | None = None,
    lookback: int | None = None,
    skip: int | None = None,
    top_k: int | None = None,
    cost_multiplier: float = 1.0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """在分区行情上运行与 ``run_backtest`` 同一撮合/成本逻辑的回测。"""
    prepared = prepare_partitioned_backtest(
        bars_dir,
        stock_basic,
        trade_calendar,
        rebalance_every=rebalance_every,
        lookback=lookback,
        skip=skip,
        top_k=top_k,
        start_date=start_date,
        end_date=end_date,
    )
    result = run_backtest(
        prepared.bars,
        stock_basic,
        prepared.trade_calendar,
        initial_cash=initial_cash,
        rebalance_every=rebalance_every,
        lookback=lookback,
        skip=skip,
        top_k=top_k,
        cost_multiplier=cost_multiplier,
        precomputed_target_weights=prepared.target_weights,
    )
    result["partitioned"] = {
        "rebalance_dates": prepared.rebalance_dates,
        "selected_codes": prepared.selected_codes,
        "execution_rows": len(prepared.bars),
    }
    return result
