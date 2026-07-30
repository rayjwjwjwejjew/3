"""数据快照与研究运行清单。

行情文件不进入 Git；本模块用逐文件 SHA-256 冻结 ``processed/`` 数据集，
并让每个实验记录它所使用的数据、代码、配置、参数与指标。它不负责下载、
下单或策略晋级：所有输出始终是 ``RESEARCH_ONLY`` 研究证据。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import CONFIG_PATH, DATA_PROCESSED, PROJECT_ROOT


SNAPSHOT_SCHEMA_VERSION = 1
RUN_MANIFEST_SCHEMA_VERSION = 1
RESEARCH_STATUS = "RESEARCH_ONLY"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)
    return path


def _git_commit(project_root: Path = PROJECT_ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNAVAILABLE"
    return result.stdout.strip() or "UNAVAILABLE"


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _processed_paths(
    processed_root: Path,
    *,
    layout: str | None = None,
) -> tuple[str, list[Path]]:
    partitioned = processed_root / "bars_by_code"
    monolithic = processed_root / "bars.parquet"
    if layout not in (None, "partitioned_by_code", "monolithic"):
        raise ValueError(f"unsupported data layout: {layout}")
    if layout != "monolithic" and partitioned.exists():
        bars = sorted(partitioned.rglob("*.parquet"))
        if not bars:
            raise FileNotFoundError(f"no parquet files in partitioned bars: {partitioned}")
        layout = "partitioned_by_code"
    elif layout != "partitioned_by_code" and monolithic.exists():
        bars = [monolithic]
        layout = "monolithic"
    else:
        raise FileNotFoundError(
            f"processed bars missing: expected {partitioned} or {monolithic}"
        )

    required = [processed_root / "stock_basic.parquet", processed_root / "trade_calendar.parquet"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"processed metadata missing: {', '.join(missing)}")
    return layout, [*bars, *required]


def build_data_snapshot(
    *,
    processed_root: Path = DATA_PROCESSED,
    source: str = "baostock",
    layout: str | None = None,
) -> dict[str, Any]:
    """构建数据内容快照，不写文件。

    快照按内容哈希，而不是 mtime：任一被纳入文件的字节变化都会改变
    ``snapshot_id``。日期覆盖范围来自交易日历，股票数/行数只作描述统计。
    """
    processed_root = Path(processed_root)
    resolved_layout, paths = _processed_paths(processed_root, layout=layout)
    files: list[dict[str, Any]] = []
    for path in paths:
        files.append(
            {
                "path": _relative(path, processed_root),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )

    calendar_path = processed_root / "trade_calendar.parquet"
    calendar = pd.read_parquet(calendar_path, columns=["date"])
    if calendar.empty:
        raise ValueError(f"trade calendar is empty: {calendar_path}")
    dates = pd.to_datetime(calendar["date"], errors="raise")
    stock_basic = pd.read_parquet(processed_root / "stock_basic.parquet", columns=["code"])

    content = {
        "layout": resolved_layout,
        "files": files,
        "source": source,
    }
    content_sha256 = _sha256_bytes(_canonical_json(content).encode("utf-8"))
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": f"data-{content_sha256[:16]}",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": source,
        "processed_root": str(processed_root.resolve()),
        "layout": resolved_layout,
        "coverage": {
            "calendar_start": str(dates.min().date()),
            "calendar_end": str(dates.max().date()),
            "calendar_rows": int(len(calendar)),
            "stock_basic_rows": int(len(stock_basic)),
        },
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
        "content_sha256": content_sha256,
    }


def write_data_snapshot(
    *,
    output_dir: Path,
    processed_root: Path = DATA_PROCESSED,
    source: str = "baostock",
    layout: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """写入不可变快照；同内容快照已存在时复用而不覆盖。"""
    snapshot = build_data_snapshot(processed_root=processed_root, source=source, layout=layout)
    path = Path(output_dir) / f"{snapshot['snapshot_id']}.json"
    if path.exists():
        existing = load_data_snapshot(path)
        if existing.get("content_sha256") != snapshot["content_sha256"]:
            raise ValueError(f"snapshot id collision at {path}")
        return existing, path
    return snapshot, _write_json_atomic(path, snapshot)


def load_data_snapshot(path: Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        snapshot = json.load(handle)
    if not isinstance(snapshot, dict):
        raise ValueError(f"snapshot must be a JSON object: {path}")
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"unsupported snapshot schema: {snapshot.get('schema_version')}")
    if not snapshot.get("snapshot_id") or not snapshot.get("content_sha256"):
        raise ValueError(f"snapshot missing id or content digest: {path}")
    return snapshot


def verify_data_snapshot(snapshot_path: Path, *, processed_root: Path = DATA_PROCESSED) -> tuple[bool, str]:
    """重新哈希当前 processed 数据并比对一个快照。"""
    expected = load_data_snapshot(snapshot_path)
    actual = build_data_snapshot(
        processed_root=processed_root,
        source=str(expected.get("source", "unknown")),
        layout=str(expected.get("layout", "")),
    )
    if actual["content_sha256"] != expected["content_sha256"]:
        return False, (
            f"content digest mismatch: expected {expected['content_sha256']}, "
            f"got {actual['content_sha256']}"
        )
    return True, f"verified {expected['snapshot_id']}"


def config_sha256(config_path: Path = CONFIG_PATH) -> str:
    return _sha256_file(Path(config_path))


def write_run_manifest(
    *,
    output_dir: Path,
    experiment_type: str,
    snapshot: dict[str, Any],
    arguments: dict[str, Any],
    metrics: dict[str, Any],
    artifacts: dict[str, str],
    config_path: Path = CONFIG_PATH,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], Path]:
    """记录研究运行；结果默认且强制标记为非实盘研究。"""
    core = {
        "experiment_type": experiment_type,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_content_sha256": snapshot["content_sha256"],
        "code_commit": _git_commit(project_root),
        "config_sha256": config_sha256(config_path),
        "arguments": arguments,
        "metrics": metrics,
        "artifacts": artifacts,
    }
    run_id = f"run-{_sha256_bytes(_canonical_json(core).encode('utf-8'))[:16]}"
    manifest = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "research_status": RESEARCH_STATUS,
        "order_authority": "NONE",
        "live_trading_locked": True,
        **core,
    }
    path = Path(output_dir) / f"{run_id}.json"
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("run_id") != run_id:
            raise ValueError(f"run manifest id collision at {path}")
        return existing, path
    return manifest, _write_json_atomic(path, manifest)


def default_snapshot_dir() -> Path:
    return DATA_PROCESSED.parent / "metadata"


def default_run_manifest_dir() -> Path:
    return PROJECT_ROOT / "results" / "run_manifests"
