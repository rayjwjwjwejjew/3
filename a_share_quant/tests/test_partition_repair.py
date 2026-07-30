"""损坏的 processed 分区必须只从对应 raw 分区定点重建。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.cleaner import repair_partitioned_bars
from src.data.schema import COL_CODE, COL_DATE


def test_repair_partitioned_bars_uses_only_reported_code(tmp_path, monkeypatch):
    raw = tmp_path / "raw" / "bars"
    processed = tmp_path / "processed"
    output = processed / "bars_by_code"
    raw.mkdir(parents=True)
    output.mkdir(parents=True)
    source = pd.DataFrame({
        COL_CODE: ["sh.600000", "sh.600000"],
        COL_DATE: pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [10.0, 10.1], "high": [10.1, 10.2], "low": [9.9, 10.0],
        "close": [10.0, 10.1], "adj_close": [10.0, 10.1], "volume": [1, 1],
        "amount": [100.0, 100.0], "adj_factor": [1.0, 1.0],
        "is_suspended": [False, False], "is_st": [False, False],
    })
    source.to_parquet(raw / "sh.600000.parquet", index=False)
    (output / "sh.600000.parquet").write_bytes(b"broken")
    report = tmp_path / "quality.csv"
    pd.DataFrame([{
        "check": "partition_read", "severity": "ERROR", "scope": str(output / "sh.600000.parquet"),
    }]).to_csv(report, index=False)
    monkeypatch.setattr("src.data.downloader.RAW_BARS_DIR", raw)
    monkeypatch.setattr("src.data.cleaner.DATA_PROCESSED", processed)

    repaired = repair_partitioned_bars(report)

    assert repaired["files"] == 1
    assert len(pd.read_parquet(output / "sh.600000.parquet")) == 2


def test_repair_partitioned_bars_fails_when_raw_source_is_unreadable(tmp_path, monkeypatch):
    raw = tmp_path / "raw" / "bars"
    processed = tmp_path / "processed"
    output = processed / "bars_by_code"
    raw.mkdir(parents=True)
    output.mkdir(parents=True)
    (raw / "sh.600000.parquet").write_bytes(b"broken")
    (output / "sh.600000.parquet").write_bytes(b"broken")
    report = tmp_path / "quality.csv"
    pd.DataFrame([{
        "check": "partition_read", "severity": "ERROR", "scope": str(output / "sh.600000.parquet"),
    }]).to_csv(report, index=False)
    monkeypatch.setattr("src.data.downloader.RAW_BARS_DIR", raw)
    monkeypatch.setattr("src.data.cleaner.DATA_PROCESSED", processed)

    with pytest.raises(RuntimeError, match="partition repair incomplete"):
        repair_partitioned_bars(report)
