"""机房资产盘点系统 - PC 后台管理端。"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
import openpyxl
import pandas as pd
import requests
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

API_BASE = "http://localhost:8000/api"
REQUEST_TIMEOUT = 10

PAGE_UPLOAD = "上传任务清单"
PAGE_TASKS = "任务看板"
PAGE_REPORT = "盘点报告"

SIDEBAR_PAGE_LABEL = {
    PAGE_UPLOAD: "上传任务清单",
    PAGE_TASKS: "任务看板总览",
    PAGE_REPORT: "盘点报告分析",
}

STATUS_TEXT = {
    "pending": "待处理",
    "doing": "进行中",
    "done": "已完成",
}

PAGE_META = {
    PAGE_UPLOAD: ("📤", "上传任务清单", "导入包含多个机房的 Excel，系统自动按机房生成待下发盘点任务。"),
    PAGE_TASKS: ("📋", "任务看板", "查看各机房盘点进度，支持删除不需要继续盘点的机房任务。"),
    PAGE_REPORT: ("📊", "盘点报告", "按机房汇总盘点结果，快速定位未扫和型号不一致设备。"),
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

.stButton > button, .stDownloadButton > button, [data-testid="stLinkButton"] a {
    min-height: 2.38rem;
    border-radius: 9px !important;
    border: 1px solid var(--line-strong) !important;
    background: #ffffff !important;
    color: var(--primary-dark) !important;
    font-size: 0.92rem !important;
    font-weight: 700 !important;
    box-shadow: 0 4px 12px rgba(22, 103, 199, 0.06);
}
.stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stLinkButton"] a:hover {
    background: var(--primary-soft) !important;
    border-color: var(--primary) !important;
    color: var(--primary-dark) !important;
}
.stButton > button[kind="primary"] {
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
.ag-theme-streamlit .ag-header-cell-text {
    font-weight: 780;
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
[data-testid="stAlert"] {
    border-radius: 12px;
    border: 1px solid var(--line);
}
div[data-testid="stHorizontalBlock"] {
    gap: 1rem;
}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60, show_spinner=False)
def cached_get(path: str):
    response = requests.get(API_BASE + path, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def clear_cache() -> None:
    cached_get.clear()


def api_get(path: str):
    try:
        return cached_get(path)
    except requests.RequestException as exc:
        st.error(f"请求失败：{exc}")
        return None


def api_post(path: str, data=None, files=None, json=None):
    try:
        response = requests.post(
            API_BASE + path,
            data=data,
            files=files,
            json=json,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        detail = str(exc)
        if getattr(exc, "response", None) is not None:
            try:
                detail = exc.response.json().get("detail", detail)
            except ValueError:
                detail = exc.response.text or detail
        st.error(f"请求失败：{detail}")
        return None


def api_delete(path: str, params=None):
    try:
        response = requests.delete(API_BASE + path, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"删除失败：{exc}")
        return None


def api_download(path: str):
    try:
        response = requests.get(API_BASE + path, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        st.error(f"下载失败：{exc}")
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
    assigned_at = parse_time(task.get("assigned_at"))
    return bool(assigned_at and task.get("status") != "done" and datetime.now() - assigned_at > timedelta(days=days))


def task_stage(task: dict) -> str:
    if task.get("status") == "done":
        return "已完成"
    if is_overdue(task):
        return "已超时"
    if task.get("status") == "doing":
        return "进行中"
    if task.get("assigned_name"):
        return "已下发未开始"
    return "未下发"


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
            [PAGE_UPLOAD, PAGE_TASKS, PAGE_REPORT],
            format_func=lambda item: f"{PAGE_META[item][0]}  {SIDEBAR_PAGE_LABEL[item]}",
            label_visibility="collapsed",
        )
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
):
    """渲染带表头模糊过滤框的表格。"""
    df = pd.DataFrame(rows)
    builder = GridOptionsBuilder.from_dataframe(df)
    builder.configure_default_column(
        filter=True,
        floatingFilter=True,
        sortable=True,
        resizable=True,
        headerClass="center-header",
        cellClass="center-cell",
    )
    for column in hidden_columns or []:
        builder.configure_column(column, hide=True)
    if enable_selection:
        builder.configure_selection("single", use_checkbox=True)
    grid_options = builder.build()
    grid_options["rowHeight"] = 38
    grid_options["headerHeight"] = 42
    grid_options["floatingFiltersHeight"] = 42
    grid_options["suppressCellFocus"] = True
    return AgGrid(
        df,
        gridOptions=grid_options,
        height=height,
        fit_columns_on_grid_load=True,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        theme="streamlit",
        custom_css={
            ".center-header .ag-header-cell-label": {
                "justify-content": "center",
                "text-align": "center",
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
    workers = api_get("/workers") or []
    tasks = api_get("/tasks") or []
    worker_options = get_worker_options(workers)
    pending_dispatch = [task for task in tasks if not task.get("assigned_name")]

    render_section("导入 Excel 清单", "📁")
    st.markdown(
        """
