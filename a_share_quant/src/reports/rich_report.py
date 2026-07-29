"""同花顺风格回测报告（rich 渲染）。

布局灵感来源：同花顺客户端
- 顶部：标题栏 + 关键数字 + 红涨绿跌
- 中部：净值曲线（ASCII sparkline）+ 关键指标 + 交易统计
- 下部：年度收益表 + 调仓时间线

设计原则：
- 红涨绿跌（A 股惯例）
- 大数字 + 箭头
- 等宽对齐
- 失败用 WARNING/ERROR 标黄/标红
"""

from __future__ import annotations

import io
import shutil
from typing import Iterable

import pandas as pd
from rich.box import HEAVY, ROUNDED
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.reports.performance import PerformanceReport


# ===== 颜色 =====
RED = "bright_red"     # A 股涨
GREEN = "bright_green"  # A 股跌
YELLOW = "yellow"
CYAN = "cyan"
DIM = "dim"


def _color_ret(v: float) -> str:
    """A 股惯例：正数红、负数绿。"""
    if v > 0:
        return RED
    if v < 0:
        return GREEN
    return DIM


def _arrow(v: float) -> str:
    return "▲" if v > 0 else ("▼" if v < 0 else "─")


def _pct(v: float, digits: int = 2) -> str:
    if v != v:  # NaN
        return "—"
    return f"{v * 100:.{digits}f}%"


