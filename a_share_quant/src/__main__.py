"""V1 CLI 入口。

子命令：
- (无)        阶段 3 的 dry run，输出配置摘要
- download    阶段 4，从 baostock 拉原始数据
- clean       阶段 4，raw → processed
- all         阶段 4，download + clean

每个子命令在缺少网络或 raw 数据时给出友好提示，并保留一个
`--self-test` 模式：用 fixture 数据走完整管道，不联网。
"""

from __future__ import annotations

import argparse
import logging
import sys

from src import __version__
from src.config import CONFIG_PATH, load_config
from src.data import BARS, make_empty_bars


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _cmd_dry(_args: argparse.Namespace) -> int:
    cfg = load_config()
    print(f"a_share_quant V{__version__}")
    print(f"config: {CONFIG_PATH}")
    print(f"universe.start_date   = {cfg.universe.start_date}")
    print(f"universe.min_amount   = {cfg.universe.min_avg_amount_20d:,.0f}")
    print(f"factor.name           = {cfg.factor.name} (lookback={cfg.factor.lookback}, skip={cfg.factor.skip})")
    print(f"signal.rebalance      = every {cfg.signal.rebalance_every} trading days")
    print(f"signal.top_k          = {cfg.signal.top_k}")
    print(f"execution.price_basis = {cfg.execution.price_basis}")
    print(f"risk.TRADING_ENABLED  = {cfg.risk.trading_enabled}")
    empty = make_empty_bars()
    BARS.validate(empty)
    print(f"schema: {len(BARS.required)} required columns OK")
    print("V1 phase-3 dry run: all imports and config loading succeeded.")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    """阶段 4 download 子命令。需联网；沙箱内会失败。"""
    from src.data.downloader import (
        download_bars_all,
        download_stock_basic,
        download_trade_calendar,
        DownloadRange,
        select_active_equity_codes,
        select_equity_codes,
    )

    cfg = load_config()
    if args.end_date is None:
        from datetime import date
        end = date.today().isoformat()
    else:
        end = args.end_date
    dr = DownloadRange(start_date=args.start_date or cfg.universe.start_date, end_date=end)

    print("downloading stock_basic...")
    sb = download_stock_basic()
    print(f"  -> {len(sb)} stocks")

    print(f"downloading trade_calendar {dr.start_date} -> {dr.end_date}...")
    tc = download_trade_calendar(dr.start_date, dr.end_date)
    print(f"  -> {len(tc)} calendar days")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.include_inactive:
        codes = select_equity_codes(sb, include_inactive=True)
    else:
        codes = select_active_equity_codes(sb)
    print(f"downloading bars for {len(codes)} codes...")
    rows = download_bars_all(codes, dr, collect=False)
    print(f"  -> {rows} bar rows")
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    """阶段 4 clean 子命令。raw → processed。"""
    from src.data.cleaner import clean_bars_partitioned, clean_stock_basic, clean_trade_calendar, run_all
    if args.partitioned:
        bars = clean_bars_partitioned()
        stock_basic = clean_stock_basic()
        calendar = clean_trade_calendar()
        print(f"  bars_by_code: {bars['rows']} rows / {bars['files']} files (skipped={bars['skipped']})")
        print(f"  stock_basic: {len(stock_basic)} rows")
        print(f"  trade_calendar: {len(calendar)} rows")
        return 0
    out = run_all()
    for k, v in out.items():
        print(f"  {k}: {len(v)} rows")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """阶段 5：对 processed/ 跑 8 项质量检查，生成报告 + assert_clean。"""
    from src.data.validator import DataQualityError, validate_processed, validate_processed_partitioned
    try:
        if args.partitioned:
            df = validate_processed_partitioned(asof_date=args.asof, report_path=args.report)
        else:
            df = validate_processed(asof_date=args.asof, report_path=args.report)
    except DataQualityError as e:
        print(f"DATA QUALITY ERROR:\n{e}", file=sys.stderr)
        return 2
    print(f"validate OK ({len(df)} findings)")
    if not df.empty:
        print(df.to_string(index=False))
    return 0


