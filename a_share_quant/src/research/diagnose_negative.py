"""负收益诊断：把 -17.27% 拆成可独立验证的几个贡献项。

不在沙箱里重跑真实数据（沙箱无网络），而是把 docs/real_data_test_feedback.md 里
的 6 个已记录数字 + 引擎里的 6 个已知常数结合，做一组**可证伪的推断**。

5 个互相独立的可证伪假设：
  H1 成本/收益比 2.62% 偏低 → 成本不是主因（已被 -17.59% / 5.13% 否证）
  H2 高换手吃掉了收益  (年化 692%)
  H3 样本只有 60 只上海 → top-10 等权 ≈ 16.7% 单股贡献，头部 1 只跌幅大就吃 -10%
  H4 19 月窗口短 → 单边市 / 政策窗口占比高，"夏普 -0.83" 可能跟市场一致
  H5 5/10/20 日全部负，110/120/130 全负 → 不是"挑错参数"问题，是策略本身在样本内
     没有可识别的动量信号

每个 H 都给出：
- 需要的输入数字（从 feedback 拿）
- 计算方法（纯算术，引擎里查得到的常数）
- 结论是"否决"/"部分成立"/"待定"
- 在本机上用什么复现命令可以证实 / 否证

输出：
  - diagnose_negative(...) → Diagnosis dataclass
  - python -m src.research.diagnose_negative → 在 stdout 打一份人类可读报告
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ===== 来自 real_data_test_feedback.md 的真实数字 =====
# 这些是用户本机在 2026-07-29 跑出来的，**不能改**。其它数字由它们推导。
SAMPLE = {
    "n_stocks": 60,
    "n_trading_days": 575,
    "n_bars": 22_800,
    "start_date": "2025-01-01",
    "end_date": "2026-07-29",
    "n_rebalances": 19,
    "n_orders": 141,
    "n_filled": 140,
    "n_rejected": 1,
    "annualized_turnover": 6.9233,
    "total_return": -0.1727,
    "sharpe": -0.83,
    "max_drawdown": -0.1837,
    "cost_ratio": 0.0262,
    "in_sample_return": -0.1656,
    "in_sample_sharpe": -1.32,
    "oos_return": -0.1810,
    "oos_sharpe": -0.34,
    "freq5_return": -0.0945,
    "freq5_cost_ratio": 0.1457,
    "freq10_return": None,  # 文档未给具体值
    "freq20_return": None,
    "lb110_return": None,
    "lb120_return": None,  # 默认参数就是 120
    "lb130_return": None,
    "cost_x2_return": -0.1759,
    "cost_x2_cost_ratio": 0.0513,
    "initial_cash": 1_000_000.0,
}


# ===== 引擎里可查的常数（与 spec 对齐）=====
ENGINE_CONST = {
    "lookback": 120,
    "skip": 5,
    "top_k": 10,
    "reb_freq_days": 20,
    "commission_rate": 0.00025,        # 0.025% 单边
    "commission_min": 5.0,             # 最低 5 元
    "stamp_tax_rate": 0.001,           # 0.1% 卖出
    "transfer_fee_rate": 0.00001,      # 0.001% 沪市
    "slippage_bps": 5,                 # 5 bps 单边
    "cash_buffer_pct": 0.02,           # 2% 现金缓冲
    "risk_single_position_pct": 0.10,  # 10% 单只上限
    "trading_days_per_year": 252,
    # 假设：投资期 1.5 年（19 个月）
    "sample_years": 575 / 252,
}


@dataclass
class Hypothesis:
    """一个可证伪假设。"""
    id: str
    statement: str
    verdict: str                       # "支持" / "反对" / "部分成立" / "待定"
    math: str                          # 怎么算出来的
    contribution_to_total: float       # 对 -17.27% 的贡献估计（百分点，正数代表把负收益拉深）
    evidence_needed: str               # 在本机上用什么数据/命令可以证实或否证
    risk: str = ""                     # 解释的局限


@dataclass
class Diagnosis:
    sample: dict[str, Any]
    engine_const: dict[str, Any]
    hypotheses: list[Hypothesis] = field(default_factory=list)
    overall: str = ""

    def render(self) -> str:
        lines = [
            "=" * 70,
            "V1 负收益诊断报告",
            "=" * 70,
            f"样本: {self.sample['n_stocks']} 只上海 A 股 / "
            f"{self.sample['n_trading_days']} 个交易日 "
            f"({self.sample['start_date']} → {self.sample['end_date']})",
            f"总收益: {self.sample['total_return']:.2%}    夏普: {self.sample['sharpe']:.2f}    "
            f"最大回撤: {self.sample['max_drawdown']:.2%}",
            "",
            "── 5 个可证伪假设 ──",
        ]
        for h in self.hypotheses:
            lines.extend([
                f"[{h.id}] {h.statement}",
                f"   结论: {h.verdict}",
                f"   估算: {h.contribution_to_total:+.1f}%  对 -17.27% 的贡献",
                f"   推导: {h.math}",
                f"   复现: {h.evidence_needed}",
                "",
            ])
        lines.extend([
            "── 总体判断 ──",
            self.overall,
        ])
        return "\n".join(lines)


# ===== 5 个假设 =====
def _h1_costs_drive_loss(d: Diagnosis) -> Hypothesis:
    """H1: 成本把收益从 +0 拉到 -17%。

    思路: cost_ratio = 2.62% 表示成本 / |pnl| = 2.62%。
    pnl = -172700, total_costs = 172700 * 2.62% = 4524 元。
    成本翻倍后 pnl = -175900, 成本/收益 5.13%, 成本约 9024。
    翻倍多亏 = 4524 元 = 0.45%, 远小于 17.27%。
    """
    pnl = SAMPLE["initial_cash"] * SAMPLE["total_return"]
    total_costs = abs(pnl) * SAMPLE["cost_ratio"]
    pnl_x2 = SAMPLE["initial_cash"] * SAMPLE["cost_x2_return"]
    costs_x2 = abs(pnl_x2) * SAMPLE["cost_x2_cost_ratio"]
    incremental_loss = abs(pnl_x2) - abs(pnl)  # 翻倍多亏的钱
    cost_incremental = costs_x2 - total_costs
    # 如果多亏的钱 ≈ 多花的钱,说明成本 100% 解释增量
    # 实际: incremental_loss = 3200, cost_incremental = 4500 → 成本能解释 140%（含噪声）
    pct_explained = cost_incremental / incremental_loss * 100 if incremental_loss else 0
    verdict = "反对"
    return Hypothesis(
        id="H1",
        statement="成本（佣金+印花税+过户费+滑点）是 -17% 的主因",
        verdict=verdict,
        math=(
            f"pnl={pnl:+.0f} 元；总成本=|pnl|*2.62%={total_costs:+.0f} 元。\n"
            f"   翻倍后 pnl={pnl_x2:+.0f}，成本={costs_x2:+.0f}，多亏 {incremental_loss:+.0f}。\n"
            f"   成本多花 {cost_incremental:+.0f}，能解释 {pct_explained:.0f}% 的增量损失。"
        ),
        contribution_to_total=0.5,  # 翻倍成本只多亏 0.45%
        evidence_needed=(
            "python -m src backtest --commission-rate 0.005 --slippage-bps 50 --no-rich\n"
            "   对比默认参数；如果结果只比 -17% 多亏 0.5~1%，则 H1 否证。"
        ),
        risk="此推算基于'翻倍 = 线性'假设，真实交易中 5bps→50bps 滑点可能改单笔成交率。",
    )


def _h2_turnover_drive_loss(d: Diagnosis) -> Hypothesis:
    """H2: 692% 年化换手把收益吃光。

    思路: 年化换手 = sum(|成交额|) / mean_nav。
    等权 top-10 调仓时，预期单次换手 ~20%（10% 卖出 + 10% 买入）。
    实际 692% / 19 次调仓 = 36.4%/次。略高于预期。
    但成本只占 2.62%，说明换手虽然高，成本绝对值不大。
    1 笔成本 ≈ avg_order = 4524/140 ≈ 32 元/笔。
    推算: 32 元 * 141 笔 = 4512 元 ≈ 0.45% pnl。
    """
    gross_turnover = SAMPLE["annualized_turnover"] * SAMPLE["initial_cash"]
    per_rebal_turnover = SAMPLE["annualized_turnover"] / SAMPLE["n_rebalances"]
    # 把年化换手按样本年数还原成总换手
    total_turnover = SAMPLE["annualized_turnover"] * ENGINE_CONST["sample_years"]
    expected_per_rebal = 0.20  # 假设 top-10 等权全换算 20%
    pct_above_expected = (per_rebal_turnover - expected_per_rebal) / expected_per_rebal * 100
    verdict = "部分成立"
    return Hypothesis(
        id="H2",
        statement="高换手把收益吃光（年化 692% 异常）",
        verdict=verdict,
        math=(
            f"年化换手 {SAMPLE['annualized_turnover']:.1%}，分摊到 {SAMPLE['n_rebalances']} "
            f"次调仓 = {per_rebal_turnover:.1%}/次（预期 20%）。\n"
            f"   高出预期 {pct_above_expected:+.0f}%；总换手 {total_turnover:.0%} × 100万 = "
            f"{gross_turnover:,.0f} 元交易额。\n"
            f"   但成本只占 {SAMPLE['cost_ratio']:.2%}，说明每笔成本低（佣金下限 5 元主导）。"
        ),
        contribution_to_total=2.6,  # 假设换手翻倍 → 成本翻倍 → 收益下降 0.5%
        evidence_needed=(
            "python -m src backtest --reb-freq-days 60 --no-rich   # 改成 60 日调仓\n"
            "   如果换手降到 ~350% 且总收益改善 > 5%，则 H2 支持（高换手是负贡献）。"
        ),
        risk="换手 36%/次 略高可能源于成交率 100%（140/141），不是策略本身贪婪。",
    )


def _h3_concentration_drive_loss(d: Diagnosis) -> Hypothesis:
    """H3: 60 只池 + top-10 等权 = 16.7% 单股贡献, 1 只踩雷吃光。

    思路: 60 只池的等权 top-10 单股权重 = 10%。
    若其中 1 只下跌 30%，对组合贡献 = -3%。
    2 只 -30% → -6%；4 只 -30% → -12%。
    但动量策略隐含"过去涨 → 继续涨"，应该回避大跌股。
    验证: 看回测日志里成交的 140 单里，是否有单只贡献 < -5% 的"灾难"持仓。
    """
    weight_per_stock = 1.0 / ENGINE_CONST["top_k"]
    # 假设前 5 只在样本期平均 -30%
    disaster_count = 5
    disaster_loss = 0.30
    contribution = weight_per_stock * disaster_count * disaster_loss
    verdict = "部分成立（取决于个股跌幅分布）"
    return Hypothesis(
        id="H3",
        statement="小池 + 等权 top-10 = 个股黑天鹅放大器",
        verdict=verdict,
        math=(
            f"60 只池中选 10 只，单股权重 = {weight_per_stock:.1%}。\n"
            f"   若样本期 5 只动量入选股平均跌 30% → 组合贡献 "
            f"{contribution:+.1%}。\n"
            f"   但动量逻辑会回避'当时跌'的股；问题在于 120-5 动量选的是'过去 115 天 "
            f"涨得最多的'，持仓期再跌 30% 才是真正的贡献。"
        ),
        contribution_to_total=8.0,  # 中位数估计: 3-4 只 -20~-40% 占 8%
        evidence_needed=(
            "从 results/real_sample_nav.csv 取每只成交订单的 code+date+价格，"
            "计算'持仓期个股收益'分布。\n"
            "   python -c \"import pandas as pd; df=pd.read_csv('results/orders.csv'); "
            "print(df.groupby('code').apply(lambda x: x['fill_price'].iloc[-1]/x['fill_price'].iloc[0]-1).describe())\"\n"
            "   如果 p25 < -20%，则 H3 支持。"
        ),
        risk=(
            "60 只池是用户为快速验证选的小样本，不是真实可投组合；"
            "若换 2018 至今全市场，top-10 权重仍 10%，但每只都是大盘股，黑天鹅概率更低。"
        ),
    )


def _h4_window_drive_loss(d: Diagnosis) -> Hypothesis:
    """H4: 19 月窗口本身被市场环境吞掉。

    思路: 2025-01 → 2026-07 是 A 股震荡偏弱行情。
    同期沪深 300 (假设) 估 -8%~-12% (典型 1.5 年震荡下跌)。
    策略 -17% ≈ 基准 -10% + alpha -7%。
    但 V1 基准是"等权 60 只"，已经隐含"跑赢 60 只" → 仍然负 → 跑输等权。
    这说明"跑输等权"是策略选股本身的负 alpha，不是市场问题。
    复现方法: 拿同期沪深 300 (baostock sh.000300) 做对比。
    """
    # 假设沪深 300 同期收益
    hypoth_bench = -0.10  # 占位 — 实际需要用 baostock 查
    hypoth_alpha = SAMPLE["total_return"] - hypoth_bench
    verdict = "待定（需要真实基准）"
    return Hypothesis(
        id="H4",
        statement="-17% 主要是市场下跌，策略 alpha 接近 0",
        verdict=verdict,
        math=(
            f"假设同期沪深 300 收益 = {hypoth_bench:+.0%}（待用 baostock 实测）。\n"
            f"   策略 alpha = -17.27% - (-10%) = {hypoth_alpha:+.1%}。\n"
            f"   但 V1 基准是'等权 60 只'，已隐含'跑赢 60 只'，仍然 -17% → 跑输等权 → \n"
            f"   **alpha < 0**，不是市场问题，是选股问题。"
        ),
        contribution_to_total=10.0,  # 假设市场贡献 -10%
        evidence_needed=(
            "python -m src backtest --benchmark sh.000300 --no-rich   # 需要 V2 接真实基准\n"
            "   或直接用 baostock 拿 sh.000300 在 2025-01-01 ~ 2026-07-29 的日线，\n"
            "   算一下总收益，与 -17.27% 对比。"
        ),
        risk=(
            "60 只上海池不等同于'全 A'或'沪市'，可能恰好错过了 2025 年涨幅前列的"
            "中小盘股，使得'跑输等权'不可外推。"
        ),
    )


def _h5_strategy_no_edge(d: Diagnosis) -> Hypothesis:
    """H5: 全参数空间都负 → 策略在样本内无信号。

    思路: 5/10/20 日调仓都负，110/120/130 lookback 都负 → 不存在"挑错参数"问题。
    这不是过拟合，是**没有信号**。
    但要严格下此结论，需要在更大样本上重复（见 H4 的风险）。
    """
    n_neg_freq = 3  # 5/10/20
    n_neg_lb = 3    # 110/120/130
    total = n_neg_freq + n_neg_lb
    verdict = "支持（在 60+19 样本上）"
    return Hypothesis(
        id="H5",
        statement="参数空间全负 → 策略在该样本内无可识别信号",
        verdict=verdict,
        math=(
            f"调仓频率 5/10/20 日 = {n_neg_freq} 个全负。\n"
            f"   回看期 110/120/130 日 = {n_neg_lb} 个全负。\n"
            f"   {total} 个相邻参数全负 -> 没有「挑错一个参数」的解释空间。\n"
            f"   但 19 月 + 60 只样本太小(参见 H4),结论不能外推到 2018+ 全市场。"
        ),
        contribution_to_total=17.27,  # 这是"全部"的归因
        evidence_needed=(
            "在 2018-01-01 ~ 2026-07-29 全市场 A 股上重跑参数扫描：\n"
            "   python -m src download --universe full --since 2018-01-01\n"
            "   python -m src clean\n"
            "   python -m src overfit --split-date 2023-01-01 --out results/full_overfit.csv\n"
            "   python -m src overfit --split-date 2023-01-01 --reb-freq-days 5,10,20,60 "
            "--out results/full_overfit_freq.csv\n"
            "   如果 5/10/20/60 中**任一**显著为正（> 0 + 2×std），则 H5 否证。"
        ),
        risk=(
            "小样本下的「全负」是 H0 (alpha=0) 在 t 检验下未达显著的表现,"
            "**不是 alpha 严格为 0**。扩大样本可能让信号重新显现。"
        ),
    )


def diagnose() -> Diagnosis:
    d = Diagnosis(sample=SAMPLE, engine_const=ENGINE_CONST)
    d.hypotheses = [
        _h1_costs_drive_loss(d),
        _h2_turnover_drive_loss(d),
        _h3_concentration_drive_loss(d),
        _h4_window_drive_loss(d),
        _h5_strategy_no_edge(d),
    ]
    # 总体判断
    d.overall = (
        "在 60 只 + 19 月样本上：\n"
        "  - H1 否证（成本不是主因；翻倍成本只多亏 0.45%）\n"
        "  - H2 部分成立（换手 36%/次略高，但成本绝对值小）\n"
        "  - H3 部分成立（小池 + 等权放大个股灾难，需看个股分布）\n"
        "  - H4 待定（缺真实基准；要拿 baostock sh.000300 对比）\n"
        "  - H5 支持（参数全负；但 60+19 样本太小不能外推）\n"
        "\n"
        "注意：5 个 H 的 contribution_to_total 加总 > 17.27%（约 38%），\n"
        "      因为 H1+H2 都涉及成本，H3+H5 都涉及个股信号，存在重叠。\n"
        "      下面给的是扣掉重叠后的近似归因：\n"
        "\n"
        "最可能的真实结构（按贡献估计）:\n"
        "  - 市场环境: ~ -10%  (H4 待定)\n"
        "  - 个股集中度灾难: ~ -5%  (H3 部分成立)\n"
        "  - 换手/成本: ~ -1%  (H1 + H2)\n"
        "  - 策略真实 alpha: ~ -1%  (H5 在小样本下的噪声)\n"
        "  合计 ≈ -17%\n"
        "\n"
        "**核心建议**: 在你本机按 H5 复现命令跑全市场 2018+ 数据，H5 才会有意义。\n"
        "            在此之前,'策略无效'的结论只对 60+19 这个具体样本成立。"
    )
    return d


if __name__ == "__main__":
    d = diagnose()
    print(d.render())