<div class="soft-panel">
    <p>Excel 至少需要包含：机房名称、网元名称、设备型号、资产标签号。系统会按机房名称自动拆分，生成待下发机房任务。</p>
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
            [{"机房名称": row["room_name"], "设备数": row["device_count"]} for row in preview["rooms"]],
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

    render_section("待下发机房", "🧭")
    if not workers:
        st.warning("暂无现场人员，请先确认后端默认用户已初始化。")
        return
    if not pending_dispatch:
        st.info("当前没有待下发机房。")
        return

    st.dataframe(
        [
            {
                "机房名称": task["room_name"],
                "设备数": task.get("total") or 0,
                "状态": STATUS_TEXT.get(task["status"], task["status"]),
                "导入时间": format_time(task.get("created_at")),
            }
            for task in pending_dispatch
        ],
        hide_index=True,
        width="stretch",
    )

    render_section("选择现场人员并下发", "👷")
    selected_rooms = st.multiselect(
        "批量下发范围",
        options=[task["id"] for task in pending_dispatch],
        default=[task["id"] for task in pending_dispatch],
        format_func=lambda task_id: next(task["room_name"] for task in pending_dispatch if task["id"] == task_id),
    )
    assignments = []
    for task in pending_dispatch:
        cols = st.columns([2.2, 1.2, 1.1])
        with cols[0]:
            st.write(f"**{task['room_name']}**")
            st.caption(f"设备数：{task.get('total') or 0}")
        with cols[1]:
            selected_worker = st.selectbox(
                "现场人员",
                list(worker_options.keys()),
                key=f"assign_worker_{task['id']}",
                label_visibility="collapsed",
            )
        with cols[2]:
            if st.button("下发", key=f"assign_btn_{task['id']}", width="stretch"):
                result = api_post(
                    f"/tasks/{task['id']}/assign",
                    json={"assigned_to": worker_options[selected_worker]},
                )
                if result:
                    clear_cache()
                    st.success(f"{task['room_name']} 已下发给 {selected_worker}")
                    st.rerun()
        if task["id"] in selected_rooms:
            assignments.append({"task_id": task["id"], "assigned_to": worker_options[selected_worker]})

    if st.button("🚀 批量下发所选机房", type="primary", width="stretch", disabled=not assignments):
        result = api_post("/tasks/batch_assign", json={"assignments": assignments})
        if result:
            clear_cache()
            st.success(result["message"])
            st.rerun()