def _cmd_snapshot_data(args: argparse.Namespace) -> int:
    """冻结 processed 数据集，并可校验既有快照。"""
    from pathlib import Path
    from src.research.lineage import default_snapshot_dir, verify_data_snapshot, write_data_snapshot

    if args.verify:
        ok, message = verify_data_snapshot(Path(args.verify))
        print(message)
        return 0 if ok else 2

    snapshot, path = write_data_snapshot(
        output_dir=Path(args.out) if args.out else default_snapshot_dir(),
        source=args.source,
    )
    coverage = snapshot["coverage"]
    print(f"snapshot: {snapshot['snapshot_id']}")
    print(f"layout: {snapshot['layout']}; files: {len(snapshot['files'])}; bytes: {snapshot['total_bytes']}")
    print(f"coverage: {coverage['calendar_start']} -> {coverage['calendar_end']} ({coverage['calendar_rows']} days)")
    print(f"written to: {path}")
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    """阶段 9 + 11：端到端回测 + 报告。"""
    from pathlib import Path
    import pandas as pd
    from src.data.cleaner import PROC_BARS, PROC_STOCK_BASIC, PROC_TRADE_CALENDAR
    from src.backtest.engine import run_backtest
    from src.reports.performance import build_report
    from src.research.lineage import default_run_manifest_dir, default_snapshot_dir, load_data_snapshot, verify_data_snapshot, write_data_snapshot, write_run_manifest

    bars_p = PROC_BARS() if callable(PROC_BARS) else PROC_BARS
    sb_p = PROC_STOCK_BASIC() if callable(PROC_STOCK_BASIC) else PROC_STOCK_BASIC
    cal_p = PROC_TRADE_CALENDAR() if callable(PROC_TRADE_CALENDAR) else PROC_TRADE_CALENDAR

    if not bars_p.exists():
        print(f"bars not found: {bars_p}\n请先跑 make self-test --keep-raw 或 make clean-data", file=sys.stderr)
        return 2
    bars = pd.read_parquet(bars_p)
    sb = pd.read_parquet(sb_p) if sb_p.exists() else pd.DataFrame()
    cal = pd.read_parquet(cal_p) if cal_p.exists() else pd.DataFrame()

    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        verified, message = verify_data_snapshot(snapshot_path)
        if not verified:
            print(f"DATA SNAPSHOT ERROR: {message}", file=sys.stderr)
            return 2
        snapshot = load_data_snapshot(snapshot_path)
        if snapshot["layout"] != "monolithic":
            print("DATA SNAPSHOT ERROR: backtest requires a monolithic bars.parquet snapshot", file=sys.stderr)
            return 2
    else:
        snapshot, snapshot_path = write_data_snapshot(
            output_dir=default_snapshot_dir(),
            layout="monolithic",
        )

    print(f"bars: {len(bars)} rows; stocks: {len(sb)}; cal: {len(cal)}")
    result = run_backtest(bars, sb, cal, initial_cash=args.initial_cash)
    nav = result["nav"]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nav.to_csv(out_path)

    report = build_report(nav, result["orders"], result.get("daily_logs"))
    report_path = out_path.parent / "report.json"
    import json
    with report_path.open("w") as f:
        json.dump(report.to_dict(), f, indent=2, default=str)

    manifest, manifest_path = write_run_manifest(
        output_dir=Path(args.manifest_out) if args.manifest_out else default_run_manifest_dir(),
        experiment_type="backtest",
        snapshot=snapshot,
        arguments={
            "initial_cash": args.initial_cash,
            "nav_output": str(out_path),
            "rich_report": not args.no_rich,
        },
        metrics=report.to_dict(),
        artifacts={
            "nav": str(out_path),
            "report": str(report_path),
            "data_snapshot": str(snapshot_path),
        },
    )

    print(f"rebalances: {sum(1 for log in result['daily_logs'] if log.is_rebalance)}")
    print(f"orders: {len(result['orders'])} (filled={report.filled_trades}, rejected={report.rejected_trades})")
    print(f"NAV written to: {out_path}")
    print(f"Report written to: {report_path}")
    print(f"Run manifest: {manifest_path} ({manifest['run_id']})")
    print()

    if args.no_rich:
        print(report.pretty())
    else:
        try:
            from src.reports.rich_report import render_report as render_rich
            render_rich(report, nav, daily_logs=result.get("daily_logs"))
        except Exception as e:
            print(f"[rich 报告渲染失败: {e}; fallback to text]")
            print(report.pretty())
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """从已有 nav.csv 渲染 rich 报告（无需重跑回测）。"""
    import pandas as pd
    from pathlib import Path
    from src.reports.performance import build_report
    nav_path = Path(args.nav)
    if not nav_path.exists():
        print(f"nav file not found: {nav_path}", file=sys.stderr)
        return 2
    nav = pd.read_csv(nav_path, index_col=0, parse_dates=True)
    # 尝试从同名 .json 读 orders；没有就空
    orders_path = nav_path.parent / "orders.json"
    if orders_path.exists():
        import json
        with orders_path.open() as f:
            json.load(f)
        # V1 简化：直接用 build_report 接受空 orders 即可
        orders = []
    else:
        orders = []
    report = build_report(nav, orders)
    try:
        from src.reports.rich_report import render_report as render_rich
        render_rich(report, nav)
    except Exception as e:
        print(f"[rich 报告渲染失败: {e}; fallback to text]")
        print(report.pretty())
    return 0


