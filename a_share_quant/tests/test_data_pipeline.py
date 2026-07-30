"""阶段 4 测试：download + clean 管道在 fixture 上端到端通过。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.cleaner import (
    clean_bars,
    clean_bars_partitioned,
    clean_stock_basic,
    clean_trade_calendar,
    run_all,
)
from src.data.schema import (
    BARS,
    COL_CODE,
    COL_DATE,
    COL_LIMIT_DOWN,
    COL_LIMIT_UP,
    COL_ST,
    COL_SUSPENDED,
)
from tests.fixtures.bars_fixture import build_fixture


@pytest.fixture(autouse=True)
def _isolate_data_dirs(tmp_path, monkeypatch):
    """把 DATA_RAW / DATA_PROCESSED 重定向到 tmp_path，不污染真实目录。"""
    raw = tmp_path / "raw"
    proc = tmp_path / "processed"
    raw.mkdir()
    (raw / "bars").mkdir()
    proc.mkdir()
    monkeypatch.setattr("src.config.DATA_RAW", raw)
    monkeypatch.setattr("src.config.DATA_PROCESSED", proc)
    monkeypatch.setattr("src.data.downloader.DATA_RAW", raw)
    monkeypatch.setattr("src.data.downloader.RAW_BARS_DIR", raw / "bars")
    monkeypatch.setattr("src.data.downloader.RAW_STOCK_BASIC", raw / "stock_basic.parquet")
    monkeypatch.setattr("src.data.downloader.RAW_TRADE_CALENDAR", raw / "trade_calendar.parquet")
    monkeypatch.setattr("src.data.cleaner.DATA_PROCESSED", proc)
    yield


def test_build_fixture_creates_files():
    from src.data import downloader
    build_fixture()
    # 3 只股票 + stock_basic + trade_calendar
    assert (downloader.RAW_BARS_DIR / "600000.parquet").exists()
    assert (downloader.RAW_BARS_DIR / "688001.parquet").exists()
    assert (downloader.RAW_BARS_DIR / "000001.parquet").exists()
    assert downloader.RAW_STOCK_BASIC.exists()
    assert downloader.RAW_TRADE_CALENDAR.exists()


def test_normalize_baostock_tradestatus_column():
    """真实 baostock 原始列名 `tradestatus` 必须正确映射为停牌状态。"""
    from src.data.downloader import _normalize_bar_frame

    raw = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "open": [10.0, 10.1], "high": [10.2, 10.3],
        "low": [9.9, 10.0], "close": [10.1, 10.2],
        "volume": [1000, 1000], "amount": [10000, 10000],
        "adjustflag": ["2", "2"], "tradestatus": ["0", "1"],
        "isST": ["0", "1"],
    })

    normalized = _normalize_bar_frame(raw, code="600000")

    BARS.validate(normalized)
    assert normalized[COL_SUSPENDED].tolist() == [True, False]
    assert normalized[COL_ST].tolist() == [False, True]


def test_download_bars_all_reuses_one_baostock_session(monkeypatch):
    """批量下载只登录一次，避免全市场下载被逐股登录放大。"""
    from src.data import downloader

    client = object()
    seen_clients = []
    monkeypatch.setattr(downloader, "_bs_login", lambda: client)
    monkeypatch.setattr(downloader, "_bs_logout", lambda value: seen_clients.append(value))
    monkeypatch.setattr(
        downloader,
        "download_bars_for_code",
        lambda code, dr, *, client: seen_clients.append(client) or pd.DataFrame(),
    )

    downloader.download_bars_all(["sh.600000", "sh.600004"], downloader.DownloadRange("2025-01-01", "2025-01-31"), sleep=0)

    assert seen_clients == [client, client, client]


def test_download_bars_all_can_stream_rows_without_collecting_frames(monkeypatch):
    """全市场下载仅需写分股文件，不能在内存里累计所有 bars。"""
    from src.data import downloader

    client = object()
    monkeypatch.setattr(downloader, "_bs_login", lambda: client)
    monkeypatch.setattr(downloader, "_bs_logout", lambda _: None)
    monkeypatch.setattr(
        downloader,
        "download_bars_for_code",
        lambda code, dr, *, client: pd.DataFrame({"code": [code, code]}),
    )

    rows = downloader.download_bars_all(
        ["sh.600000", "sh.600004"],
        downloader.DownloadRange("2025-01-01", "2025-01-31"),
        sleep=0,
        collect=False,
    )

    assert rows == 4


def test_select_active_equity_codes_excludes_indices_and_inactive_symbols():
    from src.data.downloader import select_active_equity_codes

    basic = pd.DataFrame({
        "code": ["sh.600000", "sh.000001", "sh.600001"],
        "type": ["1", "2", "1"],
        "status": ["1", "1", "0"],
    })

    assert select_active_equity_codes(basic) == ["sh.600000"]


def test_select_equity_codes_can_include_inactive_history():
    from src.data.downloader import select_equity_codes

    basic = pd.DataFrame({
        "code": ["sh.600000", "sh.000001", "sh.600001"],
        "type": ["1", "2", "1"],
        "status": ["1", "1", "0"],
    })

    assert select_equity_codes(basic, include_inactive=True) == ["sh.600000", "sh.600001"]


def test_clean_bars_schema_and_count():
    build_fixture()
    df = clean_bars()
    BARS.validate(df)
    # 3 股 × 30 日 = 90 行
    assert len(df) == 90
    assert set(df[COL_CODE].unique()) == {"600000", "688001", "000001"}


def test_clean_bars_partitioned_keeps_each_code_independent():
    from src.data.cleaner import PROC_BARS_BY_CODE

    build_fixture()
    summary = clean_bars_partitioned()
    out_dir = PROC_BARS_BY_CODE()
    files = sorted(out_dir.glob("*.parquet"))

    assert summary == {"files": 3, "rows": 90, "skipped": 0}
    assert len(files) == 3
    for path in files:
        BARS.validate(pd.read_parquet(path))


def test_clean_bars_limit_prices_main_board():
    """主板 10% 涨跌停：600000 第二个交易日 limit_up 应 = close[0] × 1.10。"""
    build_fixture()
    df = clean_bars()
    s = df[df[COL_CODE] == "600000"].sort_values(COL_DATE).reset_index(drop=True)
    prev = float(s.loc[0, "close"])
    expected_up = round(prev * 1.10, 2)
    expected_dn = round(prev * 0.90, 2)
    assert float(s.loc[1, COL_LIMIT_UP]) == expected_up
    assert float(s.loc[1, COL_LIMIT_DOWN]) == expected_dn


def test_clean_bars_limit_prices_kechuangban():
    """科创板 20% 涨跌停：688001。"""
    build_fixture()
    df = clean_bars()
    s = df[df[COL_CODE] == "688001"].sort_values(COL_DATE).reset_index(drop=True)
    prev = float(s.loc[0, "close"])
    assert float(s.loc[1, COL_LIMIT_UP]) == round(prev * 1.20, 2)
    assert float(s.loc[1, COL_LIMIT_DOWN]) == round(prev * 0.80, 2)


def test_clean_bars_limit_prices_st():
    """ST 5% 涨跌停：000001。"""
    build_fixture()
    df = clean_bars()
    s = df[df[COL_CODE] == "000001"].sort_values(COL_DATE).reset_index(drop=True)
    prev = float(s.loc[0, "close"])
    assert float(s.loc[1, COL_LIMIT_UP]) == round(prev * 1.05, 2)
    assert float(s.loc[1, COL_LIMIT_DOWN]) == round(prev * 0.95, 2)


def test_clean_bars_first_row_limit_is_nan():
    """首日没有昨收 → limit_up/down 必须是 NaN。"""
    build_fixture()
    df = clean_bars()
    for code in ("600000", "688001", "000001"):
        first = df[df[COL_CODE] == code].sort_values(COL_DATE).iloc[0]
        assert pd.isna(first[COL_LIMIT_UP])
        assert pd.isna(first[COL_LIMIT_DOWN])


def test_clean_bars_dedup():
    """重复的 (code, date) 应当只保留最后一条。"""
    from src.data import downloader
    build_fixture()
    # 手动追加一个重复行
    extra_path = downloader.RAW_BARS_DIR / "600000.parquet"
    existing = pd.read_parquet(extra_path)
    dup = existing.iloc[[-1]].copy()  # 完全复制最后一行
    pd.concat([existing, dup], ignore_index=True).to_parquet(extra_path, index=False)
    df = clean_bars()
    code_df = df[df[COL_CODE] == "600000"]
    # 不应出现 31 行；应是 30
    assert len(code_df) == 30


def test_clean_stock_basic_dates_typed():
    build_fixture()
    df = clean_stock_basic()
    assert len(df) == 3
    assert pd.api.types.is_datetime64_any_dtype(df["list_date"])
    # delist_date 是 NA（未退市）
    assert df["delist_date"].isna().all()


def test_clean_trade_calendar_sorted_unique():
    build_fixture()
    df = clean_trade_calendar()
    assert pd.api.types.is_datetime64_any_dtype(df[COL_DATE])
    assert df[COL_DATE].is_monotonic_increasing
    assert df[COL_DATE].is_unique


def test_run_all_idempotent():
    """同一个 raw 跑两次 clean，得到结果完全一致。"""
    build_fixture()
    a = run_all()
    b = run_all()
    for key in a:
        pd.testing.assert_frame_equal(
            a[key].reset_index(drop=True),
            b[key].reset_index(drop=True),
        )


def test_clean_bars_handles_empty_raw(tmp_path, monkeypatch):
    """raw/bars 目录存在但没有 parquet → 输出 schema 合规的空表，不报错。"""
    empty_raw = tmp_path / "empty_raw"
    (empty_raw / "bars").mkdir(parents=True)
    proc = tmp_path / "empty_proc"
    proc.mkdir()
    monkeypatch.setattr("src.data.downloader.RAW_BARS_DIR", empty_raw / "bars")
    monkeypatch.setattr("src.data.cleaner.DATA_PROCESSED", proc)
    # 还得有 stock_basic + trade_calendar，否则 cleaner 也会报错
    # 这里只测 clean_bars 的空 raw 处理
    from src.data.cleaner import clean_bars
    df = clean_bars()
    assert len(df) == 0
    BARS.validate(df)
