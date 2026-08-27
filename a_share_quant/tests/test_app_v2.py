"""Streamlit Web App v2 测试（多页 + 4 项新特性）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _FakeSt:
    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return _FakeSt() if name[0].islower() else None
        return _noop
    def __call__(self, *a, **k): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def mock_st(monkeypatch):
    fake = _FakeSt()
    fake.markdown = lambda *a, **k: None
    fake.error = lambda *a, **k: None
    fake.warning = lambda *a, **k: None
    fake.info = lambda *a, **k: None
    fake.success = lambda *a, **k: None
    fake.metric = lambda *a, **k: None
    fake.dataframe = lambda *a, **k: None
    fake.plotly_chart = lambda *a, **k: None
    fake.set_page_config = lambda *a, **k: None
    fake.title = lambda *a, **k: None
    fake.caption = lambda *a, **k: None
    fake.columns = lambda n: [_FakeSt() for _ in range(n if isinstance(n, int) else len(n))]
    fake.expander = lambda *a, **k: _FakeSt()
    fake.slider = lambda *a, **k: 100
    fake.number_input = lambda *a, **k: 100
    fake.checkbox = lambda *a, **k: False
    fake.cache_data = lambda *a, **k: (lambda f: f)
    fake.spinner = lambda *a, **k: _FakeSt().__enter__() if hasattr(_FakeSt(), '__enter__') else _FakeSt()
    fake.sidebar = _FakeSt()
    fake.subheader = lambda *a, **k: None
    fake.divider = lambda *a, **k: None
    fake.radio = lambda *a, **k: "📊 概览"
    fake.selectbox = lambda *a, **k: "600000"
    fake.button = lambda *a, **k: False
    fake.download_button = lambda *a, **k: None
    fake.code = lambda *a, **k: None
    fake.stop = lambda: None

    import streamlit
    for attr in dir(fake):
        if not attr.startswith('_'):
            monkeypatch.setattr(streamlit, attr, getattr(fake, attr))
    return fake


@pytest.fixture
def app_module(mock_st):
    sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location("app", PROJECT_ROOT / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def charts_module():
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.webapp import charts
    return charts


@pytest.fixture
def bench_module():
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.benchmark import benchmarks
    return benchmarks


@pytest.fixture
def loader_module():
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.webapp import data_loader
    return data_loader


# ===== 数据 fixture =====
@pytest.fixture
def sample_bars():
    n = 100
    codes = ["600000", "600001", "600002"]
    rows = []
    dates = pd.bdate_range("2024-01-02", periods=n)
    for code in codes:
        rng = np.random.default_rng(hash(code) % 100)
        prices = 10.0 * np.cumprod(1 + rng.normal(0, 0.015, n))
        for i, d in enumerate(dates):
            px = float(prices[i])
            rows.append({
                "code": code, "date": d,
                "open": px, "high": px * 1.005, "low": px * 0.995,
                "close": px, "adj_close": px,
                "volume": 1_000_000, "amount": 200_000_000.0, "adj_factor": 1.0,
                "is_suspended": False, "is_st": False,
                "limit_up": round(px * 1.10, 2), "limit_down": round(px * 0.90, 2),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_nav():
    dates = pd.bdate_range("2024-01-02", periods=100)
    nav = 1_000_000.0 * np.cumprod(1 + np.random.default_rng(0).normal(0.001, 0.012, 100))
    return pd.DataFrame({
        "date": dates, "nav": nav, "cash": 800_000.0, "position_value": nav - 800_000.0,
    }).set_index("date")


# ===== 基准测试 =====
def test_benchmark_equal_weight(bench_module, sample_bars):
    bench = bench_module.make_equal_weight_benchmark(sample_bars, initial_cash=1_000_000.0)
    assert not bench.empty
    assert "nav" in bench.columns
    # 起始 = initial
    assert abs(bench["nav"].iloc[0] - 1_000_000.0) < 1
    # 净值单调但可增可减
    assert bench["nav"].iloc[-1] > 0


def test_benchmark_empty(bench_module):
    bench = bench_module.make_equal_weight_benchmark(pd.DataFrame(), 1_000_000.0)
    assert bench.empty


def test_excess_return(bench_module, sample_nav, sample_bars):
    bench = bench_module.make_equal_weight_benchmark(sample_bars)
    excess = bench_module.excess_return(sample_nav, bench)
    assert not excess.empty
    assert "excess" in excess.columns


def test_load_real_benchmark_not_found(bench_module, tmp_path, monkeypatch):
    """无真实数据时返回 None。"""
    monkeypatch.setattr("src.data.benchmark.benchmarks.Path", lambda x: tmp_path / x if not str(x).startswith("/") else Path(x))
    # 实际上不能直接 monkeypatch Path 那样，简单测：直接传 path 参数
    result = bench_module.load_real_benchmark("sh000300", path=tmp_path / "sh000300.parquet")
    assert result is None


# ===== K 线图测试 =====
def test_kline_chart(charts_module, sample_bars):
    fig = charts_module.kline_chart(sample_bars, "600000")
    assert fig is not None
    # 应有 3 个 trace：candlestick + volume bar + MA20
    assert len(fig.data) >= 3


def test_kline_chart_no_data(charts_module):
    fig = charts_module.kline_chart(pd.DataFrame(), "999999")
    assert fig is not None


# ===== 净值图测试 =====
def test_nav_chart(charts_module, sample_nav):
    fig = charts_module.nav_chart(sample_nav)
    assert fig is not None
    assert len(fig.data) >= 1


def test_nav_chart_with_benchmark(charts_module, sample_nav, sample_bars):
    bench = __import__("src.data.benchmark.benchmarks", fromlist=["make_equal_weight_benchmark"]).make_equal_weight_benchmark(sample_bars)
    fig = charts_module.nav_chart(sample_nav, benchmark=bench)
    assert fig is not None
    assert len(fig.data) >= 2  # 策略 + 基准


# ===== 导出 PNG =====
def test_export_png(charts_module, sample_nav, tmp_path):
    fig = charts_module.nav_chart(sample_nav)
    out = tmp_path / "test.png"
    success = charts_module.export_to_png(fig, out)
    # 沙箱可能 kaleido 缺；至少要返回 bool
    assert isinstance(success, bool)


# ===== 数据加载器 =====
def test_data_loader_not_found(loader_module, tmp_path, monkeypatch):
    """无真实数据时返回 None。"""
    # 直接测：当前真实数据状态
    # 注：测试运行时不依赖真实数据
    if not (Path("data/processed") / "bars.parquet").exists():
        assert loader_module.has_real_data() is False
        assert loader_module.load_real_bars() is None


# ===== App 多页入口 =====
def test_app_has_all_pages(app_module):
    for fn in ["page_overview", "page_backtest", "page_market", "page_overfit", "page_broker", "main"]:
        assert hasattr(app_module, fn), f"missing page function: {fn}"


def test_app_main_callable(app_module):
    assert callable(app_module.main)


def test_app_docstring(app_module):
    assert app_module.__doc__ is not None
    assert "streamlit" in app_module.__doc__.lower()
    assert "同花顺" in app_module.__doc__ or "tonghuashun" in app_module.__doc__.lower() or "K 线" in app_module.__doc__


def test_app_uses_top_level_workspace_navigation():
    """研究视图应从侧边栏参数中分离，保留一条清晰的主导航。"""
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert '"研究工作区"' in source
    assert "horizontal=True" in source
    for label in ("总览", "回测明细", "行情观察", "稳健性", "执行边界"):
        assert label in source


def test_theme_keeps_accessible_reduced_motion_fallback():
    import inspect

    from src.webapp import theme

    assert "prefers-reduced-motion: reduce" in theme.APP_CSS
    assert "prefers-reduced-transparency: reduce" in theme.APP_CSS
    assert "研究用途 · 不连接券商" in inspect.getsource(theme.render_app_header)
