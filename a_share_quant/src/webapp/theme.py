"""Streamlit 表现层：研究工作台的信息层级、状态与可访问性。"""

from __future__ import annotations


APP_CSS = """
<style>
:root { color-scheme: dark; }
[data-testid="stAppViewContainer"] {
  background: radial-gradient(ellipse 68rem 34rem at 88% -12%, rgba(37, 99, 235, .18), transparent 58%), #080d17;
  color: #f4f7fb;
}
[data-testid="stHeader"] { background: rgba(8, 13, 23, .72); backdrop-filter: blur(20px) saturate(145%); }
[data-testid="stSidebar"] { background: linear-gradient(180deg, #10192a 0%, #09111e 62%, #070d17 100%); border-right: 1px solid rgba(148, 163, 184, .14); }
[data-testid="stSidebar"] > div:first-child { padding: 1rem .72rem 2rem; }
.block-container { max-width: 1440px; padding-top: 1.55rem; padding-bottom: 3.5rem; }

/* Type and quiet chrome */
h1, h2, h3 { font-optical-sizing: auto; }
.pos { color: #ff8179; font-weight: 730; }
.neg { color: #58d8ad; font-weight: 730; }
.neu { color: #a8b8ce; }
.sidebar-kicker, .eyebrow, .nav-label { color: #7dd3fc; font-size: .68rem; font-weight: 780; letter-spacing: .14em; text-transform: uppercase; }
.sidebar-title { margin: .25rem 0 .45rem; color: #f8fbff; font-size: 1.16rem; font-weight: 760; letter-spacing: -.025em; }
.sidebar-copy { color: #8fa2bc; font-size: .78rem; line-height: 1.5; }
[data-testid="stSidebar"] [data-testid="stExpander"] { border: 1px solid rgba(148, 163, 184, .14); border-radius: 12px; background: rgba(17, 29, 48, .48); margin: .58rem 0; overflow: hidden; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary { color: #dce7f6; font-size: .86rem; font-weight: 680; }
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] { padding-top: .32rem; }

/* App identity */
.app-hero { display: flex; justify-content: space-between; align-items: end; gap: 1.2rem; margin: 0; padding: 1.55rem 1.7rem; border: 1px solid rgba(148, 163, 184, .18); border-radius: 22px; background: linear-gradient(128deg, rgba(27, 47, 80, .94), rgba(14, 23, 42, .84) 58%, rgba(9, 15, 27, .86)); box-shadow: inset 0 1px rgba(255,255,255,.06), 0 24px 62px rgba(0,0,0,.18); }
.app-kicker { color: #8be9d0; font-size: .7rem; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }
.app-hero h1 { margin: .38rem 0 0; color: #f8fbff; font-size: clamp(1.8rem, 3vw, 2.65rem); line-height: 1.04; letter-spacing: -.045em; }
.app-hero p { max-width: 36rem; margin: .55rem 0 0; color: #afbdd0; font-size: .92rem; line-height: 1.5; }
.hero-status { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .5rem; }
.data-pill, .boundary-pill { display: inline-flex; align-items: center; min-height: 2rem; padding: .42rem .72rem; border-radius: 999px; font-size: .76rem; font-weight: 720; white-space: nowrap; }
.data-pill { border: 1px solid rgba(103, 232, 208, .28); color: #c6faeb; background: rgba(15, 103, 91, .28); }
.data-pill.synthetic { border-color: rgba(251, 191, 36, .32); color: #fde7a1; background: rgba(120, 78, 17, .28); }
.boundary-pill { border: 1px solid rgba(148, 163, 184, .22); color: #c7d3e4; background: rgba(24, 36, 57, .56); }

/* Navigation and hierarchy */
.nav-context { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin: 1.3rem 0 .48rem; }
.nav-context p { margin: 0; color: #899bb4; font-size: .8rem; }
[data-testid="stRadio"] > div { gap: .45rem; padding: .35rem; border: 1px solid rgba(148, 163, 184, .15); border-radius: 14px; background: rgba(14, 24, 40, .62); }
[data-testid="stRadio"] label { min-height: 2.35rem; margin: 0 !important; padding: 0 .84rem; border-radius: 10px; color: #94a6bf; transition: background 120ms ease-out, color 120ms ease-out, transform 120ms ease-out; }
[data-testid="stRadio"] label:has(input:checked) { color: #effaff; background: linear-gradient(135deg, rgba(30, 105, 126, .72), rgba(30, 69, 116, .72)); box-shadow: inset 0 1px rgba(255,255,255,.1); }
[data-testid="stRadio"] label:active { transform: scale(.98); }
[data-testid="stRadio"] label > div:first-child { display: none; }
[data-testid="stRadio"] label p { font-size: .84rem; font-weight: 690; }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; margin: 2.25rem 0 .9rem; }
.section-heading h2 { margin: .28rem 0 0; color: #eef5ff; font-size: clamp(1.1rem, 2vw, 1.36rem); font-weight: 735; line-height: 1.12; letter-spacing: -.025em; }
.section-heading p { max-width: 30rem; margin: 0; color: #8ea1bb; font-size: .82rem; line-height: 1.45; text-align: right; }

/* Result materials */
.kpi-card, .research-card, .table-card, .signal-card { border: 1px solid rgba(148, 163, 184, .18); border-radius: 17px; background: linear-gradient(145deg, rgba(30, 48, 77, .88), rgba(12, 20, 35, .91)); box-shadow: inset 0 1px rgba(255,255,255,.045), 0 12px 28px rgba(0,0,0,.12); }
.kpi-card { min-height: 138px; padding: 1.1rem 1.18rem; }
.kpi-label { color: #91a5bf; font-size: .68rem; font-weight: 760; letter-spacing: .1em; text-transform: uppercase; margin-bottom: .58rem; }
.kpi-value { color: #f6fbff; font-size: clamp(1.52rem, 2.35vw, 2.12rem); font-weight: 780; line-height: 1.05; letter-spacing: -.045em; }
.kpi-sub { color: #8799b2; font-size: .78rem; line-height: 1.4; margin-top: .6rem; }
.research-card { padding: 1.25rem; background: linear-gradient(150deg, rgba(22, 58, 73, .72), rgba(12, 24, 39, .95)); }
.research-label { color: #8be9d0; font-size: .68rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.research-state { margin: .48rem 0 .35rem; color: #f2fbff; font-size: 1.32rem; font-weight: 760; letter-spacing: -.03em; }
.research-copy { margin: 0; color: #a8b8c9; font-size: .84rem; line-height: 1.52; }
.research-meta { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .9rem; }
.research-meta span { padding: .3rem .48rem; border-radius: 7px; color: #c4d2e3; background: rgba(148, 163, 184, .12); font-size: .72rem; }
.table-card { padding: 1rem 1.05rem .7rem; }
.table-card .table-card-title { color: #dbe8f8; font-size: .83rem; font-weight: 730; margin-bottom: .64rem; }
.signal-stack { display: grid; gap: .55rem; }
.signal-card { display: flex; align-items: flex-start; gap: .68rem; padding: .78rem .85rem; background: rgba(20, 30, 48, .76); }
.signal-card.error { border-color: rgba(248, 113, 113, .3); }
.signal-card.warning { border-color: rgba(251, 191, 36, .25); }
.signal-card.ok { border-color: rgba(74, 222, 128, .24); }
.signal-icon { font-size: .95rem; line-height: 1.2; }
.signal-copy { color: #c7d3e3; font-size: .82rem; line-height: 1.45; }
.boundary-card { margin-top: 1.1rem; padding: 1rem 1.1rem; border-left: 3px solid #fbbf24; border-radius: 0 14px 14px 0; background: rgba(120, 78, 17, .17); color: #e8d8ae; font-size: .84rem; line-height: 1.55; }

/* Controls, tables and direct feedback */
[data-testid="stButton"] > button, [data-testid="stDownloadButton"] > button { min-height: 2.35rem; border-radius: 10px; border-color: rgba(103, 232, 208, .38); color: #dcfff6; background: rgba(23, 83, 83, .42); transition: transform 100ms ease-out, background 120ms ease-out, border-color 120ms ease-out; }
[data-testid="stButton"] > button:hover, [data-testid="stDownloadButton"] > button:hover { border-color: rgba(139, 233, 208, .72); background: rgba(27, 105, 100, .58); }
[data-testid="stButton"] > button:active, [data-testid="stDownloadButton"] > button:active { transform: scale(.98); background: rgba(34, 130, 121, .72); }
[data-testid="stMetric"] { padding: 1rem; border: 1px solid rgba(148, 163, 184, .17); border-radius: 14px; background: rgba(18, 31, 51, .7); }
[data-testid="stDataFrame"], [data-testid="stTable"] { border-radius: 14px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th { color: #91a6c0; font-size: .72rem; font-weight: 720; letter-spacing: .06em; text-transform: uppercase; }
td, th { padding: .58rem .45rem; border-bottom: 1px solid rgba(148, 163, 184, .12); }
td { color: #d7e1ef; font-size: .83rem; }

@media (max-width: 820px) {
  .block-container { padding: 1.1rem .85rem 2.2rem; }
  .app-hero, .nav-context, .section-heading { align-items: flex-start; flex-direction: column; }
  .hero-status { justify-content: flex-start; }
  .section-heading p { text-align: left; }
  [data-testid="stRadio"] > div { flex-wrap: wrap; }
  [data-testid="stRadio"] label { padding: 0 .68rem; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; }
}
@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
  [data-testid="stHeader"], .app-hero, .kpi-card, .research-card, .table-card, .signal-card { backdrop-filter: none; background: #101a2b; border-color: rgba(203, 213, 225, .42); }
}
</style>
"""


