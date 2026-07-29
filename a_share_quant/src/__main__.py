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
from pathlib import Path

from src import __version__
from src.config import CONFIG_PATH, load_config
from src.data import BARS, COL_CODE, COL_DATE, make_empty_bars


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
    )

    cfg = load_config()
    if args.end_date is None:
        from datetime import date
        end = date.today().isoformat()
    else:
        end = args.end_date
    dr = DownloadRange(start_date=args.start_date or cfg.universe.start_date, end_date=end)

    print(f"downloading stock_basic...")
    sb = download_stock_basic()
    print(f"  -> {len(sb)} stocks")

    print(f"downloading trade_calendar {dr.start_date} -> {dr.end_date}...")
    tc = download_trade_calendar(dr.start_date, dr.end_date)
    print(f"  -> {len(tc)} calendar days")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = sb[COL_CODE].astype(str).tolist()
    print(f"downloading bars for {len(codes)} codes...")
    bars = download_bars_all(codes, dr)
    print(f"  -> {len(bars)} bar rows")
    return 0


def _cmd_clean(_args: argparse.Namespace) -> int:
    """阶段 4 clean 子命令。raw → processed。"""
    from src.data.cleaner import run_all
    out = run_all()
    for k, v in out.items():
        print(f"  {k}: {len(v)} rows")
    return 0


def _cmd_self_test(_args: argparse.Namespace) -> int:
    """不联网：用 fixture 走完 downloader/cleaner 管道。

    重要：使用 tmp 目录，不污染项目的 data/raw/ 和 data/processed/。
    函数退出时恢复原始模块属性，避免污染后续测试。
    """
    import tempfile
    from pathlib import Path
    from src.data import downloader
    from src.data.cleaner import run_all
    from tests.fixtures.bars_fixture import build_fixture

    # 保存原值（防止污染其他测试 / 进程）
    saved = {
        "RAW_BARS_DIR": downloader.RAW_BARS_DIR,
        "RAW_STOCK_BASIC": downloader.RAW_STOCK_BASIC,
        "RAW_TRADE_CALENDAR": downloader.RAW_TRADE_CALENDAR,
    }
    from src.data import cleaner
    saved["DATA_PROCESSED"] = cleaner.DATA_PROCESSED

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

    # clean
    sub.add_parser("clean", help="clean raw -> processed")

    # self-test
    sub.add_parser("self-test", help="run the pipeline against local fixtures (no network)")

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
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