# ===== ASCII sparkline =====
def _sparkline(s: pd.Series, width: int = 50, height: int = 8) -> list[str]:
    """把序列画成多行 sparkline（height 行 × width 列）。"""
    if s.empty:
        return ["(empty)"] * height

    # 限制宽度（降采样）
    if len(s) > width:
        step = max(1, len(s) // width)
        s = s.iloc[::step][:width]

    mn, mx = s.min(), s.max()
    rng = mx - mn if mx != mn else 1.0
    norm = ((s - mn) / rng).values  # 0..1

    rows = []
    for h in range(height, 0, -1):
        threshold = h / height
        line = "".join("█" if v >= threshold - 0.05 else " " for v in norm)
        rows.append(line)
    # 末行加起始日期，首行加结束日期
    if rows:
        rows[0] = rows[0][:width - 12].ljust(width - 12) + f"  {s.index[-1].strftime('%Y-%m-%d')}"
        rows[-1] = f"{s.index[0].strftime('%Y-%m-%d')}  " + rows[-1][12:]
    return rows


# ===== 头部 =====
def _header_text(rep: PerformanceReport) -> Text:
    """同花顺风格顶栏：标题 + 区间 + 总收益 + 终值 + 回撤 + Sharpe。"""
    color = _color_ret(rep.total_return)
    arrow = _arrow(rep.total_return)
    interval = (
        f"{rep.start_date.strftime('%Y-%m-%d') if rep.start_date else '?'} → "
        f"{rep.end_date.strftime('%Y-%m-%d') if rep.end_date else '?'}"
    )
    dd = rep.max_drawdown
    sh_color = _color_ret(rep.sharpe)

    t = Text()
    t.append("a_share_quant V1", style="bold cyan")
    t.append("  ")
    t.append(interval, style=DIM)
    t.append("    ")
    t.append(f"{arrow} ", style=f"bold {color}")
    t.append(_pct(rep.total_return), style=f"bold {color}")
    t.append("  ")
    t.append("终值 ", style=DIM)
    t.append(f"¥{rep.final_nav:,.0f}", style="bold white")
    t.append("  ")
    t.append("回撤 ", style=DIM)
    t.append(f"{_arrow(dd)} ", style=f"bold {_color_ret(dd)}")
    t.append(_pct(dd), style=f"bold {_color_ret(dd)}")
    t.append("  ")
    t.append("Sharpe ", style=DIM)
    t.append(f"{rep.sharpe:+.2f}", style=f"bold {sh_color}")
    t.append("  ")
    t.append("初始 ", style=DIM)
    t.append(f"¥{rep.initial_cash:,.0f}", style="white")
    return t


# ===== 关键指标 + 交易统计 =====
def _metrics_table(rep: PerformanceReport) -> Table:
    t = Table(box=ROUNDED, title="[bold]关键指标[/bold]", title_justify="left",
             expand=True, show_header=False, padding=(0, 1))
    t.add_column(justify="left", style="dim")
    t.add_column(justify="right")
    t.add_column(justify="left", style="dim")
    t.add_column(justify="right")

    color = _color_ret(rep.annualized_return)
    t.add_row("年化收益", f"[bold {color}]{_pct(rep.annualized_return)}", "波动率", f"{_pct(rep.annualized_vol)}")
    color = _color_ret(rep.sharpe)
    t.add_row("夏普比率", f"[bold {color}]{rep.sharpe:+.2f}", "换手率", f"{_pct(rep.annualized_turnover, 1)}")
    color = _color_ret(rep.max_drawdown)
    t.add_row("最大回撤", f"[bold {color}]{_pct(rep.max_drawdown)}", "成本/收益", f"{_pct(rep.cost_ratio, 2)}")
    dd_recovery = (f"{rep.max_dd_recovery_days} 天" if rep.max_dd_recovery_days is not None
                   else "[yellow]未修复[/yellow]")
    t.add_row("回撤修复", dd_recovery, "最长亏损月", f"{rep.longest_losing_streak_months} 月")
    return t


def _trades_table(rep: PerformanceReport) -> Table:
    t = Table(box=ROUNDED, title="[bold]交易统计[/bold]", title_justify="left",
             expand=True, show_header=False, padding=(0, 1))
    t.add_column(justify="left", style="dim")
    t.add_column(justify="right")
    t.add_column(justify="left", style="dim")
    t.add_column(justify="right")
    t.add_row("总订单", f"{rep.total_trades}", "总成交", f"[green]{rep.filled_trades}[/green]")
    t.add_row("被拒", f"[red]{rep.rejected_trades}[/red]",
              "撤销", f"[yellow]{rep.cancelled_trades}[/yellow]")
    t.add_row("总成本", f"¥{rep.total_costs:,.0f}", "换手(年化)", f"{_pct(rep.annualized_turnover, 1)}")
    return t


# ===== 年度收益 =====
def _yearly_table(rep: PerformanceReport) -> Table:
    t = Table(box=ROUNDED, title="[bold]年度收益[/bold]", title_justify="left",
             expand=True, show_header=True, padding=(0, 1))
    t.add_column("年份", justify="left", style="bold")
    t.add_column("收益", justify="right")
    t.add_column("柱状", justify="left")

    if rep.yearly_returns.empty:
        t.add_row(DIM, DIM, DIM)
        return t

    for y, r in rep.yearly_returns.items():
        y_str = y.strftime("%Y") if hasattr(y, "strftime") else str(y)
        if pd.isna(r):
            t.add_row(y_str, DIM, "")
            continue
        color = _color_ret(r)
        bar_len = min(40, int(abs(r) * 200))  # 2% → 4 个块，10% → 20 个块
        bar = "█" * bar_len if bar_len > 0 else ""
        t.add_row(y_str, f"[bold {color}]{_pct(r)}", f"[{color}]{bar}[/{color}]")
    return t


# ===== 调仓时间线 =====
def _rebalance_timeline(rep: PerformanceReport, nav: pd.DataFrame, max_events: int = 30) -> Panel:
    rebal_logs = [log for log in (getattr(rep, "_daily_logs", []) or [])
                  if getattr(log, "is_rebalance", False)]
    rebal_dates = [log.date for log in rebal_logs[:max_events]]

    txt = Text()
    if not rebal_dates:
        txt.append("(no rebalance events)", style=DIM)
    else:
        for i, d in enumerate(rebal_dates):
            if i > 0:
                txt.append("  ")
            if d in nav.index:
                idx = nav.index.get_loc(d)
                if idx > 0:
                    prev = float(nav["nav"].iloc[idx - 1])
                    cur = float(nav["nav"].iloc[idx])
                    daily_ret = (cur / prev) - 1 if prev > 0 else 0.0
                    color = _color_ret(daily_ret)
                    arrow = _arrow(daily_ret)
                    txt.append(d.strftime("%m-%d"), style="bold white")
                    txt.append(f" {arrow} ", style=f"bold {color}")
                    txt.append(f"{_pct(daily_ret, 1)}", style=color)
                else:
                    txt.append(d.strftime("%m-%d"), style="bold white")
                    txt.append(" ─", style=DIM)
            else:
                txt.append(d.strftime("%m-%d"), style="dim")
    return Panel(txt, title="[bold]调仓时间线[/bold]", title_align="left",
                 box=ROUNDED, padding=(0, 1))


# ===== 净值曲线 =====
def _nav_panel(rep: PerformanceReport, nav: pd.DataFrame) -> Panel:
    if nav.empty or "nav" not in nav.columns:
        return Panel("(no nav data)", title="[bold]净值曲线[/bold]", box=ROUNDED)

    s = nav["nav"]
    try:
        tw = shutil.get_terminal_size().columns
    except Exception:
        tw = 100
    width = min(80, max(40, tw - 30))
    spark = _sparkline(s, width=width, height=10)

    txt = Text()
    for line in spark:
        txt.append(line + "\n", style="cyan")
    txt.append("\n")
    ret_color = _color_ret(rep.total_return)
    txt.append("累计 ", style=DIM)
    txt.append(_pct(rep.total_return), style=f"bold {ret_color}")
    base = s.iloc[0] if s.iloc[0] > 0 else 1.0
    txt.append("   区间高 ", style=DIM)
    txt.append(_pct(s.max() / base - 1), style="red")
    txt.append("   区间低 ", style=DIM)
    txt.append(_pct(s.min() / base - 1), style="green")

    return Panel(txt, title="[bold cyan]净值曲线[/bold cyan]", title_align="left",
                 box=HEAVY, padding=(0, 1))


# ===== 主入口 =====
def render_report(
    rep: PerformanceReport,
    nav: pd.DataFrame,
    console: Console | None = None,
    daily_logs: list | None = None,
) -> None:
    """把 PerformanceReport 渲染成同花顺风格的 rich 输出。"""
    if console is None:
        console = Console()

    if daily_logs is not None:
        rep._daily_logs = daily_logs  # type: ignore[attr-defined]

    # 顶部
    console.print(Panel(
        _header_text(rep),
        box=HEAVY,
        style="bold",
        padding=(0, 2),
    ))

    # 净值曲线
    console.print(_nav_panel(rep, nav))

    # 关键指标 + 交易统计（两列）
    console.print(Columns([_metrics_table(rep), _trades_table(rep)], equal=True, expand=True))

    # 年度收益
    console.print(_yearly_table(rep))

    # 调仓时间线
    console.print(_rebalance_timeline(rep, nav))


def render_text(
    rep: PerformanceReport,
    nav: pd.DataFrame,
    daily_logs: list | None = None,
) -> str:
    """返回纯文本版本（适合 CI / 沙箱 / 写文件）。"""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, legacy_windows=False)
    render_report(rep, nav, console=console, daily_logs=daily_logs)
    return buf.getvalue()
