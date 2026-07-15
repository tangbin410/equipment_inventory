"""机房资产盘点系统 - PC 后台管理端。"""

from __future__ import annotations

import io
import json
import secrets
import time
import zipfile
from datetime import datetime, timedelta
from html import escape
import openpyxl
import pandas as pd
import requests
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

API_BASE = "http://localhost:8000/api"
REQUEST_TIMEOUT = 10
PC_LOGIN_KEEP_SECONDS = 15 * 60
PC_SESSION_QUERY_KEY = "pc_session"

CENTER_HEADER_COMPONENT = JsCode(
    """
class CenterHeaderComponent {
    init(params) {
        this.eGui = document.createElement('div');
        this.eGui.className = 'centered-header-title';
        this.eGui.textContent = params.displayName || '';
    }

    getGui() {
        return this.eGui;
    }

    refresh(params) {
        this.eGui.textContent = params.displayName || '';
        return true;
    }
}
"""
)

PAGE_UPLOAD = "上传任务清单"
PAGE_TASKS = "任务看板"
PAGE_REPORT = "盘点报告"
PAGE_USERS = "账号管理"

SIDEBAR_PAGE_LABEL = {
    PAGE_UPLOAD: "上传任务清单",
    PAGE_TASKS: "任务看板总览",
    PAGE_REPORT: "盘点报告分析",
    PAGE_USERS: "小程序账号管理",
}

STATUS_TEXT = {
    "pending": "待领取",
    "doing": "盘点中",
    "done": "现场完成",
}

PAGE_META = {
    PAGE_UPLOAD: ("📤", "上传任务清单", "导入包含多个机房的 Excel，系统自动按片区生成待领取任务。"),
    PAGE_TASKS: ("📋", "任务看板", "查看各片区机房领取与盘点进度，支持删除不需要继续盘点的机房任务。"),
    PAGE_REPORT: ("📊", "盘点报告", "按机房汇总盘点结果，快速定位未扫和型号不一致设备。"),
    PAGE_USERS: ("👤", "账号管理", "维护小程序登录账号、负责片区和启停状态，便于现场人员按片区领取任务。"),
}


st.set_page_config(page_title="机房资产盘点", layout="wide", page_icon="📡")