def apply_theme(st) -> None:
    """注入一次全局样式；页面函数仅负责数据与内容。"""
    st.markdown(APP_CSS, unsafe_allow_html=True)


def render_app_header(st, *, real_data_available: bool, using_real_data: bool) -> None:
    """显示应用身份、数据来源和研究边界。"""
    if using_real_data:
        status = "真实数据 · 当前运行"
        state_class = ""
    elif real_data_available:
        status = "合成数据 · 可切换真实数据"
        state_class = "synthetic"
    else:
        status = "合成数据 · 未检测到本地真实数据"
        state_class = "synthetic"
    st.markdown(
        f'''<section class="app-hero"><div><div class="app-kicker">Research workbench · V1</div><h1>沪深策略研究台</h1><p>把数据质量、回撤与稳健性放在收益数字之前。</p></div><div class="hero-status"><div class="data-pill {state_class}">{status}</div><div class="boundary-pill">研究用途 · 不连接券商</div></div></section>''',
        unsafe_allow_html=True,
    )


def render_section_heading(st, eyebrow: str, title: str, copy: str = "") -> None:
    """统一工作区内的标题层级，避免靠分割线堆砌页面。"""
    description = f"<p>{copy}</p>" if copy else ""
    st.markdown(
        f'''<section class="section-heading"><div><div class="eyebrow">{eyebrow}</div><h2>{title}</h2></div>{description}</section>''',
        unsafe_allow_html=True,
    )
