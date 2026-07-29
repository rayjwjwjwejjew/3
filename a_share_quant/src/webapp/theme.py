"""Streamlit 表现层：统一主题与运行状态头部，避免散落在页面逻辑中。"""

from __future__ import annotations


APP_CSS = """
<style>
:root { color-scheme: dark; }
[data-testid="stAppViewContainer"] { background: radial-gradient(ellipse at 82% -12%, #1b3551 0%, transparent 38rem), #070d18; }
[data-testid="stHeader"] { background: rgba(7, 13, 24, .76); backdrop-filter: blur(18px) saturate(150%); }
[data-testid="stSidebar"] { background: linear-gradient(180deg, #0d1728 0%, #09111f 100%); border-right: 1px solid rgba(148, 163, 184, .14); }
[data-testid="stSidebar"] > div:first-child { padding-top: 1.25rem; }
.block-container { max-width: 1480px; padding-top: 2.1rem; padding-bottom: 3rem; }
.pos { color: #ff6b6b; font-weight: 700; }
.neg { color: #45d6a5; font-weight: 700; }
.neu { color: #9fb0c8; }
.app-hero { display:flex; justify-content:space-between; align-items:end; gap:1.2rem; margin:0 0 1.65rem; padding:1.35rem 1.55rem; border:1px solid rgba(148, 163, 184, .18); border-radius:18px; background:linear-gradient(135deg, rgba(25, 46, 75, .92), rgba(10, 19, 35, .82)); box-shadow:0 24px 60px rgba(0, 0, 0, .18); }
.app-kicker { color:#67e8d0; font-size:.74rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
.app-hero h1 { margin:.35rem 0 0; color:#f8fbff; font-size:clamp(1.65rem, 3vw, 2.45rem); line-height:1.05; letter-spacing:-.035em; }
.app-hero p { margin:.5rem 0 0; color:#aabbd2; font-size:.92rem; }
.data-pill { flex:0 0 auto; padding:.55rem .8rem; border:1px solid rgba(103, 232, 208, .25); border-radius:999px; color:#bff9e9; background:rgba(16, 78, 79, .34); font-size:.82rem; font-weight:700; }
.data-pill.synthetic { border-color:rgba(251, 191, 36, .30); color:#fde68a; background:rgba(120, 78, 17, .24); }
.kpi-card { min-height:142px; padding:1.1rem 1.2rem; border:1px solid rgba(148, 163, 184, .20); border-radius:16px; background:linear-gradient(145deg, rgba(31, 48, 78, .96), rgba(11, 20, 37, .96)); box-shadow:inset 0 1px rgba(255,255,255,.05), 0 14px 30px rgba(0,0,0,.16); }
.kpi-label { color:#91a4c1; font-size:.72rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; margin-bottom:.52rem; }
.kpi-value { color:#f7fbff; font-size:clamp(1.55rem, 2.5vw, 2.15rem); font-weight:760; line-height:1.08; letter-spacing:-.035em; }
.kpi-sub { color:#8193ad; font-size:.8rem; margin-top:.58rem; }
.section-title { color:#e8f0fb; font-size:1.02rem; font-weight:740; border-left:3px solid #67e8d0; padding-left:.72rem; margin:2.1rem 0 .85rem; letter-spacing:-.01em; }
[data-testid="stButton"] > button, [data-testid="stDownloadButton"] > button { border-radius:10px; border-color:rgba(103,232,208,.42); color:#d9fff7; background:rgba(23, 83, 83, .45); transition:transform 120ms ease-out, background 120ms ease-out; }
[data-testid="stButton"] > button:active, [data-testid="stDownloadButton"] > button:active { transform:scale(.98); background:rgba(34, 113, 108, .72); }
@media (max-width: 720px) { .block-container { padding:1.25rem .9rem 2rem; } .app-hero { align-items:flex-start; flex-direction:column; padding:1.1rem; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior:auto !important; transition:none !important; animation:none !important; } }
</style>
"""


def apply_theme(st) -> None:
    """注入一次全局样式；页面函数仅负责数据与内容。"""
    st.markdown(APP_CSS, unsafe_allow_html=True)


def render_app_header(st, *, real_data_available: bool, using_real_data: bool) -> None:
    """显示应用身份与当前数据来源，避免合成/真实数据混淆。"""
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
        f'''<section class="app-hero"><div><div class="app-kicker">Research workbench · V1</div><h1>沪深策略研究台</h1><p>先验证数据与稳健性，再讨论收益。</p></div><div class="data-pill {state_class}">{status}</div></section>''',
        unsafe_allow_html=True,
    )