st.markdown(
    """
<style>
:root {
    --bg: #f3f7fd;
    --panel: #ffffff;
    --panel-soft: #f8fbff;
    --line: #d7e5f7;
    --line-strong: #9dc4ee;
    --text: #17233c;
    --muted: #62758d;
    --primary: #1667c7;
    --primary-dark: #0b4f9f;
    --primary-soft: #e8f2ff;
    --sidebar: #092b57;
    --sidebar-soft: #0f4586;
    --success: #15945f;
    --warning: #d97706;
    --danger: #d64545;
}

html, body, [class*="css"] {
    font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    font-size: 15px;
    color: var(--text);
}

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(circle at 78% 0%, rgba(55, 129, 217, 0.12), transparent 28rem),
        linear-gradient(180deg, #edf5ff 0%, #f6f9fd 20rem, #f3f7fd 100%),
        var(--bg);
}
[data-testid="stHeader"] { display: none; }
[data-testid="stSidebar"] {
    background:
        linear-gradient(180deg, rgba(24, 103, 191, 0.18), transparent 42%),
        linear-gradient(180deg, var(--sidebar) 0%, #0b376e 54%, #08264f 100%);
    border-right: 1px solid rgba(255,255,255,0.12);
    box-shadow: 12px 0 34px rgba(9, 43, 87, 0.14);
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.65rem; }
.main .block-container {
    max-width: 1460px;
    padding: 1.35rem 2rem 2.4rem;
}

h1, h2, h3, p { letter-spacing: 0; }
.app-logo {
    padding: 0.6rem 0.25rem 1.15rem;
    border-bottom: 1px solid rgba(255,255,255,0.16);
    margin-bottom: 0.95rem;
}
.app-logo-title {
    display: block;
    color: #ffffff;
    font-size: 1.72rem !important;
    line-height: 1.3;
    font-weight: 800;
    letter-spacing: 0.02em;
}
.app-logo-sub {
    display: block;
    margin-top: 0.35rem;
    color: rgba(224, 239, 255, 0.82);
    font-size: 0.88rem;
}

[data-testid="stSidebar"] [role="radiogroup"] {
    gap: 0.45rem;
    width: 100%;
}
[data-testid="stSidebar"] label[data-baseweb="radio"] {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px;
    padding: 0.72rem 0.82rem;
    margin-bottom: 0.45rem;
    width: 100%;
    min-width: 12.4rem;
    box-sizing: border-box;
    font-size: 1.06rem !important;
    color: rgba(255,255,255,0.92) !important;
    transition: all 0.16s ease;
}
[data-testid="stSidebar"] label[data-baseweb="radio"] > div:last-child {
    flex: 1;
}
[data-testid="stSidebar"] label[data-baseweb="radio"]:hover {
    background: rgba(255,255,255,0.13);
    border-color: rgba(133, 190, 255, 0.58);
    transform: translateX(2px);
}
[data-testid="stSidebar"] label[data-baseweb="radio"] p {
    font-size: 1.06rem !important;
    line-height: 1.35 !important;
    width: 9.6rem;
    margin: 0 !important;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0;
    white-space: nowrap;
    display: inline-block;
    color: rgba(255,255,255,0.93) !important;
}
[data-testid="stSidebar"] [aria-checked="true"] {
    background: linear-gradient(90deg, #1f7ee4 0%, #1667c7 100%) !important;
    border-color: rgba(159, 207, 255, 0.86) !important;
    box-shadow: 0 10px 24px rgba(0, 80, 170, 0.28);
}
[data-testid="stSidebar"] [aria-checked="true"] p {
    color: #ffffff !important;
    font-weight: 750 !important;
}
[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {
    background: linear-gradient(90deg, #1f7ee4 0%, #1667c7 100%) !important;
    border-color: rgba(159, 207, 255, 0.86) !important;
    box-shadow: 0 10px 24px rgba(0, 80, 170, 0.28);
}
[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) p {
    color: #ffffff !important;
    font-weight: 750 !important;
}

.button-bottom-align-spacer {
    height: 1.15rem;
}

.page-head {
    display: flex;
    align-items: center;
    gap: 0.9rem;
    background:
        linear-gradient(90deg, rgba(255,255,255,0.98) 0%, rgba(244,249,255,0.98) 58%, rgba(231,242,255,0.92) 100%);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 1.05rem 1.2rem;
    margin-bottom: 1.15rem;
    box-shadow: 0 14px 34px rgba(22, 103, 199, 0.08);
    position: relative;
    overflow: hidden;
}
.page-head::after {
    content: "";
    position: absolute;
    right: -4.2rem;
    top: -5rem;
    width: 15rem;
    height: 15rem;
    border-radius: 999px;
    background: rgba(22, 103, 199, 0.08);
}
.page-icon {
    width: 2.7rem;
    height: 2.7rem;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 12px;
    background: linear-gradient(135deg, #1f7ee4 0%, #0c57b1 100%);
    color: #ffffff;
    font-size: 1.35rem;
    flex: 0 0 auto;
    box-shadow: 0 10px 24px rgba(22, 103, 199, 0.22);
}
.page-title {
    margin: 0;
    color: var(--text);
    font-size: 1.82rem !important;
    font-weight: 800;
    line-height: 1.3;
}
.page-desc {
    margin: 0.28rem 0 0;
    color: var(--muted);
    font-size: 0.92rem;
    line-height: 1.65;
}
.section-title {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    color: #123b69;
    font-size: 1.02rem;
    line-height: 1.4;
    font-weight: 750;
    margin: 1.15rem 0 0.7rem;
    padding-left: 0.5rem;
    border-left: 4px solid var(--primary);
}
.soft-panel {
    background: linear-gradient(180deg, #ffffff 0%, var(--panel-soft) 100%);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 0.95rem 1rem;
    margin-bottom: 1rem;
    box-shadow: 0 10px 26px rgba(22, 103, 199, 0.05);
}
.soft-panel p {
    margin: 0;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.75;
}

[data-testid="stMetric"] {
    background: linear-gradient(180deg, #ffffff 0%, #f9fcff 100%);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 1rem 1.05rem;
    box-shadow: 0 12px 30px rgba(22, 103, 199, 0.07);
    position: relative;
    overflow: hidden;
}
[data-testid="stMetric"]::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    width: 100%;
    height: 3px;
    background: linear-gradient(90deg, var(--primary) 0%, #59a6ff 100%);
}
[data-testid="stMetric"] label {
    color: var(--muted) !important;
    font-size: 0.84rem !important;
    font-weight: 650 !important;
}
[data-testid="stMetricValue"] {
    color: #0b3f7f !important;
    font-size: 1.65rem !important;
    font-weight: 820 !important;
}

.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button, [data-testid="stLinkButton"] a {
    min-height: 2.38rem;
    border-radius: 9px !important;
    border: 1px solid var(--line-strong) !important;
    background: #ffffff !important;
    color: var(--primary-dark) !important;
    font-size: 0.92rem !important;
    font-weight: 700 !important;
    box-shadow: 0 4px 12px rgba(22, 103, 199, 0.06);
}
.stButton > button:hover, .stFormSubmitButton > button:hover, .stDownloadButton > button:hover, [data-testid="stLinkButton"] a:hover {
    background: var(--primary-soft) !important;
    border-color: var(--primary) !important;
    color: var(--primary-dark) !important;
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1d7be0 0%, var(--primary-dark) 100%) !important;
    border-color: var(--primary) !important;
    color: #ffffff !important;
    box-shadow: 0 8px 18px rgba(22, 103, 199, 0.22);
}

[data-testid="stDataFrame"] {
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 12px 28px rgba(22, 103, 199, 0.06);
}
.ag-theme-streamlit {
    --ag-font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    --ag-font-size: 14px;
    --ag-header-background-color: #eaf4ff;
    --ag-header-foreground-color: #123b69;
    --ag-odd-row-background-color: #ffffff;
    --ag-row-hover-color: #eef7ff;
    --ag-selected-row-background-color: #e6f2ff;
    --ag-border-color: #d7e5f7;
    --ag-background-color: #ffffff;
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 12px 28px rgba(22, 103, 199, 0.07);
}
.ag-theme-streamlit .ag-header-cell-label,
.ag-theme-streamlit .ag-cell {
    justify-content: center;
    text-align: center;
}
.ag-theme-streamlit .ag-header-cell-label {
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    width: 100%;
    padding: 0 !important;
}
.ag-theme-streamlit .ag-header-cell-text {
    display: block;
    font-weight: 780;
    text-align: center !important;
    width: 100% !important;
    margin: 0 auto !important;
}
.ag-theme-streamlit .center-header {
    text-align: center !important;
}
.ag-theme-streamlit .ag-header-cell-comp-wrapper,
.ag-theme-streamlit .ag-cell-label-container,
.ag-theme-streamlit .ag-header-cell-text {
    width: 100% !important;
}
.ag-theme-streamlit .ag-header-cell-menu-button,
.ag-theme-streamlit .ag-header-icon {
    position: absolute !important;
    right: 0.35rem !important;
}
.ag-theme-streamlit .ag-floating-filter-input input {
    text-align: center;
    border-radius: 6px;
    border-color: #c7dcf5;
}
[data-testid="stFileUploader"] {
    background: linear-gradient(180deg, #ffffff 0%, var(--panel-soft) 100%);
    border: 1px dashed var(--line-strong);
    border-radius: 12px;
    padding: 0.35rem;
}
[data-testid="stSelectbox"], [data-testid="stTextInput"] {
    font-size: 0.92rem;
}
[data-testid="stSelectbox"] > div, [data-testid="stTextInput"] > div {
    border-radius: 10px;
}
[data-testid="stSelectbox"] > div:focus-within, [data-testid="stTextInput"] > div:focus-within {
    border-color: var(--primary) !important;
    box-shadow: 0 0 0 1px rgba(22, 103, 199, 0.18) !important;
}
[data-testid="InputInstructions"] {
    display: none !important;
}
[data-testid="stAlert"] {
    border-radius: 12px;
    border: 1px solid var(--line);
}
div[data-testid="stHorizontalBlock"] {
    gap: 1rem;
}
.user-guide-card {
    background:
        linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(235,245,255,0.95) 100%);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 0.95rem 1.05rem;
    margin: 0.2rem 0 0.9rem;
    box-shadow: 0 10px 26px rgba(22, 103, 199, 0.06);
}
.user-guide-title {
    color: #123b69;
    font-weight: 780;
    font-size: 0.98rem;
    margin-bottom: 0.35rem;
}
.user-guide-card p {
    color: var(--muted);
    margin: 0;
    line-height: 1.75;
    font-size: 0.9rem;
}
.user-mode-card {
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #ffffff;
    padding: 0.8rem 0.95rem;
    margin: 0.1rem 0 0.85rem;
}
.user-mode-card strong {
    color: var(--primary-dark);
}
.user-mode-card span {
    color: var(--muted);
    font-size: 0.9rem;
}
.user-list-actions {
    display: flex;
    align-items: center;
    min-height: 2.35rem;
    color: var(--muted);
    font-size: 0.9rem;
    line-height: 1.65;
}
.danger-zone {
    border-left: 4px solid var(--danger);
    background: #fff7f7;
    border-radius: 12px;
    padding: 0.75rem 0.95rem;
    color: #7f1d1d;
    line-height: 1.7;
}
.login-wrap {
    padding: 4.2rem 1rem 2rem;
}
.login-hero {
    min-height: 26rem;
    border-radius: 22px;
    padding: 2.2rem 2.25rem;
    color: #ffffff;
    background:
        radial-gradient(circle at 88% 18%, rgba(96, 182, 255, 0.38), transparent 12rem),
        linear-gradient(135deg, #0a3a75 0%, #0f62bd 54%, #1d86e8 100%);
    box-shadow: 0 26px 56px rgba(9, 43, 87, 0.22);
    position: relative;
    overflow: hidden;
}
.login-hero::after {
    content: "";
    position: absolute;
    right: -4.6rem;
    bottom: -5.2rem;
    width: 18rem;
    height: 18rem;
    border-radius: 999px;
    border: 2.6rem solid rgba(255,255,255,0.12);
}
.login-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.42rem 0.72rem;
    border: 1px solid rgba(255,255,255,0.26);
    border-radius: 999px;
    background: rgba(255,255,255,0.13);
    font-size: 0.88rem;
    font-weight: 700;
}
.login-title {
    margin: 1.45rem 0 0.75rem;
    font-size: 2.65rem !important;
    line-height: 1.15;
    font-weight: 850;
    letter-spacing: -0.03em;
}
.login-desc {
    max-width: 34rem;
    margin: 0;
    color: rgba(238, 247, 255, 0.88);
    font-size: 1rem;
    line-height: 1.85;
}
.login-points {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
    margin-top: 2.2rem;
    max-width: 34rem;
}
.login-point {
    border: 1px solid rgba(255,255,255,0.16);
    background: rgba(255,255,255,0.11);
    border-radius: 14px;
    padding: 0.8rem 0.9rem;
    font-weight: 720;
}
div[class*="st-key-login_panel"] {
    border-radius: 24px;
}
div[class*="st-key-login_panel"] [data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid rgba(157, 196, 238, 0.72) !important;
    border-radius: 24px !important;
    padding: 1.15rem 1.2rem 1.05rem !important;
    background:
        radial-gradient(circle at 92% 0%, rgba(37, 126, 224, 0.12), transparent 11rem),
        linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(247,251,255,0.97) 100%);
    box-shadow: 0 24px 58px rgba(22, 103, 199, 0.16);
    position: relative;
    overflow: hidden;
}
div[class*="st-key-login_panel"] [data-testid="stVerticalBlockBorderWrapper"]::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    width: 100%;
    height: 4px;
    background: linear-gradient(90deg, #1d7be0 0%, #59a6ff 100%);
}
.login-card-head {
    display: flex;
    align-items: center;
    gap: 0.82rem;
    margin-bottom: 0.2rem;
}
.login-icon {
    width: 2.85rem;
    height: 2.85rem;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 15px;
    color: #ffffff;
    font-size: 1.3rem;
    background: linear-gradient(135deg, #1f7ee4 0%, #0b4f9f 100%);
    box-shadow: 0 12px 24px rgba(22, 103, 199, 0.28);
}
.login-card-title {
    color: var(--text);
    font-size: 1.45rem;
    font-weight: 850;
    line-height: 1.25;
    margin: 0;
}
.login-card-sub {
    color: var(--muted);
    line-height: 1.72;
    margin: 0.82rem 0 1rem;
    font-size: 0.92rem;
}
.login-mini-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 1.05rem;
}
.login-mini-badge {
    color: #0b4f9f;
    background: #eaf4ff;
    border: 1px solid #c7dcf5;
    border-radius: 999px;
    padding: 0.28rem 0.62rem;
    font-size: 0.8rem;
    font-weight: 720;
}
div[class*="st-key-login_panel"] [data-testid="stTextInput"] label p {
    color: #173456 !important;
    font-weight: 720 !important;
}
div[class*="st-key-login_panel"] [data-testid="stTextInput"] > div {
    background: #ffffff !important;
    border: 1px solid #d6e5f7 !important;
    border-radius: 13px !important;
    min-height: 3rem;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.86), 0 7px 16px rgba(22,103,199,0.05);
}
div[class*="st-key-login_panel"] .stFormSubmitButton > button[kind="primary"] {
    min-height: 3.05rem;
    border-radius: 13px !important;
    background: linear-gradient(135deg, #2182ed 0%, #0b59b6 100%) !important;
    box-shadow: 0 13px 24px rgba(22, 103, 199, 0.24);
    font-size: 1rem !important;
    letter-spacing: 0.08em;
}
.login-security-note {
    margin-top: 0.95rem;
    color: #35536f;
    font-size: 0.86rem;
    line-height: 1.65;
    background: #f0f7ff;
    border: 1px solid #d6e8fb;
    border-radius: 13px;
    padding: 0.72rem 0.85rem;
}
</style>
""",
    unsafe_allow_html=True,
)


