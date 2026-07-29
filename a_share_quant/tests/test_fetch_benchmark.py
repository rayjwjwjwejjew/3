"""测试 fetch_benchmark。

沙箱无 baostock 网络,因此测试用 stub_fetcher 注入假数据。
关键覆盖:
  1. 指标计算:总收益、年化、最大回撤、夏普
  2. 多基准 + alpha 计算
  3. CLI 参数 (--index, --start, --end, --strategy-return)
  4. --no-network 立即退出 2
  5. fetcher 抛错时优雅跳过
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.research.fetch_benchmark import (
    BenchmarkResult,
    _annualized_return,
    _annualized_vol,
    _max_drawdown,
    _sharpe,
    alpha,
    compute_benchmark,
    render_report,
)
from src.research import fetch_benchmark as fb_mod


# ===== 单元: 指标计算 =====
def test_annualized_return_constant_series():
    """常数列: 年化收益应为 0。"""
    s = pd.Series([100.0] * 252, index=pd.date_range("2025-01-01", periods=252))
    assert _annualized_return(s) == pytest.approx(0.0, abs=1e-9)


def test_annualized_return_double_in_one_year():
    """252 个 calendar day (≈ 0.69 年) 内翻倍 → 年化 ≈ 2.0^(1/0.69)-1 ≈ 1.74。"""
    s = pd.Series([100.0 + i * (100.0 / 251) for i in range(252)],
                  index=pd.date_range("2025-01-01", periods=252))
    r = _annualized_return(s)
    # 251 calendar days = 0.687 年,翻倍折年化应 ≈ 174%
    assert 1.70 < r < 1.80


def test_max_drawdown_simple():
    """100 → 120 → 60 → 80: 最大回撤 = (60-120)/120 = -50%。"""
    s = pd.Series([100.0, 120.0, 60.0, 80.0],
                  index=pd.date_range("2025-01-01", periods=4))
    dd = _max_drawdown(s)
    assert dd == pytest.approx(-0.5, abs=1e-9)


def test_max_drawdown_monotonic_up():
    """单调上升: 回撤 = 0。"""
    s = pd.Series([100.0, 110.0, 120.0, 130.0],
                  index=pd.date_range("2025-01-01", periods=4))
    assert _max_drawdown(s) == pytest.approx(0.0, abs=1e-9)


def test_sharpe_random_walk_near_zero():
    """随机游走: 夏普接近 0。"""
    import numpy as np
    np.random.seed(42)
    rets = np.random.normal(0, 0.01, 252)
    s = pd.Series((1 + pd.Series(rets)).cumprod() * 100,
                  index=pd.date_range("2025-01-01", periods=252))
    sh = _sharpe(s)
    assert abs(sh) < 1.0


def test_alpha_simple():
    assert alpha(-0.1727, -0.10) == pytest.approx(-0.0727, abs=1e-9)
    assert alpha(0.05, 0.10) == pytest.approx(-0.05, abs=1e-9)


# ===== 单元: compute_benchmark + stub fetcher =====
def _make_close_series(
    start: str, end: str, start_price: float, end_price: float,
) -> pd.Series:
    """线性增长, 方便手算总收益。"""
    days = pd.bdate_range(start, end)
    n = len(days)
    prices = [start_price + (end_price - start_price) * i / (n - 1) for i in range(n)]
    return pd.Series(prices, index=days, name="close")


def _stub_fetcher_factory(table: dict):
    """table: {(code, start, end) -> pd.Series}"""
    def fetcher(code: str, start: str, end: str):
        key = (code, start, end)
        if key not in table:
            raise RuntimeError(f"no stub for {key}")
        return table[key]
    return fetcher


def test_compute_benchmark_uses_fetcher():
    close = _make_close_series("2025-01-01", "2025-12-31", 100.0, 110.0)
    fetcher = _stub_fetcher_factory({("sh.000300", "2025-01-01", "2025-12-31"): close})
    r = compute_benchmark("sh.000300", "沪深 300", "2025-01-01", "2025-12-31", fetcher=fetcher)
    assert r.code == "sh.000300"
    assert r.total_return == pytest.approx(0.10, abs=1e-6)
    assert r.n_days == len(close)
    assert r.max_drawdown <= 0  # 单调上升 → 0


def test_compute_benchmark_empty_raises():
    fetcher = lambda c, s, e: pd.Series(dtype=float)
    with pytest.raises(RuntimeError, match="empty data"):
        compute_benchmark("sh.000300", "沪深 300", "2025-01-01", "2025-12-31", fetcher=fetcher)


# ===== 单元: render_report =====
def test_render_report_without_strategy():
    r = BenchmarkResult(
        code="sh.000300", name="沪深 300",
        start_date="2025-01-01", end_date="2025-12-31",
        n_days=252, total_return=0.10, annualized_return=0.10,
        annualized_vol=0.15, max_drawdown=-0.05, sharpe=0.5,
    )
    out = render_report(None, [r])
    assert "沪深 300" in out
    assert "策略" not in out.split("alpha")[0].split("\n")[0]  # alpha 段不会出


def test_render_report_with_strategy_and_alpha():
    r1 = BenchmarkResult(
        code="sh.000300", name="沪深 300",
        start_date="2025-01-01", end_date="2026-07-29",
        n_days=400, total_return=-0.10, annualized_return=-0.07,
        annualized_vol=0.18, max_drawdown=-0.15, sharpe=-0.4,
    )
    r2 = BenchmarkResult(
        code="sh.000905", name="中证 500",
        start_date="2025-01-01", end_date="2026-07-29",
        n_days=400, total_return=-0.12, annualized_return=-0.08,
        annualized_vol=0.22, max_drawdown=-0.20, sharpe=-0.5,
    )
    out = render_report(-0.1727, [r1, r2])
    assert "策略 alpha" in out
    assert "沪深 300" in out
    # alpha = -17.27% - (-10%) = -7.27%
    assert "-7.27%" in out


# ===== CLI =====
def test_cli_no_network_exits_2():
    """--no-network 应该立即退出码 2,不发任何 baostock 请求。"""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = fb_mod.main(["--no-network"])
    assert code == 2
    assert "requires network" in buf.getvalue() or "sandbox" in buf.getvalue()


def test_cli_with_stub_fetcher(monkeypatch):
    """用 monkeypatch 替换 _fetch_index_close,验证 CLI 串联工作。"""
    close = _make_close_series("2025-01-01", "2026-07-29", 4000.0, 3600.0)
    table = {("sh.000300", "2025-01-01", "2026-07-29"): close}

    def stub(code, start, end, client=None):
        return table[(code, start, end)]

    monkeypatch.setattr(fb_mod, "_fetch_index_close", stub)
    # 同时 stub _bs_login/_bs_logout 避免真连接
    monkeypatch.setattr(fb_mod, "_bs_login", lambda: object())
    monkeypatch.setattr(fb_mod, "_bs_logout", lambda bs: None)

    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = fb_mod.main([
            "--index", "sh.000300",
            "--start", "2025-01-01",
            "--end", "2026-07-29",
            "--strategy-return", "-0.1727",
            "--out", "/tmp/_test_bench.csv",
        ])
    assert code == 0
    out = buf.getvalue()
    assert "沪深 300" in out
    assert "策略 alpha" in out
    # 3600/4000 - 1 = -10%
    assert "-10.00%" in out
    # alpha = -17.27% - (-10%) = -7.27%
    assert "-7.27%" in out
    # CSV 落盘
    import os
    assert os.path.exists("/tmp/_test_bench.csv")
    df = pd.read_csv("/tmp/_test_bench.csv")
    assert len(df) == 1
    assert df.iloc[0]["code"] == "sh.000300"
    os.remove("/tmp/_test_bench.csv")


def test_cli_one_fetcher_fails_others_continue(monkeypatch):
    """一个 fetcher 失败不应中断其他基准。"""
    def partial_stub(code, start, end, client=None):
        if code == "sh.000300":
            return _make_close_series(start, end, 100.0, 110.0)
        raise RuntimeError("simulated network error")

    monkeypatch.setattr(fb_mod, "_fetch_index_close", partial_stub)
    monkeypatch.setattr(fb_mod, "_bs_login", lambda: object())
    monkeypatch.setattr(fb_mod, "_bs_logout", lambda bs: None)

    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = fb_mod.main([
            "--index", "sh.000300", "--index", "sh.000905",
            "--start", "2025-01-01", "--end", "2025-12-31",
        ])
    assert code == 0  # 至少一个成功就 OK
    assert "沪深 300" in buf.getvalue()
    # stderr 应该有 warn
    assert "failed" in buf.getvalue() or "warn" in buf.getvalue()