def _cmd_overfit(args: argparse.Namespace) -> int:
    """阶段 12：过拟合 / 稳健性测试。"""
    import pandas as pd
    from pathlib import Path
    from src.data.cleaner import PROC_BARS, PROC_STOCK_BASIC, PROC_TRADE_CALENDAR
    from src.reports.overfit import run_all_overfit_tests
    from src.research.lineage import default_run_manifest_dir, default_snapshot_dir, load_data_snapshot, verify_data_snapshot, write_data_snapshot, write_run_manifest

    bars_p = PROC_BARS() if callable(PROC_BARS) else PROC_BARS
    sb_p = PROC_STOCK_BASIC() if callable(PROC_STOCK_BASIC) else PROC_STOCK_BASIC
    cal_p = PROC_TRADE_CALENDAR() if callable(PROC_TRADE_CALENDAR) else PROC_TRADE_CALENDAR

    if not bars_p.exists():
        print(f"bars not found: {bars_p}", file=sys.stderr)
        return 2
    bars = pd.read_parquet(bars_p)
    sb = pd.read_parquet(sb_p) if sb_p.exists() else pd.DataFrame()
    cal = pd.read_parquet(cal_p) if cal_p.exists() else pd.DataFrame()

    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        verified, message = verify_data_snapshot(snapshot_path)
        if not verified:
            print(f"DATA SNAPSHOT ERROR: {message}", file=sys.stderr)
            return 2
        snapshot = load_data_snapshot(snapshot_path)
        if snapshot["layout"] != "monolithic":
            print("DATA SNAPSHOT ERROR: overfit requires a monolithic bars.parquet snapshot", file=sys.stderr)
            return 2
    else:
        snapshot, snapshot_path = write_data_snapshot(
            output_dir=default_snapshot_dir(),
            layout="monolithic",
        )

    out = run_all_overfit_tests(bars, sb, cal, split_date=args.split_date)
    print("=" * 60)
    for name, summary in out.items():
        print(f"\n── {name} ──")
        print(summary.pretty())
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, summary in out.items():
        for r in summary.results:
            d = r.report.to_dict()
            row = {"test": name, "label": r.label, **r.params}
            for k in ("total_return", "annualized_return", "annualized_vol", "sharpe",
                      "max_drawdown", "filled_trades", "annualized_turnover", "cost_ratio"):
                row[k] = d.get(k)
            rows.append(row)
    pd.DataFrame(rows).to_csv(out_path, index=False)
    manifest, manifest_path = write_run_manifest(
        output_dir=Path(args.manifest_out) if args.manifest_out else default_run_manifest_dir(),
        experiment_type="overfit",
        snapshot=snapshot,
        arguments={"split_date": args.split_date, "output": str(out_path)},
        metrics={"test_groups": len(out), "result_rows": len(rows)},
        artifacts={"overfit_csv": str(out_path), "data_snapshot": str(snapshot_path)},
    )
    print(f"\n→ written to {out_path}")
    print(f"Run manifest: {manifest_path} ({manifest['run_id']})")
    return 0