def auth_headers() -> dict:
    token = st.session_state.get("access_token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


@st.cache_data(ttl=60, show_spinner=False)
def cached_get(path: str, token: str):
    response = requests.get(API_BASE + path, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def clear_cache() -> None:
    cached_get.clear()


def request_error_detail(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        return response.json().get("detail") or str(exc)
    except ValueError:
        return response.text or str(exc)


@st.cache_resource(show_spinner=False)
def pc_login_sessions() -> dict[str, dict]:
    """保存 PC 管理端短时免登录会话；仅存在于当前 Streamlit 进程。"""
    return {}


def get_query_param(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def clear_query_param(name: str) -> None:
    try:
        del st.query_params[name]
    except KeyError:
        pass


def cleanup_pc_login_sessions() -> None:
    store = pc_login_sessions()
    now = int(time.time())
    expired_keys = [key for key, item in store.items() if int(item.get("expires_at", 0)) <= now]
    for key in expired_keys:
        store.pop(key, None)


def create_pc_login_session(access_token: str) -> str:
    """登录成功后生成 15 分钟短会话，支持浏览器刷新免重新输入密码。"""
    cleanup_pc_login_sessions()
    session_id = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + PC_LOGIN_KEEP_SECONDS
    pc_login_sessions()[session_id] = {
        "access_token": access_token,
        "expires_at": expires_at,
    }
    st.session_state["access_token"] = access_token
    st.session_state["pc_login_expires_at"] = expires_at
    st.query_params[PC_SESSION_QUERY_KEY] = session_id
    return session_id


def restore_pc_login_session() -> bool:
    """刷新页面后尝试用短会话恢复 PC 登录态。"""
    cleanup_pc_login_sessions()
    session_id = get_query_param(PC_SESSION_QUERY_KEY)
    if not session_id:
        return False
    item = pc_login_sessions().get(session_id)
    if not item or int(item.get("expires_at", 0)) <= int(time.time()):
        clear_query_param(PC_SESSION_QUERY_KEY)
        return False
    st.session_state["access_token"] = item["access_token"]
    st.session_state["pc_login_expires_at"] = item["expires_at"]
    return True


def clear_pc_login_session() -> None:
    session_id = get_query_param(PC_SESSION_QUERY_KEY)
    if session_id:
        pc_login_sessions().pop(session_id, None)
    clear_query_param(PC_SESSION_QUERY_KEY)
    st.session_state.pop("access_token", None)
    st.session_state.pop("pc_login_expires_at", None)


def api_get(path: str):
    try:
        return cached_get(path, st.session_state.get("access_token", ""))
    except requests.RequestException as exc:
        st.error(f"请求失败：{request_error_detail(exc)}")
        return None


def api_post(path: str, data=None, files=None, json=None):
    try:
        response = requests.post(
            API_BASE + path,
            data=data,
            files=files,
            json=json,
            headers=auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"请求失败：{request_error_detail(exc)}")
        return None


def api_patch(path: str, json=None):
    try:
        response = requests.patch(API_BASE + path, json=json, headers=auth_headers(), timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"请求失败：{request_error_detail(exc)}")
        return None


def api_delete(path: str, params=None):
    try:
        response = requests.delete(API_BASE + path, params=params, headers=auth_headers(), timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"删除失败：{request_error_detail(exc)}")
        return None


def api_download(path: str):
    try:
        response = requests.get(API_BASE + path, headers=auth_headers(), timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        st.error(f"下载失败：{request_error_detail(exc)}")
        return None


def format_time(value: str | None) -> str:
    """把 ISO 时间显示成适合运维人员阅读的格式。"""
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


def parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def is_overdue(task: dict, days: int = 7) -> bool:
    started_at = parse_time(task.get("claimed_at") or task.get("assigned_at"))
    return bool(started_at and task.get("status") != "done" and datetime.now() - started_at > timedelta(days=days))


def task_stage(task: dict) -> str:
    return STATUS_TEXT.get(task.get("status"), "待领取")


def task_closure_stage(task: dict) -> str:
    return task.get("closure_status") or ("已闭环" if task.get("status") == "done" else "现场未完成")


TASK_DELETE_QUERY_FIELDS = {
    "机房名称": "room_name",
    "任务名称": "task_name",
    "片区": "area_name",
    "领取人": "claimed_name",
    "现场状态": "field_stage",
    "闭环状态": "closure_stage",
}


def task_query_value(task: dict, field: str) -> str:
    """按管理员选择的字段提取任务查询文本。"""
    if field == "field_stage":
        return task_stage(task)
    if field == "closure_stage":
        return task_closure_stage(task)
    if field == "area_name":
        return task.get("area_name") or "默认片区"
    return str(task.get(field) or "")


def match_task_by_field(task: dict, field: str, keyword: str) -> bool:
    """作废任务查询：按单一字段做包含匹配。"""
    needle = keyword.strip().lower()
    if not needle:
        return False
    return needle in task_query_value(task, field).lower()


def task_delete_result_rows(tasks: list[dict]) -> list[dict]:
    """生成作废任务查询结果表。"""
    return [
        {
            "_task_id": task["id"],
            "机房": task["room_name"],
            "片区": task.get("area_name") or "默认片区",
            "任务名称": task["task_name"],
            "领取人": task.get("claimed_name") or "-",
            "现场状态": task_stage(task),
            "闭环状态": task_closure_stage(task),
            "进度": f"{task.get('scanned') or 0}/{task.get('total') or 0}",
        }
        for task in tasks
    ]


@st.dialog("作废任务")
def render_delete_task_dialog(task: dict) -> None:
    """弹窗填写作废原因，避免危险操作长期铺在主界面。"""
    room_name = escape(str(task.get("room_name") or "-"))
    task_name = escape(str(task.get("task_name") or "-"))
    area_name = escape(str(task.get("area_name") or "默认片区"))
    st.markdown(
        f"""
<div class="danger-zone">
    将作废任务：{room_name} / {task_name}<br>
    片区：{area_name}。作废后该任务不再参与业务展示和统计，但历史数据会保留。
</div>
""",
        unsafe_allow_html=True,
    )
    with st.form(f"delete_task_dialog_{task['id']}"):
        delete_reason = st.text_area(
            "作废原因",
            placeholder="必填，例如：清单重复导入、机房暂不纳入本次试用、任务导入错误",
            height=110,
        )
        submitted = st.form_submit_button(
            "确认作废任务",
            type="primary",
            width="stretch",
        )
    if submitted:
        if not delete_reason.strip():
            st.warning("请填写作废原因后再确认作废。")
            return
        result = api_delete(
            f"/tasks/{task['id']}",
            params={"reason": delete_reason.strip()},
        )
        if result:
            clear_cache()
            st.session_state["task_notice"] = result["message"]
            st.session_state["delete_task_query_has_run"] = False
            st.session_state["delete_task_query_result_ids"] = []
            st.session_state["delete_task_selected_id"] = None
            st.rerun()


REPORT_QUERY_FIELDS = {
    "全部查询": "__all__",
    "机房名称": "room_name",
    "片区": "area_name",
    "领取人": "assigned_name",
}


def report_query_value(task: dict, field: str) -> str:
    """按管理员选择的字段提取报告任务查询文本。"""
    if field == "field_stage":
        return task_stage(task)
    if field == "closure_stage":
        return task_closure_stage(task)
    if field == "area_name":
        return task.get("area_name") or "默认片区"
    if field == "assigned_name":
        return task.get("assigned_name") or "-"
    return str(task.get(field) or "")


def match_report_task_by_field(task: dict, field: str, keyword: str) -> bool:
    if field == "__all__":
        return True
    needle = keyword.strip().lower()
    if not needle:
        return False
    return needle in report_query_value(task, field).lower()


def report_result_rows(tasks: list[dict]) -> list[dict]:
    """生成盘点报告查询结果表。"""
    return [
        {
            "_task_id": task["id"],
            "机房": task["room_name"],
            "片区": task.get("area_name") or "默认片区",
            "任务名称": task["task_name"],
            "领取人": task.get("assigned_name") or "-",
            "现场状态": task_stage(task),
            "闭环状态": task_closure_stage(task),
            "异常数": task.get("anomaly_count") or 0,
            "待闭环": task.get("review_pending") or 0,
            "进度": f"{task.get('scanned') or 0}/{task.get('total') or 0}",
        }
        for task in tasks
    ]


def safe_download_name(value: str) -> str:
    """生成适合 Excel/zip 内文件名的短名称。"""
    text = str(value or "").strip() or "未命名"
    for char in '\\/:*?"<>|':
        text = text.replace(char, "_")
    return text[:60]


def build_selected_export(
    selected_tasks: list[dict],
    endpoint_suffix: str,
    file_prefix: str,
) -> tuple[bytes | None, str]:
    """按勾选任务生成单个 Excel 或多个 Excel 的 zip 包。"""
    if not selected_tasks:
        return None, ""
    if len(selected_tasks) == 1:
        task = selected_tasks[0]
        data = api_download(f"/export/{task['id']}{endpoint_suffix}")
        filename = f"{file_prefix}_{safe_download_name(task['room_name'])}_{task['id']}.xlsx"
        return data, filename

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for task in selected_tasks:
            data = api_download(f"/export/{task['id']}{endpoint_suffix}")
            if data is None:
                continue
            filename = f"{file_prefix}_{safe_download_name(task['room_name'])}_{task['id']}.xlsx"
            archive.writestr(filename, data)
    data = output.getvalue()
    return (data if data else None), f"{file_prefix}_批量_{len(selected_tasks)}份.zip"


def render_sidebar() -> str:
    with st.sidebar:
        st.markdown(
            """
<div class="app-logo">
    <span class="app-logo-title">机房资产盘点系统</span>
    <span class="app-logo-sub">传输网管维护 · PC 管理后台</span>
</div>
""",
            unsafe_allow_html=True,
        )
        page = st.radio(
            "功能导航",
            [PAGE_UPLOAD, PAGE_TASKS, PAGE_REPORT, PAGE_USERS],
            format_func=lambda item: f"{PAGE_META[item][0]}  {SIDEBAR_PAGE_LABEL[item]}",
            label_visibility="collapsed",
        )
        if st.button("退出登录", width="stretch"):
            clear_pc_login_session()
            clear_cache()
            st.rerun()
    return page


def render_page_head(page: str) -> None:
    icon, title, desc = PAGE_META[page]
    st.markdown(
        f"""
<div class="page-head">
    <div class="page-icon">{icon}</div>
    <div>
        <h1 class="page-title">{title}</h1>
        <p class="page-desc">{desc}</p>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_section(title: str, icon: str) -> None:
    st.markdown(f'<div class="section-title">{icon}<span>{title}</span></div>', unsafe_allow_html=True)


def get_worker_options(workers: list[dict]) -> dict[str, int]:
    return {worker["name"]: worker["id"] for worker in workers}


ANOMALY_TYPES = ["资产条码不一致", "无条码", "设备型号不一致", "盘亏", "盘盈"]
MATCHED_ANOMALIES = {"资产条码不一致", "无条码", "设备型号不一致"}
UNMATCHED_ANOMALIES = {"盘亏", "盘盈"}


def get_anomaly_types(record: dict) -> list[str]:
    """按“设备对得上/对不上”的口径识别 5 类现场异常。"""
    record_type = record.get("record_type") or "normal"
    if record_type == "missing":
        return ["盘亏"]
    if record_type == "surplus":
        return ["盘盈"]

    types = []
    asset_status = record.get("asset_match_status") or record.get("match_status")
    if asset_status not in {"一致", "未扫", "无条码"}:
        # 新导入清单已限制资产条码不能为空；这里兼容历史“无期望条码”数据，统一归入条码不一致类。
        types.append("资产条码不一致")
    if asset_status == "无条码":
        types.append("无条码")
    if record.get("model_match_status") == "不一致":
        types.append("设备型号不一致")
    return types


def anomaly_category(record: dict) -> str:
    """返回异常大类：设备对得上 / 设备对不上 / 正常。"""
    types = set(get_anomaly_types(record))
    if types & UNMATCHED_ANOMALIES:
        return "设备对不上"
    if types & MATCHED_ANOMALIES:
        return "设备对得上"
    return "正常"


def is_anomaly_record(record: dict) -> bool:
    """是否属于需要管理员复核或闭环的现场异常。"""
    return bool(get_anomaly_types(record))


def anomaly_type_text(record: dict) -> str:
    types = get_anomaly_types(record)
    return "、".join(types) if types else "-"


def anomaly_rank(record: dict) -> int:
    """设备明细默认按现场异常处理优先级展示。"""
    types = set(get_anomaly_types(record))
    if record.get("evidence_photo") and (record.get("evidence_photo_days_left") is None or record.get("evidence_photo_days_left") <= 3):
        return 0
    for idx, anomaly_type in enumerate(["盘亏", "盘盈", "资产条码不一致", "无条码", "设备型号不一致"], start=1):
        if anomaly_type in types:
            return idx
    if record.get("match_status") == "未扫":
        return 6
    return 7


def evidence_countdown_text(record: dict) -> str:
    """生成现场凭证照片 30 天有效期倒计时文案。"""
    if not record.get("evidence_photo"):
        return ""
    days_left = record.get("evidence_photo_days_left")
    expires_at = format_time(record.get("evidence_photo_expires_at"))
    if days_left is None:
        return "现场凭证照片已过期或已清理，请重新盘查"
    if days_left <= 0 or not record.get("evidence_photo_valid"):
        return "现场凭证照片已过期，请重新盘查"
    return f"现场凭证照片剩余 {days_left} 天过期（到期时间：{expires_at}），请尽快复核处理"


USER_ROLE_LABELS = {"worker": "现场人员", "admin": "管理员"}
USER_ROLE_VALUES = {"现场人员": "worker", "管理员": "admin"}
USER_QUERY_FIELDS = {
    "姓名": "name",
    "手机号": "phone",
    "登录编码": "wx_openid",
    "工号": "employee_no",
    "负责片区": "area_name",
    "角色": "role",
    "状态": "is_active",
}


def user_display_role(user: dict) -> str:
    return USER_ROLE_LABELS.get(user.get("role"), "现场人员")


def user_display_status(user: dict) -> str:
    return "启用" if user.get("is_active") else "停用"


def match_user_by_field(user: dict, field: str, keyword: str) -> bool:
    """按管理员选择的字段查询账号，默认采用包含匹配，便于模糊查找。"""
    needle = keyword.strip().lower()
    if not needle:
        return False
    if field == "role":
        value = user_display_role(user)
    elif field == "is_active":
        value = user_display_status(user)
    else:
        value = str(user.get(field) or "")
    return needle in value.lower()


def render_user_fields(selected_user: dict | None, form_key: str, submit_label: str) -> tuple[bool, dict]:
    """渲染账号新增/编辑字段，返回提交状态和请求参数。"""
    user = selected_user or {}
    default_role_label = USER_ROLE_LABELS.get(user.get("role", "worker"), "现场人员")

    st.caption("基础信息")
    base_col1, base_col2, base_col3 = st.columns([1.1, 1, 1])
    with base_col1:
        wx_openid = st.text_input(
            "登录编码",
            value=user.get("wx_openid", ""),
            placeholder="如 worker_006；小程序测试登录使用",
            help="微信一键登录生成的待绑定账号，也会显示在这里。",
            key=f"{form_key}_wx_openid",
        )
    with base_col2:
        name = st.text_input(
            "姓名",
            value=user.get("name", ""),
            placeholder="如 王师傅",
            key=f"{form_key}_name",
        )
    with base_col3:
        employee_no = st.text_input(
            "工号",
            value=user.get("employee_no", ""),
            key=f"{form_key}_employee_no",
        )

    st.caption("权限和联系方式")
    perm_col1, perm_col2, perm_col3 = st.columns([1, 1.35, 1])
    with perm_col1:
        role_label = st.selectbox(
            "角色",
            ["现场人员", "管理员"],
            index=["现场人员", "管理员"].index(default_role_label),
            key=f"{form_key}_role",
        )
    with perm_col2:
        area_name = st.text_input(
            "负责片区",
            value=user.get("area_name", "默认片区"),
            placeholder="如 北区 或 北区,南区,西区",
            help="多个片区建议用英文逗号分隔，便于复制和统一维护。",
            key=f"{form_key}_area_name",
        )
    with perm_col3:
        phone = st.text_input(
            "手机号",
            value=user.get("phone", ""),
            key=f"{form_key}_phone",
        )
        is_active = st.checkbox(
            "账号启用",
            value=bool((selected_user or {"is_active": 1}).get("is_active")),
            help="关闭后该账号不能登录小程序，但历史盘点记录仍保留。",
            key=f"{form_key}_is_active",
        )

    submitted = st.form_submit_button(submit_label, type="primary", width="stretch")
    payload = {
        "wx_openid": wx_openid.strip(),
        "name": name.strip(),
        "role": USER_ROLE_VALUES[role_label],
        "area_name": area_name.strip(),
        "phone": phone.strip(),
        "employee_no": employee_no.strip(),
        "is_active": is_active,
    }
    return submitted, payload


@st.dialog("新建空白账号")
def render_create_user_dialog(dialog_version: int) -> None:
    """弹窗新增账号，避免新增表单长期占用账号维护主界面。"""
    st.caption("填写现场人员登录编码、姓名、负责片区和启用状态后保存。")
    form_key = f"create_user_dialog_{dialog_version}"
    with st.form(form_key):
        submitted, payload = render_user_fields(None, form_key, "新增账号")
    if submitted:
        result = api_post("/users", json=payload)
        if result:
            clear_cache()
            st.session_state["user_notice"] = "账号已新增"
            st.rerun()


@st.dialog("编辑账号")
def render_edit_user_dialog(selected_user: dict) -> None:
    """弹窗编辑账号，避免维护区长期展开大表单。"""
    selected_name = escape(str(selected_user.get("name") or "-"))
    selected_code = escape(str(selected_user.get("wx_openid") or "-"))
    selected_area = escape(str(selected_user.get("area_name") or "默认片区"))
    selected_status = user_display_status(selected_user)
    st.markdown(
        f"""
<div class="user-mode-card">
    <strong>当前正在编辑：</strong>{selected_name}
    <span>　登录编码：{selected_code}　|　负责片区：{selected_area}　|　状态：{selected_status}</span>
</div>
""",
        unsafe_allow_html=True,
    )

    form_key = f"edit_user_dialog_{selected_user['id']}"
    with st.form(form_key):
        submitted, payload = render_user_fields(selected_user, form_key, "保存修改")
    if submitted:
        result = api_patch(f"/users/{selected_user['id']}", json=payload)
        if result:
            clear_cache()
            st.session_state["user_notice"] = "账号已更新"
            st.rerun()

    with st.expander("危险操作：删除账号", expanded=False):
        st.markdown(
            f"""
<div class="danger-zone">
    将删除账号：{selected_name} / {selected_code}。<br>
    删除后账号不再显示，也不能登录小程序；历史盘点记录仍会保留。
</div>
""",
            unsafe_allow_html=True,
        )
        confirm_delete = st.checkbox(
            "我确认要删除该账号",
            key=f"dialog_confirm_delete_user_{selected_user['id']}",
        )
        if st.button(
            "删除账号",
            disabled=not confirm_delete,
            width="stretch",
            key=f"dialog_delete_user_{selected_user['id']}",
        ):
            result = api_delete(f"/users/{selected_user['id']}")
            if result:
                clear_cache()
                st.session_state["user_notice"] = result["message"]
                st.session_state["user_query_has_run"] = False
                st.session_state["user_query_result_ids"] = []
                st.session_state["user_edit_selected_id"] = None
                st.rerun()


def get_grid_selected_rows(grid_response) -> list[dict]:
    """兼容 AgGrid 不同版本返回 list 或 DataFrame 的选中行格式。"""
    selected_rows = grid_response.get("selected_rows", []) if grid_response else []
    if isinstance(selected_rows, pd.DataFrame):
        return selected_rows.to_dict("records")
    return selected_rows or []


def render_header_filter_grid(
    rows: list[dict],
    key: str,
    height: int = 260,
    hidden_columns: list[str] | None = None,
    enable_selection: bool = False,
    show_filters: bool = True,
    selection_mode: str = "single",
    fit_columns_on_grid_load: bool = True,
    pagination_page_size: int | None = None,
    sortable: bool = True,
    use_center_header_component: bool = False,
):
    """渲染表格，可按需显示表头模糊过滤框。"""
    df = pd.DataFrame(rows)
    if enable_selection:
        df.insert(0, "选择", "")
    builder = GridOptionsBuilder.from_dataframe(df)
    builder.configure_default_column(
        filter=show_filters,
        floatingFilter=show_filters,
        sortable=sortable,
        resizable=True,
        suppressHeaderMenuButton=True,
        suppressHeaderFilterButton=True,
        headerClass="center-header",
        cellClass="center-cell",
    )
    for column in hidden_columns or []:
        builder.configure_column(column, hide=True)
    if enable_selection:
        builder.configure_column(
            "选择",
            headerName="选择",
            checkboxSelection=True,
            headerCheckboxSelection=selection_mode == "multiple",
            filter=False,
            floatingFilter=False,
            sortable=False,
            resizable=False,
            width=72,
            minWidth=72,
            maxWidth=72,
            pinned="left",
            suppressMenu=True,
            cellClass="center-cell",
            headerClass="center-header",
        )
        builder.configure_selection(selection_mode, use_checkbox=False)
    grid_options = builder.build()
    grid_options["rowHeight"] = 38
    grid_options["headerHeight"] = 42
    grid_options["floatingFiltersHeight"] = 42 if show_filters else 0
    grid_options["suppressCellFocus"] = True
    if use_center_header_component:
        grid_options.setdefault("defaultColDef", {})["headerComponent"] = CENTER_HEADER_COMPONENT
    if pagination_page_size:
        grid_options["pagination"] = True
        grid_options["paginationPageSize"] = pagination_page_size
        grid_options["paginationPageSizeSelector"] = False
    return AgGrid(
        df,
        gridOptions=grid_options,
        height=height,
        fit_columns_on_grid_load=fit_columns_on_grid_load,
        update_mode=GridUpdateMode.NO_UPDATE,
        update_on=["selectionChanged"] if enable_selection else [],
        data_return_mode="FILTERED_AND_SORTED" if enable_selection else "MINIMAL",
        theme="streamlit",
        allow_unsafe_jscode=use_center_header_component,
        custom_css={
            ".centered-header-title": {
                "display": "flex",
                "align-items": "center",
                "justify-content": "center",
                "width": "100%",
                "height": "100%",
                "min-width": "0",
                "text-align": "center",
                "font-weight": "700",
                "line-height": "1.2",
            },
            ".ag-header-cell.center-header": {
                "padding-left": "0 !important",
                "padding-right": "0 !important",
            },
            ".ag-header-cell.center-header .ag-header-cell-comp-wrapper": {
                "width": "100% !important",
                "justify-content": "center !important",
            },
            ".ag-header-cell.center-header .ag-cell-label-container": {
                "width": "100% !important",
                "justify-content": "center !important",
            },
            ".ag-header-cell.center-header .ag-header-cell-label": {
                "display": "flex !important",
                "flex": "1 1 auto !important",
                "justify-content": "center !important",
                "align-items": "center !important",
                "text-align": "center !important",
                "width": "100% !important",
                "padding": "0 !important",
            },
            ".ag-header-cell.center-header .ag-header-cell-text": {
                "display": "block !important",
                "width": "100% !important",
                "text-align": "center !important",
                "font-weight": "700",
            },
            ".center-cell": {
                "text-align": "center",
                "display": "flex",
                "align-items": "center",
                "justify-content": "center",
            },
            ".ag-header-cell": {
                "background-color": "#f4f8ff",
                "font-weight": "700",
            },
            ".ag-row": {
                "border-bottom": "1px solid #edf3fb",
            },
        },
        key=key,
    )


def build_issue_excel(issue_rows: list[dict]) -> bytes:
    """把预检异常明细生成 Excel，方便管理员回头修改原始清单。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "预检异常明细"
    headers = ["级别", "Excel行号", "机房名称", "设备名称", "问题类型", "处理建议"]
    ws.append(headers)
    for row in issue_rows:
        ws.append([row.get(header, "") for header in headers])
    for column in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in column)
        ws.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 42)
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def render_upload_page() -> None:
    render_page_head(PAGE_UPLOAD)
    tasks = api_get("/tasks") or []
    pending_dispatch = [task for task in tasks if task.get("status") == "pending"]

    render_section("导入 Excel 清单", "📁")
    st.markdown(
        """
<div class="soft-panel">
    <p>Excel 至少需要包含：机房名称、机房所在片区、网元名称、设备型号、资产标签号。系统会按机房拆分，并把任务放入对应片区的待领取池。</p>
</div>
""",
        unsafe_allow_html=True,
    )
    st.link_button("📥 下载标准模板", f"{API_BASE}/upload_template")
    upload_file = st.file_uploader("选择 Excel 文件", type=["xlsx", "xls"])
    file_bytes = upload_file.getvalue() if upload_file else b""
    file_token = f"{upload_file.name}:{len(file_bytes)}" if upload_file else ""
    preview_col, import_col = st.columns(2)
    with preview_col:
        preview_clicked = st.button("🔎 预检清单", width="stretch", disabled=upload_file is None)
    with import_col:
        preview_ready = st.session_state.get("preview_file_token") == file_token
        submitted = st.button("📤 导入并生成待盘点机房", type="primary", width="stretch", disabled=not preview_ready)

    if preview_clicked and upload_file:
        files = {"file": (upload_file.name, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        preview = api_post("/upload_rooms/preview", files=files)
        if preview:
            st.session_state["upload_preview"] = preview
            st.session_state["upload_preview_token"] = file_token
            st.session_state["preview_file_token"] = file_token if preview["valid"] else ""
            st.rerun()

    preview = st.session_state.get("upload_preview") if st.session_state.get("upload_preview_token") == file_token else None
    if preview:
        issue_rows = preview.get("issue_rows", [])
        error_count = sum(1 for row in issue_rows if row.get("级别") == "错误")
        remind_count = sum(1 for row in issue_rows if row.get("级别") == "提醒")
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("识别机房", preview["room_count"])
        p2.metric("设备总数", preview["device_count"])
        p3.metric("错误项", error_count)
        p4.metric("提醒项", remind_count)
        p5.metric("空资产条码", preview["missing_barcode"])
        st.dataframe(
            [{"机房名称": row["room_name"], "机房所在片区": row.get("area_name", ""), "设备数": row["device_count"]} for row in preview["rooms"]],
            hide_index=True,
            width="stretch",
        )
        if issue_rows:
            st.download_button(
                "📥 下载预检异常明细",
                data=build_issue_excel(issue_rows),
                file_name="预检异常明细.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
            st.dataframe(issue_rows[:100], hide_index=True, width="stretch")
            if len(issue_rows) > 100:
                st.caption("异常明细较多，页面仅展示前 100 条；请下载 Excel 查看完整明细。")
        if preview["valid"]:
            if remind_count:
                st.warning("预检通过，但存在提醒项；建议管理员确认后再正式导入。")
            else:
                st.success("预检通过，可以正式导入。")
        else:
            st.error("预检未通过，请下载异常明细并修改清单后重新上传。")

    if submitted and upload_file:
        files = {"file": (upload_file.name, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        result = api_post("/upload_rooms", files=files)
        if result:
            st.session_state.pop("upload_preview", None)
            st.session_state.pop("upload_preview_token", None)
            st.session_state.pop("preview_file_token", None)
            clear_cache()
            st.success(result["message"])
            st.rerun()

    render_section("片区待领取任务池", "🧭")
    if not pending_dispatch:
        st.info("当前没有待领取机房。导入成功后，现场人员可在小程序【领任务】中领取同片区任务。")
        return

    st.dataframe(
        [
            {
                "机房名称": task["room_name"],
                "机房所在片区": task.get("area_name") or "默认片区",
                "设备数": task.get("total") or 0,
                "状态": STATUS_TEXT.get(task["status"], task["status"]),
                "导入时间": format_time(task.get("created_at")),
            }
            for task in pending_dispatch
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption("说明：待领取任务不绑定具体人员，同片区现场人员领取成功后，任务会自动变为“盘点中”。")


def render_tasks_page() -> None:
    render_page_head(PAGE_TASKS)
    tasks = api_get("/tasks") or []
    if notice := st.session_state.pop("task_notice", None):
        st.success(notice)
    if not tasks:
        st.info("暂无盘点任务。")
        return

    total = len(tasks)
    pending = sum(1 for task in tasks if task["status"] == "pending")
    doing = sum(1 for task in tasks if task["status"] == "doing")
    done = sum(1 for task in tasks if task["status"] == "done")
    pending_closure = sum(1 for task in tasks if task_closure_stage(task) == "待闭环")
    overdue = sum(1 for task in tasks if is_overdue(task))
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("任务总数", total)
    c2.metric("待领取", pending)
    c3.metric("盘点中", doing)
    c4.metric("现场完成", done)
    c5.metric("待闭环", pending_closure)
    c6.metric("超时任务", overdue, help="领取后超过 7 天仍未完成")

    render_section("任务列表", "📌")
    task_table_rows = []
    for task in tasks:
        task_table_rows.append({
            "_task_id": task["id"],
            "_room_name": task["room_name"],
            "机房": task["room_name"],
            "片区": task.get("area_name") or "默认片区",
            "任务名称": task["task_name"],
            "领取人": task.get("claimed_name") or "-",
            "现场状态": task_stage(task),
            "闭环状态": task_closure_stage(task),
            "异常数": task.get("anomaly_count") or 0,
            "待闭环": task.get("review_pending") or 0,
            "进度": f"{task.get('scanned') or 0}/{task.get('total') or 0}",
            "完成率": f"{(task.get('scanned') or 0) / (task.get('total') or 1):.0%}",
            "领取时间": format_time(task.get("claimed_at")),
            "是否超时": "是" if is_overdue(task) else "否",
        })

    render_header_filter_grid(
        task_table_rows,
        key="task_list_grid",
        height=260,
        hidden_columns=["_task_id", "_room_name"],
        enable_selection=False,
    )

    render_section("作废任务", "🗑️")
    st.markdown(
        """
<div class="user-guide-card">
    <div class="user-guide-title">作废方式</div>
    <p>先选择查询字段，再输入对应内容，点击查询后显示相关任务。勾选任务后点击“作废选中任务”，会弹出卡片填写作废原因。</p>
</div>
""",
        unsafe_allow_html=True,
    )
    task_field_col, task_keyword_col, task_button_col = st.columns([1, 2.4, 0.8])
    with task_field_col:
        delete_field_label = st.selectbox(
            "查询字段",
            list(TASK_DELETE_QUERY_FIELDS.keys()),
            key="delete_task_query_field",
        )
    with task_keyword_col:
        delete_query_value = st.text_input(
            "查询内容",
            placeholder=f"请输入{delete_field_label}",
            key="delete_task_query_value",
        )
    with task_button_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        run_delete_query = st.button("查询", type="primary", width="stretch", key="delete_task_query_btn")

    if run_delete_query:
        query_value = delete_query_value.strip()
        if not query_value:
            st.warning("请输入查询内容后再查询。")
            st.session_state["delete_task_query_has_run"] = False
            st.session_state["delete_task_query_result_ids"] = []
            st.session_state["delete_task_selected_id"] = None
        else:
            query_field = TASK_DELETE_QUERY_FIELDS[delete_field_label]
            delete_candidates = [
                task for task in tasks
                if match_task_by_field(task, query_field, query_value)
            ]
            st.session_state["delete_task_query_has_run"] = True
            st.session_state["delete_task_query_field_label"] = delete_field_label
            st.session_state["delete_task_query_result_value"] = query_value
            st.session_state["delete_task_query_result_ids"] = [task["id"] for task in delete_candidates]
            st.session_state["delete_task_selected_id"] = None

    if st.session_state.get("delete_task_query_has_run"):
        result_ids = set(st.session_state.get("delete_task_query_result_ids", []))
        delete_candidates = [task for task in tasks if task["id"] in result_ids]
        field_label = st.session_state.get("delete_task_query_field_label", delete_field_label)
        value_text = st.session_state.get("delete_task_query_result_value", "")

        if not delete_candidates:
            st.warning(f"没有查到“{field_label} = {value_text}”相关任务。")
        else:
            shown_delete_candidates = delete_candidates[:50]
            st.success(f"查询到 {len(delete_candidates)} 个相关任务。")
            if len(delete_candidates) > 50:
                st.caption("匹配结果较多，当前仅显示前 50 条，请输入更精确的条件。")
            delete_result_grid = render_header_filter_grid(
                task_delete_result_rows(shown_delete_candidates),
                key=f"delete_task_result_grid_{field_label}_{value_text}",
                height=min(240, 48 + 42 * max(len(shown_delete_candidates), 1)),
                hidden_columns=["_task_id"],
                enable_selection=True,
                show_filters=False,
            )
            selected_rows = get_grid_selected_rows(delete_result_grid)
            selected_delete_id = None
            if selected_rows:
                selected_delete_id = selected_rows[0].get("_task_id")
                if selected_delete_id is not None:
                    selected_delete_id = int(selected_delete_id)
                    st.session_state["delete_task_selected_id"] = selected_delete_id
            selected_delete_task = next(
                (task for task in shown_delete_candidates if task["id"] == selected_delete_id),
                None,
            )
            delete_tip_col, action_col = st.columns([2.4, 0.8])
            with delete_tip_col:
                st.markdown(
                    '<div class="user-list-actions">勾选上方查询结果中的任务，然后点击右侧按钮弹出作废确认卡片。</div>',
                    unsafe_allow_html=True,
                )
            with action_col:
                st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
                if st.button(
                    "作废选中任务",
                    type="primary",
                    width="stretch",
                    disabled=selected_delete_task is None,
                    key=f"delete_task_open_dialog_{field_label}_{value_text}",
                ):
                    render_delete_task_dialog(selected_delete_task)


def render_report_page() -> None:
    render_page_head(PAGE_REPORT)
    summary = api_get("/summary") or []
    if not summary:
        st.info("暂无盘点报告数据。")
        return

    st.markdown(
        """
<div class="user-guide-card">
    <div class="user-guide-title">报告查询</div>
    <p>可按全部、机房名称、片区或领取人查询报告任务。选择“全部查询”时无需输入内容，点击查询即可显示全部机房报告。</p>
</div>
""",
        unsafe_allow_html=True,
    )
    report_field_col, report_keyword_col, report_button_col = st.columns([1, 2.4, 0.8])
    with report_field_col:
        report_field_label = st.selectbox(
            "查询字段",
            list(REPORT_QUERY_FIELDS.keys()),
            key="report_query_field",
        )
    with report_keyword_col:
        report_query_input = st.text_input(
            "查询内容",
            placeholder="全部查询无需输入" if report_field_label == "全部查询" else f"请输入{report_field_label}",
            key="report_query_input",
            disabled=report_field_label == "全部查询",
        )
    with report_button_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        run_report_query = st.button("查询", type="primary", width="stretch", key="report_query_btn")

    if run_report_query:
        query_field = REPORT_QUERY_FIELDS[report_field_label]
        query_value = "" if query_field == "__all__" else report_query_input.strip()
        if query_field != "__all__" and not query_value:
            st.warning("请输入查询内容后再查询。")
            st.session_state["report_query_has_run"] = False
            st.session_state["report_query_result_ids"] = []
            st.session_state["report_selected_ids"] = []
        else:
            matched_reports = [
                task for task in summary
                if match_report_task_by_field(task, query_field, query_value)
            ]
            st.session_state["report_query_has_run"] = True
            st.session_state["report_query_field_label"] = report_field_label
            st.session_state["report_query_result_value"] = "全部" if query_field == "__all__" else query_value
            st.session_state["report_query_result_ids"] = [task["id"] for task in matched_reports]
            st.session_state["report_selected_ids"] = []

    if not st.session_state.get("report_query_has_run"):
        st.info("请先选择字段并输入内容查询报告任务。")
        return

    result_ids = set(st.session_state.get("report_query_result_ids", []))
    report_candidates = [task for task in summary if task["id"] in result_ids]
    field_label = st.session_state.get("report_query_field_label", report_field_label)
    value_text = st.session_state.get("report_query_result_value", "")
    if not report_candidates:
        st.warning(f"没有查到“{field_label} = {value_text}”相关报告任务。")
        return

    shown_report_candidates = report_candidates[:80]
    st.success(f"查询到 {len(report_candidates)} 个相关报告任务。")
    if len(report_candidates) > 80:
        st.caption("匹配结果较多，当前仅显示前 80 条，请输入更精确的条件。")
    report_grid = render_header_filter_grid(
        report_result_rows(shown_report_candidates),
        key=f"report_result_grid_{field_label}_{value_text}",
        height=min(280, 48 + 42 * max(len(shown_report_candidates), 1)),
        hidden_columns=["_task_id"],
        enable_selection=True,
        show_filters=False,
        selection_mode="multiple",
    )
    selected_rows = get_grid_selected_rows(report_grid)
    if selected_rows:
        selected_ids = [
            int(row["_task_id"])
            for row in selected_rows
            if row.get("_task_id") is not None
        ]
        st.session_state["report_selected_ids"] = selected_ids

    selected_report_ids = set(st.session_state.get("report_selected_ids", []))
    selected_tasks = [task for task in shown_report_candidates if task["id"] in selected_report_ids]
    report_tip_col, report_download_col = st.columns([2.4, 0.8])
    with report_tip_col:
        st.markdown(
            '<div class="user-list-actions">勾选上方查询结果中的一份或多份报告，然后点击右侧按钮下载 Excel 报告。</div>',
            unsafe_allow_html=True,
        )
    with report_download_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        report_file, report_filename = build_selected_export(selected_tasks, "", "盘点报告")
        st.download_button(
            "📥 下载 Excel 报告",
            data=report_file or b"",
            file_name=report_filename or "盘点报告.xlsx",
            disabled=report_file is None,
            width="stretch",
        )

    if not selected_tasks:
        st.info("请先勾选需要查看或下载的报告任务。")
        return

    all_records = []
    if len(selected_tasks) == 1:
        selected_task = selected_tasks[0]
        st.caption(
            f"现场状态：{task_stage(selected_task)}；闭环状态：{task_closure_stage(selected_task)}；"
            f"待闭环异常：{selected_task.get('review_pending') or 0}"
        )
    else:
        st.caption(f"当前已选择 {len(selected_tasks)} 份报告，下方汇总和设备明细为多任务合并结果。")
    for task in selected_tasks:
        for record in task["records"]:
            item = dict(record)
            item["_room_name"] = task["room_name"]
            item["_task_id"] = task["id"]
            item["_last_scanned_at"] = task.get("last_scanned_at") or ""
            all_records.append(item)

    if not all_records:
        st.info("当前任务暂无盘点记录。")
        return

    inventory_records = [record for record in all_records if (record.get("record_type") or "normal") != "surplus"]
    anomaly_records = [record for record in all_records if is_anomaly_record(record)]
    total_count = len(inventory_records)
    scanned_count = sum(1 for record in inventory_records if record["match_status"] != "未扫")
    pending_count = sum(1 for record in inventory_records if record["match_status"] == "未扫")
    anomaly_count = len(anomaly_records)
    matched_anomaly_count = sum(1 for record in anomaly_records if anomaly_category(record) == "设备对得上")
    unmatched_anomaly_count = sum(1 for record in anomaly_records if anomaly_category(record) == "设备对不上")
    review_pending_count = sum(
        1 for record in anomaly_records
        if (record.get("review_status") or "待复核") == "待复核"
    )
    evidence_expiring_count = sum(
        1 for record in anomaly_records
        if record.get("evidence_photo")
        and (record.get("evidence_photo_days_left") is None or record.get("evidence_photo_days_left") <= 3)
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("设备总数", total_count)
    c2.metric("已盘", scanned_count)
    c3.metric("未盘", pending_count)
    c4.metric("异常设备数", anomaly_count)
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("设备对得上异常", matched_anomaly_count)
    c6.metric("设备对不上异常", unmatched_anomaly_count)
    c7.metric("待复核", review_pending_count)
    c8.metric("凭证即将过期", evidence_expiring_count)

    rooms = {}
    for record in all_records:
        room_name = record["_room_name"]
        if room_name not in rooms:
            rooms[room_name] = {
                "total": 0,
                "scanned": 0,
                "asset_ok": 0,
                "asset_diff": 0,
                "no_barcode": 0,
                "anomaly": 0,
                "matched_anomaly": 0,
                "unmatched_anomaly": 0,
                "surplus": 0,
                "missing": 0,
                "asset_barcode_diff": 0,
                "model_diff": 0,
                "review_pending": 0,
                "review_closed": 0,
                "pending": 0,
                "last_scanned_at": "",
                "records": [],
            }
        asset_status = record.get("asset_match_status") or record["match_status"]
        record_type = record.get("record_type") or "normal"
        if record_type != "surplus":
            rooms[room_name]["total"] += 1
            if record["match_status"] == "未扫":
                rooms[room_name]["pending"] += 1
            else:
                rooms[room_name]["scanned"] += 1
        if record_type not in {"surplus", "missing"}:
            if asset_status == "一致":
                rooms[room_name]["asset_ok"] += 1
            elif asset_status == "无条码":
                rooms[room_name]["no_barcode"] += 1
            elif asset_status != "未扫":
                rooms[room_name]["asset_diff"] += 1
        if record_type == "surplus":
            rooms[room_name]["surplus"] += 1
        elif record_type == "missing":
            rooms[room_name]["missing"] += 1
        if is_anomaly_record(record):
            anomaly_types = get_anomaly_types(record)
            rooms[room_name]["anomaly"] += 1
            if "资产条码不一致" in anomaly_types:
                rooms[room_name]["asset_barcode_diff"] += 1
            if "设备型号不一致" in anomaly_types:
                rooms[room_name]["model_diff"] += 1
            review_status = record.get("review_status") or "待复核"
            if review_status == "已闭环":
                rooms[room_name]["review_closed"] += 1
            else:
                rooms[room_name]["review_pending"] += 1
            if anomaly_category(record) == "设备对得上":
                rooms[room_name]["matched_anomaly"] += 1
            elif anomaly_category(record) == "设备对不上":
                rooms[room_name]["unmatched_anomaly"] += 1
        rooms[room_name]["records"].append(record)
        if record.get("_last_scanned_at"):
            rooms[room_name]["last_scanned_at"] = max(
                rooms[room_name]["last_scanned_at"],
                record["_last_scanned_at"],
            )

    render_section("机房异常汇总", "🏢")
    room_summary_rows = [
        {
            "机房名称": room_name,
            "设备数": data["total"],
            "条码一致": data["asset_ok"],
            "条码异常": data["asset_diff"],
            "无条码": data["no_barcode"],
            "型号不一致": data["model_diff"],
            "盘亏": data["missing"],
            "盘盈": data["surplus"],
            "最近盘点时间": format_time(data["last_scanned_at"]),
        }
        for room_name, data in sorted(rooms.items())
    ]
    render_header_filter_grid(
        room_summary_rows,
        key="room_anomaly_summary_grid",
        height=260,
        show_filters=True,
        sortable=False,
        use_center_header_component=True,
    )

    records = sorted(all_records, key=anomaly_rank)

    detail_title_col, detail_export_col, surplus_export_col = st.columns([2.4, 1, 1])
    with detail_title_col:
        render_section("设备明细", "🧾")
    with detail_export_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        detail_file, detail_filename = build_selected_export(selected_tasks, "/details", "设备明细")
        st.download_button(
            "📋 导出设备明细",
            data=detail_file or b"",
            file_name=detail_filename or "设备明细.xlsx",
            disabled=detail_file is None,
            width="stretch",
        )
    with surplus_export_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        exception_file, exception_filename = build_selected_export(selected_tasks, "/surplus_missing", "盘盈盘亏")
        st.download_button(
            "🛠️ 导出盘盈盘亏",
            data=exception_file or b"",
            file_name=exception_filename or "盘盈盘亏.xlsx",
            disabled=exception_file is None,
            width="stretch",
        )
    device_display_limit = 500
    shown_records = records[:device_display_limit]
    if len(records) > device_display_limit:
        st.caption(
            f"设备明细较多，页面优先展示前 {device_display_limit} 条；完整设备明细请点击右侧“导出设备明细”。"
        )
    device_detail_rows = [
        {
            "设备名称": record["device_name"],
            "机房": record["_room_name"],
            "异常大类": anomaly_category(record),
            "异常类型": anomaly_type_text(record),
            "现场状态": record.get("field_status") or "正常",
            "设备型号": record["expected_model"],
            "资产条码": record.get("expected_barcode") or "-",
            "现场设备型号": record["actual_model"] or "-",
            "现场资产条码": record.get("asset_barcodes") or ("无条码" if record.get("asset_no_barcode") else "-"),
            "现场机柜条码": record.get("cabinet_barcodes") or ("无条码" if record.get("cabinet_no_barcode") else "-"),
            "现场机架号": record.get("rack_no") or "-",
            "资产条码对比结果": record.get("asset_match_status") or record["match_status"],
            "设备型号对比结果": record.get("model_match_status") or "-",
            "复核状态": record.get("review_status") or "待复核",
            "盘亏原因": record.get("missing_reason") or "-",
            "备注": record["notes"] or "-",
        }
        for record in shown_records
    ]
    render_header_filter_grid(
        device_detail_rows,
        key="device_detail_grid",
        height=280,
        fit_columns_on_grid_load=False,
        pagination_page_size=50,
    )

    mismatch_records = [
        record for record in records
        if is_anomaly_record(record)
    ]
    if mismatch_records:
        render_section("现场异常复核闭环", "⚠️")
        anomaly_options = {
            (
                f"{record['_room_name']} / {record['device_name']} / {anomaly_type_text(record)} / {record.get('review_status') or '待复核'}"
                + (
                    f" / 凭证剩余{record.get('evidence_photo_days_left')}天"
                    if record.get("evidence_photo") and record.get("evidence_photo_days_left") is not None
                    else ""
                )
            ): record
            for record in mismatch_records
        }
        anomaly_label = st.selectbox("选择异常记录", list(anomaly_options.keys()))
        anomaly = anomaly_options[anomaly_label]
        st.info(
            f"异常大类：{anomaly_category(anomaly)}；异常类型：{anomaly_type_text(anomaly)}；"
            f"期望型号：{anomaly['expected_model'] or '-'}；现场型号：{anomaly['actual_model'] or '-'}"
        )
        if anomaly.get("evidence_photo"):
            countdown_text = evidence_countdown_text(anomaly)
            days_left = anomaly.get("evidence_photo_days_left")
            if days_left is None or days_left <= 3:
                st.warning(f"⏰ {countdown_text}")
            else:
                st.info(f"⏰ {countdown_text}")
            token = st.session_state.get("access_token", "")
            st.link_button("🖼️ 查看现场凭证", f"{API_BASE}/evidence/{anomaly['evidence_photo']}?access_token={token}")
        review_col, reason_col, handler_col = st.columns([1, 1.2, 1])
        with review_col:
            review_status = st.selectbox(
                "复核状态",
                ["待复核", "已确认异常", "已退回现场", "已整改", "已闭环"],
                key="review_status",
            )
        with reason_col:
            review_reason = st.selectbox(
                "异常原因",
                ["现场盘盈", "现场盘亏", "资源表错误", "现场条码缺失", "贴错条码", "设备更换未同步", "现场录入错误", "其他"],
                key="review_reason",
            )
        with handler_col:
            review_handler = st.text_input("处理人", value=anomaly.get("review_handler") or "", key="review_handler")

        result_col, note_col, button_col = st.columns([1.2, 2, 1])
        with result_col:
            review_result = st.selectbox(
                "处理结论",
                ["待处理", "需更新资源表", "需现场整改", "退回重新盘点", "已确认属实", "已关闭"],
                key="review_result",
            )
        with note_col:
            review_note = st.text_input("处理说明", value=anomaly.get("review_note") or "", key="review_note")
        with button_col:
            st.write("")
            can_save_review = review_status != "已闭环" or (review_note.strip() and review_handler.strip() and review_result != "待处理")
            if st.button("保存复核", width="stretch", disabled=not can_save_review):
                result = api_post(
                    f"/records/{anomaly['id']}/review",
                    json={
                        "status": review_status,
                        "note": review_note.strip(),
                        "reason": review_reason,
                        "handler": review_handler.strip(),
                        "result": review_result,
                    },
                )
                if result:
                    clear_cache()
                    st.success(result["message"])
                    st.rerun()


def render_users_page() -> None:
    """小程序账号管理：维护现场人员可领取片区和账号启停。"""
    render_page_head(PAGE_USERS)
    users = api_get("/users") or []
    if notice := st.session_state.pop("user_notice", None):
        st.success(notice)

    total = len(users)
    active = sum(1 for user in users if user.get("is_active"))
    inactive = total - active
    workers = sum(1 for user in users if user.get("role") == "worker")
    admins = sum(1 for user in users if user.get("role") == "admin")
    multi_area = sum(1 for user in users if any(sep in (user.get("area_name") or "") for sep in [",", "，", "、"]))
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("账号总数", total)
    c2.metric("启用账号", active)
    c3.metric("待启用/停用", inactive)
    c4.metric("现场人员", workers)
    c5.metric("管理员", admins)
    c6.metric("跨片区", multi_area)

    render_section("账号列表", "👥")
    user_rows = [
        {
            "_user_id": user["id"],
            "登录编码": user.get("wx_openid") or "",
            "姓名": user.get("name") or "",
            "角色": user_display_role(user),
            "负责片区": user.get("area_name") or "默认片区",
            "手机号": user.get("phone") or "",
            "工号": user.get("employee_no") or "",
            "状态": "✅ 启用" if user.get("is_active") else "⏸ 停用",
        }
        for user in users
    ]
    render_header_filter_grid(
        user_rows,
        key="user_list_grid",
        height=300,
        hidden_columns=["_user_id"],
        enable_selection=False,
    )

    list_tip_col, new_account_col = st.columns([3, 1])
    with list_tip_col:
        st.markdown(
            '<div class="user-list-actions">这里用于快速总览所有账号；如需维护某个账号，请在下方按字段查询后编辑。</div>',
            unsafe_allow_html=True,
        )
    with new_account_col:
        if st.button("➕ 新建空白账号", width="stretch"):
            st.session_state["create_user_dialog_version"] = st.session_state.get("create_user_dialog_version", 0) + 1
            render_create_user_dialog(st.session_state["create_user_dialog_version"])

    render_section("账号维护", "🛠️")
    st.markdown(
        """
<div class="user-guide-card">
    <div class="user-guide-title">维护方式</div>
    <p>先选择查询字段，再输入对应内容，点击查询后显示相关账号。勾选账号后点击“编辑选中账号”，会弹出编辑卡片；保存修改和删除账号都在卡片中完成。</p>
</div>
""",
        unsafe_allow_html=True,
    )

    field_col, keyword_col, button_col = st.columns([1, 2.4, 0.8])
    with field_col:
        query_field_label = st.selectbox(
            "查询字段",
            list(USER_QUERY_FIELDS.keys()),
            help="选择你要按哪类信息查询账号。",
        )
    with keyword_col:
        query_value = st.text_input(
            "查询内容",
            placeholder=f"请输入{query_field_label}",
            help="支持包含匹配，例如姓名输入“王”可查出姓名包含“王”的账号。",
        )
    with button_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        do_query = st.button("查询", type="primary", width="stretch")

    if do_query:
        query_value_clean = query_value.strip()
        if not query_value_clean:
            st.warning("请输入查询内容后再查询。")
            st.session_state["user_query_result_ids"] = []
            st.session_state["user_query_has_run"] = False
        else:
            query_field = USER_QUERY_FIELDS[query_field_label]
            matched_users = [
                user for user in users
                if match_user_by_field(user, query_field, query_value_clean)
            ]
            st.session_state["user_query_has_run"] = True
            st.session_state["user_query_field_label"] = query_field_label
            st.session_state["user_query_value"] = query_value_clean
            st.session_state["user_query_result_ids"] = [user["id"] for user in matched_users]
            st.session_state["user_edit_selected_id"] = None

    if st.session_state.get("user_query_has_run"):
        result_ids = set(st.session_state.get("user_query_result_ids", []))
        result_users = [user for user in users if user["id"] in result_ids]
        field_label = st.session_state.get("user_query_field_label", query_field_label)
        value_text = st.session_state.get("user_query_value", "")

        if not result_users:
            st.warning(f"没有查到“{field_label} = {value_text}”相关账号。")
        else:
            st.success(f"查询到 {len(result_users)} 个相关账号。")
            result_rows = [
                {
                    "_user_id": user["id"],
                    "登录编码": user.get("wx_openid") or "",
                    "姓名": user.get("name") or "",
                    "角色": user_display_role(user),
                    "负责片区": user.get("area_name") or "默认片区",
                    "手机号": user.get("phone") or "",
                    "工号": user.get("employee_no") or "",
                    "状态": user_display_status(user),
                }
                for user in result_users
            ]
            result_grid = render_header_filter_grid(
                result_rows,
                key=f"user_query_result_grid_{field_label}_{value_text}",
                height=min(220, 48 + 42 * max(len(result_rows), 1)),
                hidden_columns=["_user_id"],
                enable_selection=True,
                show_filters=False,
            )
            selected_rows = get_grid_selected_rows(result_grid)
            selected_id = None
            if selected_rows:
                selected_id = selected_rows[0].get("_user_id")
                if selected_id is not None:
                    selected_id = int(selected_id)
                    st.session_state["user_edit_selected_id"] = selected_id
            elif st.session_state.get("user_edit_selected_id") in result_ids:
                selected_id = st.session_state["user_edit_selected_id"]

            selected_user = next((user for user in result_users if user["id"] == selected_id), None)
            action_tip_col, edit_action_col = st.columns([2.6, 1])
            with action_tip_col:
                st.markdown(
                    '<div class="user-list-actions">勾选上方查询结果中的账号，然后点击右侧按钮弹出编辑卡片。</div>',
                    unsafe_allow_html=True,
                )
            with edit_action_col:
                if st.button(
                    "编辑选中账号",
                    type="primary",
                    width="stretch",
                    disabled=selected_user is None,
                ):
                    render_edit_user_dialog(selected_user)

    render_section("最近操作审计", "🧷")
    audit_logs = api_get("/audit_logs?limit=100") or []
    if not audit_logs:
        st.info("暂无操作审计记录。")
        return
    audit_rows = [
        {
            "时间": format_time(log.get("created_at")),
            "操作人": log.get("actor_name") or "-",
            "角色": "管理员" if log.get("actor_role") == "admin" else ("现场人员" if log.get("actor_role") == "worker" else "-"),
            "动作": log.get("action") or "",
            "对象": f"{log.get('target_type') or ''}:{log.get('target_id') or ''}",
            "请求ID": log.get("request_id") or "-",
            "详情": json.dumps(log.get("detail") or {}, ensure_ascii=False),
        }
        for log in audit_logs
    ]
    render_header_filter_grid(
        audit_rows,
        key="audit_log_grid",
        height=260,
        enable_selection=False,
    )


def require_admin_login() -> None:
    if st.session_state.get("access_token"):
        return
    if restore_pc_login_session():
        return

    st.markdown('<div class="login-wrap">', unsafe_allow_html=True)
    left_col, right_col = st.columns([1.05, 0.95], gap="large")
    with left_col:
        st.markdown(
            """
<div class="login-hero">
    <div class="login-badge">📡 传输网管维护 · 内部试用</div>
    <h1 class="login-title">机房资产<br>盘点管理端</h1>
    <p class="login-desc">
        面向机房现场盘点、账号分配、任务跟踪和异常闭环。管理员在 PC 端统一维护任务，
        现场人员通过小程序领取和提交盘点结果。
    </p>
    <div class="login-points">
        <div class="login-point">账号启停</div>
        <div class="login-point">片区授权</div>
        <div class="login-point">任务看板</div>
        <div class="login-point">异常闭环</div>
    </div>
</div>
""",
            unsafe_allow_html=True,
        )
    with right_col:
        with st.container(border=True, key="login_panel"):
            st.markdown(
                """
<div class="login-card-head">
    <div class="login-icon">🔐</div>
    <div>
        <div class="login-card-title">管理员登录</div>
        <div style="color:#62758d;font-size:0.9rem;margin-top:0.2rem;">PC 管理后台安全入口</div>
    </div>
</div>
<div class="login-card-sub">请输入管理员账号密码。登录成功后，15 分钟内刷新页面不会要求重新输入。</div>
<div class="login-mini-badges">
    <span class="login-mini-badge">15 分钟免重复登录</span>
    <span class="login-mini-badge">内部试用环境</span>
</div>
""",
                unsafe_allow_html=True,
            )
            with st.form("admin_login", border=False, enter_to_submit=False):
                username = st.text_input("管理员账号", value="admin_001", placeholder="请输入管理员账号")
                password = st.text_input("密码", type="password", placeholder="请输入密码")
                submitted = st.form_submit_button("登录", type="primary", width="stretch")
            st.markdown(
                """
<div class="login-security-note">
    🔐 安全提示：免登录只对当前浏览器窗口短时有效。点击左侧“退出登录”会立即清除登录状态。
</div>
""",
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)

    if submitted:
        try:
            response = requests.post(
                API_BASE + "/admin/login",
                json={"username": username.strip(), "password": password},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            create_pc_login_session(response.json()["access_token"])
            st.rerun()
        except requests.RequestException:
            st.error("账号或密码错误，或后端服务不可用。")
    st.stop()


require_admin_login()
current_page = render_sidebar()
if current_page == PAGE_UPLOAD:
    render_upload_page()
elif current_page == PAGE_TASKS:
    render_tasks_page()
elif current_page == PAGE_USERS:
    render_users_page()
else:
    render_report_page()