def render_tasks_page() -> None:
    render_page_head(PAGE_TASKS)
    tasks = api_get("/tasks") or []
    if not tasks:
        st.info("暂无盘点任务。")
        return

    total = len(tasks)
    pending = sum(1 for task in tasks if task["status"] == "pending")
    doing = sum(1 for task in tasks if task["status"] == "doing")
    done = sum(1 for task in tasks if task["status"] == "done")
    overdue = sum(1 for task in tasks if is_overdue(task))
    unassigned = sum(1 for task in tasks if not task.get("assigned_name"))
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("任务总数", total)
    c2.metric("未下发", unassigned or pending)
    c3.metric("进行中", doing)
    c4.metric("已完成", done)
    c5.metric("超时任务", overdue, help="下发后超过 7 天仍未完成")

    filter_col, refresh_col = st.columns([3.2, 0.8])
    with filter_col:
        status_filter = st.selectbox(
            "状态筛选",
            ["全部", "未下发", "已下发未开始", "进行中", "已超时", "已完成"],
        )
    with refresh_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        if st.button("🔄 刷新", width="stretch"):
            clear_cache()
            st.rerun()

    filtered = tasks if status_filter == "全部" else [t for t in tasks if task_stage(t) == status_filter]
    if not filtered:
        st.info("当前筛选条件下没有任务。")
        return

    render_section("任务列表", "📌")
    task_table_rows = []
    for task in filtered:
        task_table_rows.append({
            "_task_id": task["id"],
            "_room_name": task["room_name"],
            "机房": task["room_name"],
            "任务名称": task["task_name"],
            "现场人员": task.get("assigned_name") or "未下发",
            "状态": task_stage(task),
            "进度": f"{task.get('scanned') or 0}/{task.get('total') or 0}",
            "完成率": f"{(task.get('scanned') or 0) / (task.get('total') or 1):.0%}",
            "下发时间": format_time(task.get("assigned_at")),
            "是否超时": "是" if is_overdue(task) else "否",
        })

    event = render_header_filter_grid(
        task_table_rows,
        key="task_list_grid",
        height=260,
        hidden_columns=["_task_id", "_room_name"],
        enable_selection=True,
    )
    selected_rows = get_grid_selected_rows(event)
    if selected_rows:
        selected_task = selected_rows[0]
        st.session_state["selected_task_id"] = selected_task["_task_id"]
        st.session_state["selected_report_room"] = selected_task["_room_name"]

    render_section("作废任务", "🗑️")
    delete_keyword = st.text_input(
        "搜索要作废的机房任务",
        placeholder="输入机房名称或任务名称关键字，支持模糊搜索",
    )
    keyword = delete_keyword.strip().lower()
    delete_candidates = [
        task
        for task in tasks
        if keyword
        and (
            keyword in (task.get("room_name") or "").lower()
            or keyword in (task.get("task_name") or "").lower()
        )
    ]
    shown_delete_candidates = delete_candidates[:50]
    delete_options = {
        f"{task['room_name']} / {task['task_name']}": task["id"]
        for task in shown_delete_candidates
    }
    if keyword and not delete_candidates:
        st.warning("没有找到匹配的机房任务，请换个关键字再试。")
    if len(delete_candidates) > 50:
        st.caption("匹配结果较多，当前仅显示前 50 条，请输入更精确的关键字。")
    if not delete_options:
        delete_options = {"请先输入关键字搜索": None}
    delete_col, reason_col, action_col = st.columns([2, 2, 1])
    with delete_col:
        delete_rows = [
            {
                "机房": task["room_name"],
                "任务名称": task["task_name"],
                "现场人员": task.get("assigned_name") or "未下发",
                "状态": task_stage(task),
                "进度": f"{task.get('scanned') or 0}/{task.get('total') or 0}",
            }
            for task in shown_delete_candidates
        ]
        delete_event = st.dataframe(
            delete_rows,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="delete_task_table",
        )
        delete_label = None
        if delete_event and delete_event.selection and delete_event.selection.rows:
            selected_delete_index = delete_event.selection.rows[0]
            if selected_delete_index < len(shown_delete_candidates):
                selected_delete_task = shown_delete_candidates[selected_delete_index]
                delete_label = f"{selected_delete_task['room_name']} / {selected_delete_task['task_name']}"
    with reason_col:
        delete_reason = st.text_input("作废原因", placeholder="必填，如：清单重复导入")
    delete_task_id = delete_options.get(delete_label)
    if delete_task_id is None:
        delete_reason = ""
    with action_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        if st.button("作废任务", width="stretch", disabled=not delete_reason.strip()):
            result = api_delete(
                f"/tasks/{delete_task_id}",
                params={"reason": delete_reason.strip()},
            )
            if result:
                clear_cache()
                st.success(result["message"])
                st.rerun()


