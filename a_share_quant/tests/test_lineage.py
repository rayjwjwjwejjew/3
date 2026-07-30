"""数据快照与运行清单必须可复现、可发现篡改、且保持研究边界。"""

from __future__ import annotations

import json

import pandas as pd

from src.research.lineage import (
    RESEARCH_STATUS,
    build_data_snapshot,
    verify_data_snapshot,
    write_data_snapshot,
    write_run_manifest,
)


def _write_processed(root, *, close: float = 10.0, partitioned: bool = False) -> None:
    bars = pd.DataFrame({
        "code": ["600000", "600000"],
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "close": [close, close + 0.1],
    })
    if partitioned:
        target = root / "bars_by_code"
        target.mkdir(parents=True, exist_ok=True)
        bars.to_parquet(target / "600000.parquet", index=False)
    else:
        root.mkdir(parents=True, exist_ok=True)
        bars.to_parquet(root / "bars.parquet", index=False)
    pd.DataFrame({"code": ["600000"]}).to_parquet(root / "stock_basic.parquet", index=False)
    pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-01-03"])}).to_parquet(
        root / "trade_calendar.parquet", index=False
    )


def test_snapshot_is_content_addressed_and_records_coverage(tmp_path):
    processed = tmp_path / "processed"
    _write_processed(processed, partitioned=True)

    snapshot = build_data_snapshot(processed_root=processed)

    assert snapshot["layout"] == "partitioned_by_code"
    assert snapshot["coverage"]["calendar_start"] == "2024-01-02"
    assert snapshot["coverage"]["calendar_end"] == "2024-01-03"
    assert snapshot["snapshot_id"].startswith("data-")
    assert len(snapshot["files"]) == 3


def test_snapshot_can_require_monolithic_layout_when_both_layouts_exist(tmp_path):
    processed = tmp_path / "processed"
    _write_processed(processed)
    _write_processed(processed, partitioned=True)

    snapshot = build_data_snapshot(processed_root=processed, layout="monolithic")

    assert snapshot["layout"] == "monolithic"
    assert {item["path"] for item in snapshot["files"]} == {
        "bars.parquet", "stock_basic.parquet", "trade_calendar.parquet"
    }


def test_snapshot_write_reuses_same_content_and_verify_detects_change(tmp_path):
    processed = tmp_path / "processed"
    _write_processed(processed)
    snapshot, path = write_data_snapshot(output_dir=tmp_path / "metadata", processed_root=processed)
    repeated, repeated_path = write_data_snapshot(output_dir=tmp_path / "metadata", processed_root=processed)

    assert repeated_path == path
    assert repeated["snapshot_id"] == snapshot["snapshot_id"]
    assert verify_data_snapshot(path, processed_root=processed) == (True, f"verified {snapshot['snapshot_id']}")

    _write_processed(processed, close=99.0)
    verified, message = verify_data_snapshot(path, processed_root=processed)
    assert not verified
    assert "content digest mismatch" in message


def test_run_manifest_records_research_only_lineage(tmp_path):
    processed = tmp_path / "processed"
    _write_processed(processed)
    snapshot, snapshot_path = write_data_snapshot(output_dir=tmp_path / "metadata", processed_root=processed)
    config = tmp_path / "strategy.yaml"
    config.write_text("factor: momentum\n", encoding="utf-8")
    project = tmp_path / "not-a-git-project"
    project.mkdir()

    manifest, path = write_run_manifest(
        output_dir=tmp_path / "manifests",
        experiment_type="backtest",
        snapshot=snapshot,
        arguments={"initial_cash": 1_000_000},
        metrics={"total_return": -0.1},
        artifacts={"data_snapshot": str(snapshot_path)},
        config_path=config,
        project_root=project,
    )

    assert path.exists()
    assert manifest["research_status"] == RESEARCH_STATUS
    assert manifest["order_authority"] == "NONE"
    assert manifest["live_trading_locked"] is True
    assert manifest["snapshot_id"] == snapshot["snapshot_id"]
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == manifest["run_id"]