def _cmd_paper(args: argparse.Namespace) -> int:
    """阶段 13：模拟盘单日任务（幂等）。"""
    import pandas as pd
    from pathlib import Path
    from src.data.cleaner import PROC_BARS, PROC_STOCK_BASIC, PROC_TRADE_CALENDAR
    from src.backtest.paper import run_daily

    bars_p = PROC_BARS() if callable(PROC_BARS) else PROC_BARS
    sb_p = PROC_STOCK_BASIC() if callable(PROC_STOCK_BASIC) else PROC_STOCK_BASIC
    cal_p = PROC_TRADE_CALENDAR() if callable(PROC_TRADE_CALENDAR) else PROC_TRADE_CALENDAR

    if not bars_p.exists():
        print(f"bars not found: {bars_p}", file=sys.stderr)
        return 2
    bars = pd.read_parquet(bars_p)
    sb = pd.read_parquet(sb_p) if sb_p.exists() else pd.DataFrame()
    cal = pd.read_parquet(cal_p) if cal_p.exists() else pd.DataFrame()

    state = run_daily(
        bars, sb, cal, asof_date=args.asof,
        initial_cash=args.initial_cash,
        state_dir=Path(args.state_dir),
    )
    print(f"date: {state.asof_date}")
    print(f"target weights: {state.target_weights}")
    print(f"orders: {len(state.orders)}")
    for od in state.orders:
        print(f"  {od['side']} {od['code']} {od['shares']} @ {od['price']} → {od['status']}")
    if state.notes and state.notes != "ok":
        print(f"notes: {state.notes}")
    return 0


def _cmd_broker(args: argparse.Namespace) -> int:
    """阶段 14：broker 操作。V1 阶段仅支持 emergency stop / status。"""
    from src.broker import emergency_stop, is_emergency_stopped, clear_emergency_stop
    from src.risk.controls import TRADING_ENABLED

    if args.action == "emergency-stop":
        reason = input("reason for emergency stop: ").strip() or "manual CLI"
        emergency_stop(reason)
        print("EMERGENCY STOP triggered. All auto-trading disabled.")
        return 0
    if args.action == "clear-stop":
        ans = input("type 'YES' to clear emergency stop: ").strip()
        if ans == "YES":
            clear_emergency_stop()
            print("emergency stop cleared.")
        else:
            print("aborted.")
        return 0
    if args.action == "status":
        print(f"TRADING_ENABLED: {TRADING_ENABLED}")
        print(f"emergency_stopped: {is_emergency_stopped()}")
        print("V1 broker layer: manual mode only (no auto-submit)")
        return 0
    return 1