def render_report_page() -> None:
    render_page_head(PAGE_REPORT)
    summary = api_get("/summary") or []
    if not summary:
        st.info("暂无盘点报告数据。")
        return

    task_options = {f"{task['room_name']} / {task['task_name']}": task["id"] for task in summary}
    selected_task_id = st.session_state.get("selected_task_id")
    option_labels = list(task_options.keys())
    default_index = 0
    if selected_task_id in task_options.values():
        default_index = list(task_options.values()).index(selected_task_id)

    select_col, export_col, detail_export_col, surplus_export_col = st.columns([2.6, 1, 1, 1.1])
    with select_col:
        selected_label = st.selectbox(
            "报告任务",
            option_labels,
            index=default_index,
            help="选择一个机房任务后，下方只展示该任务的汇总、明细和复核内容。",
        )
    selected_report_id = task_options[selected_label]
    with export_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        st.link_button(
            "📥 下载 Excel 报告",
            f"{API_BASE}/export/{selected_report_id}",
            width="stretch",
        )
    with detail_export_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        st.link_button(
            "📋 导出设备明细",
            f"{API_BASE}/export/{selected_report_id}/details",
            width="stretch",
        )
    with surplus_export_col:
        st.markdown('<div class="button-bottom-align-spacer"></div>', unsafe_allow_html=True)
        st.link_button(
            "🛠️ 导出盘盈盘亏",
            f"{API_BASE}/export/{selected_report_id}/surplus_missing",
            width="stretch",
        )

    all_records = []
    selected_tasks = [task for task in summary if task["id"] == selected_report_id]
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

    render_section("现场异常分类统计", "🧭")
    anomaly_summary_rows = []
    for anomaly_type in ANOMALY_TYPES:
        type_records = [record for record in all_records if anomaly_type in get_anomaly_types(record)]
        anomaly_summary_rows.append({
            "异常大类": "设备对不上" if anomaly_type in UNMATCHED_ANOMALIES else "设备对得上",
            "异常类型": anomaly_type,
            "数量": len(type_records),
            "待复核": sum(1 for record in type_records if (record.get("review_status") or "待复核") == "待复核"),
            "已闭环": sum(1 for record in type_records if (record.get("review_status") or "") == "已闭环"),
        })
    render_header_filter_grid(
        anomaly_summary_rows,
        key="anomaly_summary_grid",
        height=240,
    )

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
            rooms[room_name]["anomaly"] += 1
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

    render_section("机房汇总", "🏢")
    room_summary_rows = [
        {
            "机房名称": room_name,
            "设备数": data["total"],
            "已盘": data["scanned"],
            "未扫": data["pending"],
            "条码一致": data["asset_ok"],
            "条码异常": data["asset_diff"],
            "无条码": data["no_barcode"],
            "异常设备数": data["anomaly"],
            "设备对得上异常": data["matched_anomaly"],
            "设备对不上异常": data["unmatched_anomaly"],
            "盘盈": data["surplus"],
            "盘亏": data["missing"],
            "最后盘点时间": format_time(data["last_scanned_at"]),
        }
        for room_name, data in sorted(rooms.items())
    ]
    render_header_filter_grid(
        room_summary_rows,
        key="room_summary_grid",
        height=220,
    )

    room_col, view_col = st.columns([1, 1])
    with room_col:
        selected_room = st.selectbox("设备明细范围", ["全部"] + sorted(rooms.keys()))
    with view_col:
        detail_view = st.selectbox(
            "处理视图",
            [
                "全部记录", "全部异常", "设备对得上", "设备对不上",
                "资产条码不一致", "无条码", "设备型号不一致", "盘亏", "盘盈",
                "未扫设备", "待复核", "已闭环",
            ],
            help="用于快速优先处理现场异常，不影响表头内的模糊筛选。",
        )
    records = all_records if selected_room == "全部" else rooms[selected_room]["records"]
    if detail_view == "全部异常":
        records = [record for record in records if is_anomaly_record(record)]
    elif detail_view == "设备对得上":
        records = [record for record in records if anomaly_category(record) == "设备对得上"]
    elif detail_view == "设备对不上":
        records = [record for record in records if anomaly_category(record) == "设备对不上"]
    elif detail_view in ANOMALY_TYPES:
        records = [record for record in records if detail_view in get_anomaly_types(record)]
    elif detail_view == "未扫设备":
        records = [record for record in records if record["match_status"] == "未扫"]
    elif detail_view == "待复核":
        records = [
            record for record in records
            if is_anomaly_record(record)
            and (record.get("review_status") or "待复核") == "待复核"
        ]
    elif detail_view == "已闭环":
        records = [
            record for record in records
            if is_anomaly_record(record)
            and (record.get("review_status") or "") == "已闭环"
        ]
    records = sorted(records, key=anomaly_rank)

    render_section("设备明细", "🧾")
    device_detail_rows = [
        {
            "机房": record["_room_name"],
            "异常大类": anomaly_category(record),
            "异常类型": anomaly_type_text(record),
            "现场状态": record.get("field_status") or "正常",
            "设备": record["device_name"],
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
        for record in records
    ]
    render_header_filter_grid(
        device_detail_rows,
        key="device_detail_grid",
        height=280,
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
            st.link_button("🖼️ 查看现场凭证", f"{API_BASE}/evidence/{anomaly['evidence_photo']}")
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

current_page = render_sidebar()
if current_page == PAGE_UPLOAD:
    render_upload_page()
elif current_page == PAGE_TASKS:
    render_tasks_page()
else:
    render_report_page()
