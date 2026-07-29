"""拉真实指数基准,用于 H4 诊断 (回答"市场环境贡献多少 -17%")。

用法(本机):
    python -m src.research.fetch_benchmark
    python -m src.research.fetch_benchmark --index sh.000300 --start 2025-01-01 --end 2026-07-29
    python -m src.research.fetch_benchmark --out results/benchmarks.csv

沙箱无网络,本模块默认会优雅报错并打印"需要联网"提示。单元测试用
fixture 模拟 baostock 响应,不需要真实网络。

输出:
    1. results/benchmarks.csv  (各基准日线 + 累计收益)
    2. stdout: 一份人类可读对照表
        沪深 300 / 中证 500 / 创业板指 / 策略本身
        区间 / 总收益 / 年化 / 最大回撤 / 策略 alpha

依赖: baostock (已在 requirements.txt)
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from src.data.downloader import _bs_login, _bs_logout, _bs_to_df  # noqa: PLC2701

logger = logging.getLogger(__name__)


# ===== 候选基准 =====
# baostock 指数代码格式:
#   sh.000300 = 沪深 300
#   sh.000905 = 中证 500
#   sz.399006 = 创业板指
#   sh.000016 = 上证 50
#   sz.399905 = 中证 500 (深圳代码,等同 sh.000905)
DEFAULT_INDICES: list[tuple[str, str]] = [
    ("sh.000300", "沪深 300"),
    ("sh.000905", "中证 500"),
    ("sz.399006", "创业板指"),
    ("sh.000016", "上证 50"),
]


@dataclass(frozen=True)
class BenchmarkResult:
    code: str
    name: str
    start_date: str
    end_date: str
    n_days: int
    total_return: float
    annualized_return: float
    annualized_vol: float
    max_drawdown: float
    sharpe: float            # 无风险利率 2%

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "n_days": self.n_days,
            "total_return": round(self.total_return, 4),
            "annualized_return": round(self.annualized_return, 4),
            "annualized_vol": round(self.annualized_vol, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 3),
        }


# ===== 核心:拉一只指数的日线并算指标 =====
def _fetch_index_close(
    code: str,
    start_date: str,
    end_date: str,
    client=None,
) -> pd.Series:
    """拉指数日线收盘价,返回 pd.Series(date, close)。"""
    bs = client or _bs_login()
    owns_session = client is None
    try:
        rs = bs.query_history_k_data_plus(
            code,
            "date,close",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2",  # 指数本身不需复权,但参数保留
        )
        df = _bs_to_df(rs)
        if df.empty:
            raise RuntimeError(f"baostock returned empty for {code} {start_date}~{end_date}")
        df = df.rename(columns={"date": "date", "close": "close"})
        df["date"] = pd.to_datetime(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        return df.set_index("date")["close"]
    finally:
        if owns_session:
            _bs_logout(bs)


def _annualized_return(s: pd.Series, periods_per_year: int = 252) -> float:
    s = s.dropna()
    if len(s) < 2 or s.iloc[0] <= 0:
        return 0.0
    n_days = (s.index[-1] - s.index[0]).days
    if n_days <= 0:
        return 0.0
    years = n_days / 365.25
    return float((s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1)


def _annualized_vol(s: pd.Series, periods_per_year: int = 252) -> float:
    r = s.pct_change(fill_method=None).dropna()
    if r.empty:
        return 0.0
    return float(r.std() * math.sqrt(periods_per_year))


def _max_drawdown(s: pd.Series) -> float:
    s = s.dropna()
    if s.empty:
        return 0.0
    running_max = s.cummax()
    dd = s / running_max - 1.0
    return float(dd.min())


def _sharpe(s: pd.Series, rf_annual: float = 0.02, periods_per_year: int = 252) -> float:
    r = s.pct_change(fill_method=None).dropna()
    if r.std() == 0 or len(r) < 2:
        return 0.0
    rf_daily = (1 + rf_annual) ** (1 / periods_per_year) - 1
    excess = r - rf_daily
    return float(excess.mean() / r.std() * math.sqrt(periods_per_year))


def compute_benchmark(
    code: str,
    name: str,
    start_date: str,
    end_date: str,
    fetcher: Callable | None = None,
) -> BenchmarkResult:
    """计算单个基准的指标。fetcher 是可注入依赖(测试用)。"""
    if fetcher is None:
        fetcher = _fetch_index_close
    close = fetcher(code, start_date, end_date)
    if close.empty:
        raise RuntimeError(f"empty data for {code}")
    return BenchmarkResult(
        code=code,
        name=name,
        start_date=str(close.index[0].date()),
        end_date=str(close.index[-1].date()),
        n_days=len(close),
        total_return=float(close.iloc[-1] / close.iloc[0] - 1),
        annualized_return=_annualized_return(close),
        annualized_vol=_annualized_vol(close),
        max_drawdown=_max_drawdown(close),
        sharpe=_sharpe(close),
    )


# ===== 策略 alpha: 策略总收益 - 基准总收益 =====
def alpha(strategy_total: float, bench_total: float) -> float:
    """Alpha 用算术差(不是超额收益的几何分解,够用)。"""
    return strategy_total - bench_total


# ===== 报告渲染 =====
def render_report(
    strategy_total: float | None,
    results: list[BenchmarkResult],
) -> str:
    lines = [
        "=" * 72,
        "H4 诊断: 真实基准 vs 策略总收益",
        "=" * 72,
    ]
    if strategy_total is not None:
        lines.append(f"策略本身总收益: {strategy_total:+.2%}")
    lines.append("")
    lines.append(
        f"{'基准':<14} {'代码':<11} {'区间':<25} "
        f"{'总收益':>9} {'年化':>9} {'最大回撤':>9} {'夏普':>7}"
    )
    lines.append("-" * 72)
    for r in results:
        lines.append(
            f"{r.name:<14} {r.code:<11} "
            f"{r.start_date}→{r.end_date} "
            f"{r.total_return:>+8.2%} {r.annualized_return:>+8.2%} "
            f"{r.max_drawdown:>+8.2%} {r.sharpe:>+6.2f}"
        )
    if strategy_total is not None:
        lines.append("")
        lines.append(f"{'策略 alpha (=策略总收益 - 基准总收益)':─<50}")
        for r in results:
            a = alpha(strategy_total, r.total_return)
            lines.append(f"  vs {r.name:<10}: {a:>+6.2%}  (策略 {strategy_total:+.2%} - 基准 {r.total_return:+.2%})")
    lines.append("=" * 72)
    return "\n".join(lines)


# ===== CLI =====
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="拉真实指数基准,诊断 H4")
    parser.add_argument(
        "--index", action="append", default=None,
        help="指数代码,可多次指定 (默认 4 个主流基准)",
    )
    parser.add_argument("--start", default="2025-01-01", help="起始日 YYYY-MM-DD")
    parser.add_argument("--end", default="2026-07-29", help="结束日 YYYY-MM-DD")
    parser.add_argument(
        "--strategy-return", type=float, default=None,
        help="(可选) 策略总收益,例如 -0.1727。提供后会算 alpha。",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="(可选) 写一份 CSV 到这个路径",
    )
    parser.add_argument(
        "--no-network", action="store_true",
        help="仅在沙箱中用,直接报错退出",
    )
    args = parser.parse_args(argv)

    if args.no_network:
        print("[sandbox] fetch_benchmark requires network. Run on local machine.",
              file=sys.stderr)
        return 2

    # 选基准
    if args.index:
        pairs = []
        for code in args.index:
            name = next((n for c, n in DEFAULT_INDICES if c == code), code)
            pairs.append((code, name))
    else:
        pairs = DEFAULT_INDICES

    results: list[BenchmarkResult] = []
    bs = _bs_login()
    try:
        for code, name in pairs:
            print(f"[fetch] {name} ({code}) {args.start} ~ {args.end} ...", file=sys.stderr)
            try:
                r = compute_benchmark(
                    code, name, args.start, args.end,
                    fetcher=lambda c, s, e, _bs=bs: _fetch_index_close(c, s, e, client=_bs),
                )
                results.append(r)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {code} failed: {e}", file=sys.stderr)
    finally:
        _bs_logout(bs)

    if not results:
        print("[error] no benchmark succeeded", file=sys.stderr)
        return 1

    print(render_report(args.strategy_return, results))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([r.to_dict() for r in results])
        df.to_csv(args.out, index=False)
        print(f"\n[saved] {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