def _cmd_self_test(args: argparse.Namespace) -> int:
    """不联网：用 fixture 走完 downloader/cleaner 管道。

    默认写到 tmp 目录，不污染项目。
    --keep-raw 时写到项目的 data/raw/ 和 data/processed/，方便后续 backtest。
    """
    import tempfile
    from pathlib import Path
    from src.data import downloader
    from src.data.cleaner import run_all
    from tests.fixtures.bars_fixture import build_fixture

    saved = {
        "RAW_BARS_DIR": downloader.RAW_BARS_DIR,
        "RAW_STOCK_BASIC": downloader.RAW_STOCK_BASIC,
        "RAW_TRADE_CALENDAR": downloader.RAW_TRADE_CALENDAR,
    }
    from src.data import cleaner
    saved["DATA_PROCESSED"] = cleaner.DATA_PROCESSED

    if args.keep_raw:
        # 用项目目录
        raw = downloader.DATA_RAW
        proc = cleaner.DATA_PROCESSED
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "bars").mkdir(exist_ok=True)
        proc.mkdir(parents=True, exist_ok=True)
        downloader.RAW_BARS_DIR = raw / "bars"
        downloader.RAW_STOCK_BASIC = raw / "stock_basic.parquet"
        downloader.RAW_TRADE_CALENDAR = raw / "trade_calendar.parquet"
        cleaner.DATA_PROCESSED = proc
        try:
            build_fixture()
            out = run_all()
            for k, v in out.items():
                print(f"  {k}: {len(v)} rows")
        finally:
            for k, v in saved.items():
                if k == "DATA_PROCESSED":
                    cleaner.DATA_PROCESSED = v
                else:
                    setattr(downloader, k, v)
        return 0

    # 默认：tmp
    try:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            raw = td_path / "raw"
            (raw / "bars").mkdir(parents=True)
            proc = td_path / "processed"
            proc.mkdir()
            downloader.RAW_BARS_DIR = raw / "bars"
            downloader.RAW_STOCK_BASIC = raw / "stock_basic.parquet"
            downloader.RAW_TRADE_CALENDAR = raw / "trade_calendar.parquet"
            cleaner.DATA_PROCESSED = proc
            build_fixture()
            out = run_all()
            for k, v in out.items():
                print(f"  {k}: {len(v)} rows")
    finally:
        downloader.RAW_BARS_DIR = saved["RAW_BARS_DIR"]
        downloader.RAW_STOCK_BASIC = saved["RAW_STOCK_BASIC"]
        downloader.RAW_TRADE_CALENDAR = saved["RAW_TRADE_CALENDAR"]
        cleaner.DATA_PROCESSED = saved["DATA_PROCESSED"]
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src", description="a_share_quant CLI")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    # download
    pd_ = sub.add_parser("download", help="download raw data from baostock")
    pd_.add_argument("--start-date", default=None)
    pd_.add_argument("--end-date", default=None)
    pd_.add_argument("--codes", default=None, help="comma-separated; default = all A-shares")
    pd_.add_argument(
        "--include-inactive",
        action="store_true",
        help="include delisted ordinary shares for historical coverage",
    )

    # clean
    pc = sub.add_parser("clean", help="clean raw -> processed")
    pc.add_argument(
        "--partitioned",
        action="store_true",
        help="write one processed parquet per code for low-memory full-market imports",
    )

    # self-test
    pst = sub.add_parser("self-test", help="run the pipeline against local fixtures (no network)")
    pst.add_argument("--keep-raw", action="store_true",
                     help="write to project's data/raw and data/processed (default: tmp)")

    # validate
    pv = sub.add_parser("validate", help="run data quality checks on processed/")
    pv.add_argument("--asof", required=True, help="as-of date YYYY-MM-DD")
    pv.add_argument("--report", default=None, help="override report path")
    pv.add_argument(
        "--partitioned",
        action="store_true",
        help="validate processed/bars_by_code without loading full-market bars into memory",
    )

    ps = sub.add_parser("snapshot-data", help="freeze processed data as a local content-hash manifest")
    ps.add_argument("--out", default=None, help="snapshot directory (default: data/metadata)")
    ps.add_argument("--source", default="baostock", help="declared source label stored in the snapshot")
    ps.add_argument("--verify", default=None, help="verify an existing snapshot against current processed data")

    # backtest
    pb = sub.add_parser("backtest", help="run backtest on processed/ data")
    pb.add_argument("--initial-cash", type=float, default=1_000_000.0)
    pb.add_argument("--out", default="results/nav.csv", help="output NAV csv path")
    pb.add_argument("--no-rich", action="store_true",
                    help="skip rich report, print plain text only")
    pb.add_argument("--snapshot", default=None, help="existing data snapshot JSON; default creates/reuses one")
    pb.add_argument("--manifest-out", default=None, help="run manifest directory (default: results/run_manifests)")

    # report (从 nav.csv 单独渲染)
    pr = sub.add_parser("report", help="render rich report from existing nav.csv")
    pr.add_argument("--nav", default="results/nav.csv", help="path to nav.csv")

    # overfit
    po = sub.add_parser("overfit", help="run overfitting / robustness tests")
    po.add_argument("--split-date", required=True, help="in-sample / out-of-sample split YYYY-MM-DD")
    po.add_argument("--out", default="results/overfit.csv", help="output CSV")
    po.add_argument("--snapshot", default=None, help="existing data snapshot JSON; default creates/reuses one")
    po.add_argument("--manifest-out", default=None, help="run manifest directory (default: results/run_manifests)")

    # paper
    pp = sub.add_parser("paper", help="run a single paper-trading day (idempotent)")
    pp.add_argument("--asof", required=True, help="paper trade as-of date YYYY-MM-DD")
    pp.add_argument("--initial-cash", type=float, default=1_000_000.0)
    pp.add_argument("--state-dir", default="results/paper_state", help="state persistence dir")

    # broker (manual only in V1)
    pbr = sub.add_parser("broker", help="broker operations (V1: manual only)")
    pbr.add_argument("--action", choices=["emergency-stop", "clear-stop", "status"],
                     default="status")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.cmd is None:
        return _cmd_dry(args)
    if args.cmd == "download":
        return _cmd_download(args)
    if args.cmd == "clean":
        return _cmd_clean(args)
    if args.cmd == "self-test":
        return _cmd_self_test(args)
    if args.cmd == "validate":
        return _cmd_validate(args)
    if args.cmd == "snapshot-data":
        return _cmd_snapshot_data(args)
    if args.cmd == "backtest":
        return _cmd_backtest(args)
    if args.cmd == "report":
        return _cmd_report(args)
    if args.cmd == "overfit":
        return _cmd_overfit(args)
    if args.cmd == "paper":
        return _cmd_paper(args)
    if args.cmd == "broker":
        return _cmd_broker(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
