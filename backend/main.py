"""机房资产盘点系统 - FastAPI 后端入口。"""

from __future__ import annotations

import io
import ipaddress
import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import openpyxl
from openpyxl.styles import Alignment
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import Request as HttpRequest

from . import three_way
from .ai_agent import analyze_inventory, build_inventory_rows, chat_inventory, check_model_available, device_extra_value
from .asset_qr import (
    batch_import_qr_assets,
    create_qr_asset,
    create_qr_asset_history,
    delete_qr_asset,
    delete_qr_asset_history,
    get_qr_asset,
    get_qr_asset_by_token,
    list_qr_assets,
    render_public_html,
    render_qr_png,
    rebind_qr_asset,
    update_qr_asset,
    update_qr_asset_history,
)
from .database import (
    assign_task,
    batch_assign_tasks,
    backup_database,
    complete_ai_adjustment_task,
    complete_inventory_task,
    create_audit_log,
    create_record_correction,
    create_rule_adjustment,
    create_surplus_record,
    create_user,
    delete_user,
    delete_task,
    dispatch_ai_adjustment_task,
    claim_task,
    get_all_tasks,
    get_area_tasks,
    get_audit_logs,
    get_ai_analysis_result,
    list_ai_analysis_results,
    get_claimed_tasks,
    get_latest_ai_analysis_result,
    list_analyzed_ai_task_versions,
    get_conn,
    get_idempotent_result,
    list_ai_adjustment_tasks,
    get_my_adjustment_tasks,
    get_report,
    get_system_setting,
    get_record_by_id,
    list_record_corrections,
    get_task_by_id,
    get_task_items,
    get_asset_info_status,
    list_asset_info_imports,
    normalize_barcode,
    query_asset_info,
    get_user_by_id,
    get_user_by_openid,
    get_user_for_auth,
    get_users,
    get_workers,
    USER_AREA_CHOICES,
    USER_AREA_CORE,
    init_db,
    import_room_tasks,
    user_can_access_area,
    split_codes,
    mark_record_missing,
    normalize_user_area_name,
    reject_ai_adjustment_task,
    remind_ai_adjustment_task,
    release_task,
    save_ai_analysis_result,
    save_idempotent_result,
    set_system_setting,
    submit_scan,
    update_user,
    verify_ai_adjustment_task,
    set_password_hash,
    add_compare_status,
    apply_latest_record_correction,
    apply_resource_baseline,
    add_task_field_status,
    create_cabinet,
    CaptureConflictError,
    get_collection_warnings,
    save_admin_rack,
    void_capture_record,
    delete_task_rack,
    delete_task_surplus,
    get_task_cabinet_data,
    get_prior_room_racks,
    save_asset_info_snapshot,
    set_asset_info_display_fields,
)
from .models import (
    AdjustmentCompleteRequest,
    AdjustmentVerifyRequest,
    AdminRecordCorrectionRequest,
    CabinetCreateRequest,
    AdminRackCorrectionRequest,
    CaptureVoidRequest,
    AIAdjustmentDispatchRequest,
    AIAdjustmentRejectRequest,
    AIAdjustmentReminderRequest,
    AIChatRequest,
    AIAnalysisRequest,
    AdminLoginRequest,
    LoginRequest,
    MissingSubmitRequest,
    ProfileBindRequest,
    ManualRecordAdjustmentRequest,
    ScanResponse,
    ScanSubmitRequest,
    SurplusSubmitRequest,
    UserCreateRequest,
    UserUpdateRequest,
    UserPasswordRequest,
    UserOut,
    QrAssetBindingRequest,
    QrAssetCreateRequest,
    QrAssetHistoryCreateRequest,
    QrAssetHistoryUpdateRequest,
    QrAssetUpdateRequest,
)
from .models import ComparisonRequest, ComparisonDecisionRequest, ComparisonCaseActionRequest, LocationCodeRequest, AdminResourceCorrectionRequest
from .auth import create_access_token, decode_access_token, hash_password, require_secret_configured, verify_password
from .permissions import admin_request_allowed
from .admin_access import remote_admin_host, verify_remote_admin_request
from .admin_site import RemoteAdminSiteMiddleware


def _is_production() -> bool:
    return os.getenv("INVENTORY_ENV", "").strip().lower() in {"prod", "production"}


app = FastAPI(
    title="机房资产盘点系统",
    version="1.0.0",
    docs_url=None if _is_production() else "/docs",
    redoc_url=None if _is_production() else "/redoc",
    openapi_url=None if _is_production() else "/openapi.json",
)
MAX_EXCEL_BYTES = int(os.getenv("INVENTORY_MAX_EXCEL_MB", "20")) * 1024 * 1024
TASK_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "inventory_template.xlsx"
TASK_TEMPLATE_HEADERS = (
    "机房名称",
    "机房所在片区",
    "设备类别",
    "网元名称",
    "设备型号",
    "资产标签号",
    "设备机架",
    "生命周期状态",
    "地点编码",
)
_WECHAT_ACCESS_TOKEN_CACHE: dict[str, str | int] = {"token": "", "expires_at": 0}
_LOGIN_RATE_BUCKETS: dict[str, list[float]] = {}
_LOGIN_RATE_LOCK = Lock()


@app.exception_handler(RequestValidationError)
async def validation_error_without_input(_request: HttpRequest, exc: RequestValidationError):
    # 保留字段及错误说明；密码等原始输入不回显给浏览器或接口调用方。
    errors = [{key: value for key, value in error.items() if key not in {"input", "ctx"}} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except ValueError:
        return default


def _client_ip(request: HttpRequest) -> str:
    source = (request.client.host if request.client else "") or "unknown"
    trusted_proxies = _admin_allowed_ips(
        os.getenv("INVENTORY_TRUSTED_PROXY_IPS", "127.0.0.1,::1")
    )
    try:
        source_ip = ipaddress.ip_address(source)
        is_trusted_proxy = any(
            source_ip in ipaddress.ip_network(item, strict=False)
            for item in trusted_proxies
            if _valid_network(item)
        )
    except ValueError:
        is_trusted_proxy = False

    if is_trusted_proxy:
        for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
            value = request.headers.get(header, "").strip()
            if value:
                return value.split(",", 1)[0].strip()[:80] or "unknown"
    return source


def _valid_network(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        return False


def _admin_allowed_ips(raw: str | None = None) -> list[str]:
    value = raw if raw is not None else os.getenv("INVENTORY_ADMIN_ALLOWED_IPS", "127.0.0.1,::1")
    return [item.strip() for item in value.split(",") if item.strip()]


def _check_admin_source_allowed(request: HttpRequest) -> None:
    # 公网后台必须同时满足专用域名、Access 签名和邮箱白名单；不关闭原有 IP 保护。
    host = remote_admin_host()
    if host and request.url.hostname == host:
        verify_remote_admin_request(request)
        return
    if os.getenv("INVENTORY_ADMIN_IP_RESTRICTION_ENABLED", "1") == "0":
        return

    client = _client_ip(request)
    try:
        client_ip = ipaddress.ip_address(client)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc

    for item in _admin_allowed_ips():
        try:
            if client_ip in ipaddress.ip_network(item, strict=False):
                return
        except ValueError:
            continue
    raise HTTPException(status_code=404, detail="Not Found")


def _check_dev_login_source(request: HttpRequest) -> None:
    try:
        local = ipaddress.ip_address(_client_ip(request)).is_loopback
    except ValueError:
        local = False
    if _is_production() or not local or request.headers.get("cf-connecting-ip"):
        raise HTTPException(status_code=403, detail="测试账号仅允许本机调测")


def _is_field_or_public_api_path(method: str, path: str) -> bool:
    allowed = [
        ("POST", r"^/api/login$"),
        ("POST", r"^/api/me/profile$"),
        ("GET", r"^/api/me/profile$"),
        ("GET", r"^/api/tasks/(area|my)$"),
        ("GET", r"^/api/tasks/\d+/items$"),
        ("POST", r"^/api/tasks/\d+/(claim|release|complete)$"),
        ("POST", r"^/api/scan$"),
        ("POST", r"^/api/records/\d+/missing$"),
        ("POST", r"^/api/surplus$"),
        ("POST", r"^/api/cabinets$"),
        ("DELETE", r"^/api/tasks/\d+/cabinets/\d+$"),
        ("DELETE", r"^/api/tasks/\d+/surplus/\d+$"),
        ("GET", r"^/api/report/\d+$"),
        ("GET", r"^/api/adjustments/my$"),
        ("POST", r"^/api/adjustments/\d+/complete$"),
        ("GET", r"^/api/public/qr/[^/]+$"),
    ]
    return any(method == allowed_method and re.fullmatch(pattern, path) for allowed_method, pattern in allowed)


def _login_rate_config(scope: str) -> tuple[int, int]:
    scope_name = scope.upper()
    default_limit = 5 if scope == "admin" else 30
    limit = _env_int(f"INVENTORY_{scope_name}_LOGIN_RATE_LIMIT", default_limit)
    window = _env_int(
        f"INVENTORY_{scope_name}_LOGIN_RATE_WINDOW_SECONDS",
        _env_int("INVENTORY_LOGIN_RATE_WINDOW_SECONDS", 60),
    )
    return limit, window


def _check_login_rate_limit(request: HttpRequest, scope: str, identity: str = "") -> None:
    if os.getenv("INVENTORY_LOGIN_RATE_LIMIT_ENABLED", "1") == "0":
        return

    limit, window = _login_rate_config(scope)
    now = time.monotonic()
    keys = [f"{scope}:ip:{_client_ip(request)}"]
    if scope == "admin" and identity.strip():
        keys.append(f"{scope}:id:{identity.strip().lower()[:120]}")

    retry_after = 0
    with _LOGIN_RATE_LOCK:
        for key in keys:
            attempts = [ts for ts in _LOGIN_RATE_BUCKETS.get(key, []) if now - ts < window]
            _LOGIN_RATE_BUCKETS[key] = attempts
            if len(attempts) >= limit:
                retry_after = max(retry_after, int(window - (now - attempts[0])) + 1)
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail=f"登录尝试过于频繁，请 {retry_after} 秒后再试",
                headers={"Retry-After": str(retry_after)},
            )
        for key in keys:
            _LOGIN_RATE_BUCKETS.setdefault(key, []).append(now)


def _cors_origins() -> list[str]:
    raw = os.getenv("INVENTORY_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["http://127.0.0.1:5173"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def hide_admin_api_from_public(request: HttpRequest, call_next):
    if request.url.path.startswith("/api/") and not _is_field_or_public_api_path(request.method, request.url.path):
        try:
            _check_admin_source_allowed(request)
        except HTTPException:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


app.add_middleware(RemoteAdminSiteMiddleware)


def _ensure_default_user(wx_openid: str, name: str, role: str, area_name: str) -> dict:
    """仅创建缺失的默认账号，避免服务重启覆盖管理员维护的资料。"""
    user = get_user_by_openid(wx_openid)
    return user if user is not None else create_user(wx_openid, name, role, area_name)


@app.on_event("startup")
def on_startup():
    """启动时初始化数据库，并创建默认用户。"""
    require_secret_configured()
    init_db()
    admin = _ensure_default_user("admin_001", "管理员", "admin", USER_AREA_CORE)
    configured_password = os.getenv("INVENTORY_ADMIN_PASSWORD", "")
    if not admin.get("password_hash"):
        if not configured_password:
            raise RuntimeError("首次启动缺少 INVENTORY_ADMIN_PASSWORD，无法创建管理员密码")
        # 环境密码只用于首次初始化；后台重置的密码不能被重启覆盖。
        set_password_hash(admin["id"], hash_password(configured_password))
    _ensure_default_user("worker_001", "张三", "worker", "北区")
    _ensure_default_user("worker_002", "李四", "worker", "南区")
    _ensure_default_user("worker_003", "王五", "worker", "西区")
    _ensure_default_user("worker_004", "赵六", "worker", "南区")
    _ensure_default_user("worker_005", "周班长", "worker", USER_AREA_CORE)


def current_user(request: HttpRequest, authorization: str = Header("")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    payload = decode_access_token(authorization[7:].strip())
    try:
        user = get_user_for_auth(int(payload["sub"])) if payload else None
    except (KeyError, TypeError, ValueError):
        user = None
    if not user or payload.get("role") != user["role"] or int(payload.get("ver", -1)) != int(user.get("auth_version") or 0):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    if payload.get("method") == "dev" or (
        not payload.get("method") and str(user.get("wx_openid", "")).startswith("worker_")
    ):
        if os.getenv("INVENTORY_DEV_LOGIN", "0") != "1" or _is_production():
            raise HTTPException(status_code=401, detail="测试登录已失效，请重新登录")
        _check_dev_login_source(request)
    if user["role"] == "admin":
        # 管理员即使访问现场接口，也必须经过相同的来源及权限检查。
        _check_admin_source_allowed(request)
        if not admin_request_allowed(user, request.method, request.url.path):
            raise HTTPException(status_code=403, detail="协作管理员无权执行此操作，请联系完整管理员")
    return user


def admin_user(request: HttpRequest, user: dict = Depends(current_user)) -> dict:
    _check_admin_source_allowed(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return user


def _get_task_or_404(task_id: int) -> dict:
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _require_task_access(user: dict, task_id: int, require_claim: bool = True) -> dict:
    task = _get_task_or_404(task_id)
    if user["role"] == "admin":
        return task
    if not user_can_access_area(user["area_name"], task["area_name"]):
        raise HTTPException(status_code=403, detail="只能操作本人所属片区的任务")
    if require_claim and task.get("claimed_by") != user["id"]:
        raise HTTPException(status_code=403, detail="只能操作本人已领取的任务")
    return task


def _require_task_write_access(user: dict, task_id: int) -> dict:
    task = _require_task_access(user, task_id)
    if task.get("superseded_by_task_id"):
        raise HTTPException(status_code=409, detail="该机房有最新任务，请释放当前任务并领取最新任务")
    if task["status"] != "doing":
        raise HTTPException(status_code=409, detail="任务尚未领取或已完成锁定；有误数据请由管理员修正，严重问题转整改")
    return task


def _require_record_access(user: dict, record_id: int) -> dict:
    record = get_record_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    if record.get("is_void"):
        raise HTTPException(status_code=409, detail="该盘盈记录已作废，请刷新任务")
    _require_task_access(user, record["task_id"])
    return record


def _audit(
    actor: dict | None,
    action: str,
    target_type: str,
    target_id: int | None = None,
    detail: dict | None = None,
    request_id: str = "",
) -> None:
    create_audit_log(actor, action, target_type, target_id, detail or {}, request_id)


def _public_qr_url(token: str) -> str:
    """Build the immutable URL encoded in the QR image."""
    base_url = os.getenv("INVENTORY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=503, detail="请先配置固定的 INVENTORY_PUBLIC_BASE_URL，再下载二维码")
    return f"{base_url}/qr/{token}"


def _load_workbook(file_content: bytes):
    """从上传文件内容读取 Excel 工作簿。"""
    try:
        return openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Excel 文件读取失败，请确认上传的是有效 .xlsx 文件") from exc


async def _read_excel_upload(file: UploadFile) -> bytes:
    """读取上传的 Excel，并做基础安全校验。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".xlsx":
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 格式的 Excel 文件")
    content = await file.read(MAX_EXCEL_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Excel 文件不能为空")
    if len(content) > MAX_EXCEL_BYTES:
        max_mb = MAX_EXCEL_BYTES // 1024 // 1024
        raise HTTPException(status_code=400, detail=f"Excel 文件不能超过 {max_mb}MB")
    return content


def _excel_cell_text(value) -> str:
    """将 Excel 单元格值转成可展示、可稳定查询的文本。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _asset_info_preview(workbook, file_name: str) -> dict:
    """读取资产信息表表头和数据质量问题，不写数据库。"""
    sheets = workbook.worksheets
    if not sheets:
        raise HTTPException(status_code=400, detail="Excel 中没有工作表")
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="资产信息表为空，未找到表头")

    raw_headers = [_excel_cell_text(value) for value in rows[0]]
    if not any(raw_headers):
        raise HTTPException(status_code=400, detail="资产信息表第一行没有有效表头")
    headers = []
    duplicate_headers = set()
    seen_headers = set()
    for index, header in enumerate(raw_headers, start=1):
        name = header or f"未命名列{index}"
        if name in seen_headers:
            duplicate_headers.add(name)
        seen_headers.add(name)
        headers.append(name)
    if duplicate_headers:
        raise HTTPException(status_code=400, detail=f"资产信息表存在重复表头：{'、'.join(sorted(duplicate_headers))}")

    data_rows = []
    for excel_row, row in enumerate(rows[1:], start=2):
        fields = {
            header: _excel_cell_text(row[index]) if index < len(row) else ""
            for index, header in enumerate(headers)
        }
        if any(fields.values()):
            data_rows.append({"excel_row": excel_row, "fields": fields})
    barcode_candidates = [
        header for header in headers
        if any(token in header.lower() for token in ("资产条码", "资产编码", "条码", "asset barcode", "asset_barcode"))
    ]
    return {
        "file_name": file_name or "未命名文件.xlsx",
        "sheet_name": sheet.title,
        "sheets": [item.title for item in sheets],
        "headers": headers,
        "row_count": len(data_rows),
        "barcode_candidates": barcode_candidates,
        "rows": data_rows,
    }


def _validate_asset_info_rows(preview: dict, barcode_column: str) -> list[dict]:
    headers = preview["headers"]
    if barcode_column not in headers:
        raise HTTPException(status_code=400, detail="请选择有效的资产条码列")
    if not preview["rows"]:
        raise HTTPException(status_code=400, detail="资产信息表没有有效数据行")
    records = []
    seen: dict[str, int] = {}
    empty_rows = []
    duplicates: dict[str, list[int]] = {}
    for item in preview["rows"]:
        barcode = item["fields"].get(barcode_column, "").strip()
        if not barcode:
            empty_rows.append(item["excel_row"])
            continue
        normalized = normalize_barcode(barcode)
        if normalized in seen:
            duplicates.setdefault(normalized, [seen[normalized]]).append(item["excel_row"])
        else:
            seen[normalized] = item["excel_row"]
        records.append({"barcode": barcode, "fields": item["fields"]})
    if empty_rows:
        preview_rows = "、".join(str(row) for row in empty_rows[:20])
        suffix = "等" if len(empty_rows) > 20 else ""
        raise HTTPException(status_code=400, detail=f"资产条码列存在空值，Excel行号：{preview_rows}{suffix}")
    if duplicates:
        duplicate_text = "；".join(f"{barcode}（第{'、'.join(map(str, rows))}行）" for barcode, rows in list(duplicates.items())[:20])
        suffix = "等" if len(duplicates) > 20 else ""
        raise HTTPException(status_code=400, detail=f"资产条码必须唯一，发现重复：{duplicate_text}{suffix}")
    return records


def _build_col_map(ws) -> dict[str, int]:
    """读取第一行表头，返回列名到列号的映射。"""
    try:
        header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    except StopIteration as exc:
        raise HTTPException(status_code=400, detail="Excel 为空，未找到表头") from exc

    col_map = {}
    for idx, value in enumerate(header):
        if value is not None:
            name = str(value).strip()
            if name in col_map:
                raise HTTPException(status_code=400, detail=f"Excel 存在重复表头：{name}")
            col_map[name] = idx
    return col_map


def _find_room_col(col_map: dict[str, int]) -> str | None:
    """识别机房名称列，兼容常见命名。"""
    for col_name in ("机房名称", "所属机房", "机房"):
        if col_name in col_map:
            return col_name
    return None


def _find_area_col(col_map: dict[str, int]) -> str | None:
    """识别机房片区列，便于后续按片区下发到任务池。"""
    return _find_col(col_map, ("机房所在片区", "片区", "所属片区", "机房片区"))


def _find_col(col_map: dict[str, int], candidates: tuple[str, ...]) -> str | None:
    """按候选列名查找 Excel 表头，兼容不同资源表命名。"""
    for col_name in candidates:
        if col_name in col_map:
            return col_name
    return None


def _normalize_import_area_name(value: str | None, row_no: int | str = "") -> str:
    area_name = str(value or "").strip()
    if area_name in USER_AREA_CHOICES:
        return area_name
    location = f"第 {row_no} 行" if row_no else "当前表单"
    raise HTTPException(
        status_code=400,
        detail=f"{location}机房所在片区不合法：{area_name or '空'}。只允许：{'、'.join(USER_AREA_CHOICES)}",
    )


def _parse_excel_devices(ws, fixed_room_name: str | None = None, fixed_area_name: str = USER_AREA_CORE) -> dict[str, list[dict]]:
    """解析设备清单，并按机房名称分组。"""
    col_map = _build_col_map(ws)
    missing = [column for column in TASK_TEMPLATE_HEADERS if column not in col_map]
    if missing:
        raise HTTPException(status_code=400, detail=f"Excel 缺少模板必填列：{', '.join(missing)}")

    room_col = "机房名称"
    area_col = "机房所在片区"
    device_col = "网元名称"
    model_col = "设备型号"
    barcode_col = "资产标签号"

    extra_cols = ["设备机架", "设备类别", "所属机房业务级别", "生命周期状态", "维护专业"]
    rooms: dict[str, list[dict]] = {}
    room_codes: dict[str, str] = {}

    for row_no, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if all(cell is None for cell in row):
            continue

        raw_device_name = row[col_map[device_col]]
        if raw_device_name is None or str(raw_device_name).strip() == "":
            raise HTTPException(status_code=400, detail=f"第 {row_no} 行网元名称为空，请补齐或删除整行")

        room_name = fixed_room_name
        if room_name is None and room_col is not None:
            raw_room_name = row[col_map[room_col]]
            room_name = str(raw_room_name).strip() if raw_room_name else ""
        if not room_name:
            raise HTTPException(status_code=400, detail=f"第 {row_no} 行机房名称为空，请补齐或删除整行")
        area_name = _normalize_import_area_name(fixed_area_name)
        if fixed_room_name is None and area_col is not None:
            raw_area_name = row[col_map[area_col]]
            area_name = _normalize_import_area_name(raw_area_name, row_no)

        room_code = _excel_cell_text(row[col_map["地点编码"]]).strip()
        if not room_code:
            raise HTTPException(status_code=400, detail=f"第 {row_no} 行地点编码不能为空")
        if room_name in room_codes and room_codes[room_name] != room_code:
            raise HTTPException(status_code=400, detail=f"{room_name}对应多个地点编码")
        if any(n != room_name and c == room_code for n, c in room_codes.items()):
            raise HTTPException(status_code=400, detail=f"地点编码 {room_code} 对应多个机房")
        room_codes[room_name] = room_code

        raw_model = row[col_map[model_col]]
        raw_barcode = row[col_map[barcode_col]]
        extra = {}
        for col_name in extra_cols:
            if col_name not in col_map:
                continue
            col_idx = col_map[col_name]
            if col_idx < len(row) and row[col_idx] is not None:
                extra[col_name] = str(row[col_idx]).strip()
        if "设备机架" not in extra and "网元类型" in col_map:
            legacy_col_idx = col_map["网元类型"]
            if legacy_col_idx < len(row) and row[legacy_col_idx] is not None:
                extra["设备机架"] = str(row[legacy_col_idx]).strip()

        rooms.setdefault(room_name, []).append({
            "_row_no": row_no,
            "_area_name": area_name,
            "room_code": room_code,
            "device_name": str(raw_device_name).strip(),
            "expected_model": _excel_cell_text(raw_model),
            "expected_barcode": _excel_cell_text(raw_barcode),
            "device_extra": extra,
        })

    if not rooms:
        raise HTTPException(status_code=400, detail="Excel 中没有有效的设备数据")
    return rooms


def _preview_rooms(rooms: dict[str, list[dict]]) -> dict:
    """汇总导入质量，关键字段缺失或重复会阻止正式导入。"""
    device_keys: list[str] = []
    barcode_keys: list[str] = []
    device_rows: dict[str, list[dict]] = {}
    barcode_rows: dict[str, list[dict]] = {}
    missing_model = 0
    missing_barcode = 0
    issue_rows = []
    room_rows = []
    for room_name, devices in rooms.items():
        room_areas = sorted({(device.get("_area_name") or "").strip() for device in devices if (device.get("_area_name") or "").strip()})
        area_name = room_areas[0] if room_areas else ""
        room_rows.append({"room_name": room_name, "room_code": devices[0].get("room_code", "") if devices else "", "area_name": area_name, "device_count": len(devices)})
        if not area_name:
            issue_rows.append({
                "级别": "错误",
                "Excel行号": devices[0].get("_row_no", "") if devices else "",
                "机房名称": room_name,
                "设备名称": "",
                "问题类型": "空机房所在片区",
                "处理建议": "机房所在片区不能为空，请补齐后重新导入。",
            })
        if len(room_areas) > 1:
            issue_rows.append({
                "级别": "错误",
                "Excel行号": "",
                "机房名称": room_name,
                "设备名称": "",
                "问题类型": "同一机房片区不一致",
                "处理建议": "同一机房只能归属一个片区，请统一该机房所有行的机房所在片区。",
            })
        for device in devices:
            row_no = device.get("_row_no", "")
            device_key = f"{room_name}::{device['device_name']}"
            device_keys.append(device_key)
            device_rows.setdefault(device_key, []).append(device)
            codes = split_codes(device["expected_barcode"])
            if codes:
                for code in sorted(codes):
                    barcode_keys.append(code)
                    barcode_rows.setdefault(code, []).append(device)
            else:
                missing_barcode += 1
                issue_rows.append({
                    "级别": "错误",
                    "Excel行号": row_no,
                    "机房名称": room_name,
                    "设备名称": device["device_name"],
                    "问题类型": "空资产条码",
                    "处理建议": "资产条码不能为空；请补齐资产条码后重新导入。",
                })
            if not device["expected_model"]:
                missing_model += 1
                issue_rows.append({
                    "级别": "错误",
                    "Excel行号": row_no,
                    "机房名称": room_name,
                    "设备名称": device["device_name"],
                    "问题类型": "空设备型号",
                    "处理建议": "请补齐设备型号后重新导入。",
                })

    duplicate_devices = sorted(key.split("::", 1)[1] for key, count in Counter(device_keys).items() if count > 1)
    duplicate_barcodes = sorted(key for key, count in Counter(barcode_keys).items() if count > 1)
    for key, rows in device_rows.items():
        if len(rows) <= 1:
            continue
        room_name, device_name = key.split("::", 1)
        for device in rows:
            issue_rows.append({
                "级别": "错误",
                "Excel行号": device.get("_row_no", ""),
                "机房名称": room_name,
                "设备名称": device_name,
                "问题类型": "同一机房重复设备",
                "处理建议": "请确认是否重复导入，同一机房内设备名称应唯一。",
            })
    for barcode, rows in barcode_rows.items():
        if len(rows) <= 1:
            continue
        for device in rows:
            issue_rows.append({
                "级别": "错误",
                "Excel行号": device.get("_row_no", ""),
                "机房名称": next((name for name, devs in rooms.items() if device in devs), ""),
                "设备名称": device["device_name"],
                "问题类型": f"重复资产条码：{barcode}",
                "处理建议": "请确认资产条码是否贴错或清单重复。",
            })
    error_count = sum(1 for row in issue_rows if row.get("级别") == "错误")
    return {
        "valid": error_count == 0,
        "room_count": len(rooms),
        "device_count": sum(len(devices) for devices in rooms.values()),
        "missing_model": missing_model,
        "missing_barcode": missing_barcode,
        "duplicate_devices": duplicate_devices,
        "duplicate_barcodes": duplicate_barcodes,
        "issue_rows": sorted(issue_rows, key=lambda item: (item["级别"] != "错误", item["Excel行号"] or 0)),
        "rooms": room_rows,
    }


def _validate_user_payload(wx_openid: str | None, name: str | None, role: str | None, area_name: str | None) -> None:
    """校验账号管理字段，避免后台误保存空账号或错误角色。"""
    if wx_openid is not None and not wx_openid.strip():
        raise HTTPException(status_code=400, detail="登录编码不能为空")
    if name is not None and not name.strip():
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if role is not None and role.strip() not in {"admin", "worker"}:
        raise HTTPException(status_code=400, detail="角色只能是 admin 或 worker")
    if area_name is not None and not area_name.strip():
        raise HTTPException(status_code=400, detail="负责片区不能为空")
    if area_name is not None:
        try:
            normalize_user_area_name(area_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


def _worker_session_payload(user: dict, auth_method: str | None = None) -> dict:
    """小程序端保存到本地的用户会话字段。"""
    return {
        "user_id": user["id"],
        "name": user["name"],
        "role": user["role"],
        "area_name": user["area_name"] or USER_AREA_CORE,
        "phone": user.get("phone") or "",
        "access_token": create_access_token(user, auth_method=auth_method),
    }


def _wechat_config() -> tuple[str, str]:
    appid = os.getenv("WECHAT_APPID", "").strip()
    secret = os.getenv("WECHAT_SECRET", "").strip()
    if not appid or not secret:
        raise HTTPException(status_code=503, detail="服务器未配置 WECHAT_APPID/WECHAT_SECRET，无法获取手机号")
    return appid, secret


def _get_wechat_access_token() -> str:
    appid, secret = _wechat_config()
    now = int(time.time())
    cached_token = str(_WECHAT_ACCESS_TOKEN_CACHE.get("token") or "")
    cached_expires_at = int(_WECHAT_ACCESS_TOKEN_CACHE.get("expires_at") or 0)
    if cached_token and cached_expires_at > now + 60:
        return cached_token

    url = "https://api.weixin.qq.com/cgi-bin/token?" + urlencode({
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret,
    })
    try:
        result = json.loads(urlopen(url, timeout=8).read())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="微信 access_token 获取失败") from exc
    token = result.get("access_token", "")
    if not token:
        message = result.get("errmsg") or "微信 access_token 返回异常"
        raise HTTPException(status_code=502, detail=f"微信 access_token 获取失败：{message}")
    expires_in = int(result.get("expires_in") or 7200)
    _WECHAT_ACCESS_TOKEN_CACHE["token"] = token
    _WECHAT_ACCESS_TOKEN_CACHE["expires_at"] = now + max(expires_in - 120, 300)
    return token


def _get_phone_number_from_wechat(phone_code: str) -> str:
    code = phone_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="手机号授权凭证不能为空")
    access_token = _get_wechat_access_token()
    url = "https://api.weixin.qq.com/wxa/business/getuserphonenumber?" + urlencode({"access_token": access_token})
    payload = json.dumps({"code": code}).encode("utf-8")
    try:
        req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        result = json.loads(urlopen(req, timeout=8).read())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="微信手机号接口暂不可用") from exc

    if int(result.get("errcode") or 0) != 0:
        message = result.get("errmsg") or "微信手机号接口返回错误"
        raise HTTPException(status_code=502, detail=f"微信手机号获取失败：{message}")
    phone_info = result.get("phone_info") or {}
    phone_number = str(phone_info.get("phoneNumber") or phone_info.get("purePhoneNumber") or "").strip()
    if not phone_number:
        raise HTTPException(status_code=502, detail="微信手机号接口未返回手机号")
    return phone_number


@app.get("/api/workers", response_model=list[UserOut])
def api_workers(_: dict = Depends(admin_user)):
    """查询所有现场人员。"""
    return get_workers()


@app.get("/api/admin/me", response_model=UserOut)
def api_admin_me(user: dict = Depends(admin_user)):
    """返回数据库中的当前后台权限，避免前端依赖旧登录缓存。"""
    return user


@app.get("/api/users", response_model=list[UserOut])
def api_users(_: dict = Depends(admin_user)):
    """PC 后台查询全部小程序账号。"""
    return get_users()


@app.get("/api/users/{user_id}", response_model=UserOut | None)
def api_user(user_id: int, _: dict = Depends(admin_user)):
    """按 ID 查询用户。"""
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@app.post("/api/users", response_model=UserOut)
def api_create_user(req: UserCreateRequest, actor: dict = Depends(admin_user)):
    """完整管理员新增账号；重复编码须显式编辑，不能覆盖已有密码。"""
    _validate_user_payload(req.wx_openid, req.name, req.role, req.area_name)
    if get_user_by_openid(req.wx_openid.strip()):
        raise HTTPException(status_code=409, detail="登录编码已存在，请编辑已有账号")
    if req.role == "admin" and req.password is None:
        raise HTTPException(status_code=400, detail="请为后台管理员设置独立登录密码（12—128位）")
    if req.role != "admin" and req.password is not None:
        raise HTTPException(status_code=400, detail="现场人员通过微信登录，无需设置后台密码")
    try:
        user = create_user(
            wx_openid=req.wx_openid, name=req.name, role=req.role, area_name=req.area_name,
            phone=req.phone, employee_no=req.employee_no, is_active=1 if req.is_active else 0,
            admin_scope=req.admin_scope if req.role == "admin" else "full",
            password_hash=hash_password(req.password) if req.password is not None else None,
            update_existing=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(actor, "user.create", "user", user["id"], {"role": user["role"], "admin_scope": user["admin_scope"]})
    return user


@app.patch("/api/users/{user_id}", response_model=UserOut)
def api_update_user(user_id: int, req: UserUpdateRequest, actor: dict = Depends(admin_user)):
    """PC 后台修改小程序账号信息。"""
    data = req.model_dump(exclude_unset=True, exclude={"password"})
    _validate_user_payload(
        data.get("wx_openid"),
        data.get("name"),
        data.get("role"),
        data.get("area_name"),
    )
    existing = get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    target_role = data.get("role", existing["role"])
    if target_role == "admin" and existing["role"] != "admin" and req.password is None:
        raise HTTPException(status_code=400, detail="升级为后台管理员时，请设置独立登录密码（12—128位）")
    if target_role != "admin" and req.password is not None:
        raise HTTPException(status_code=400, detail="现场人员通过微信登录，无需设置后台密码")
    try:
        user = update_user(user_id, data, password_hash=hash_password(req.password) if req.password is not None else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _audit(actor, "user.update", "user", user_id, {"fields": sorted(data.keys())})
    if req.password is not None:
        _audit(actor, "user.password.reset", "user", user_id)
    return user


@app.post("/api/users/{user_id}/password")
def api_reset_user_password(user_id: int, req: UserPasswordRequest, actor: dict = Depends(admin_user)):
    """完整管理员重置后台密码；不返回明文或哈希，旧会话立即失效。"""
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user["role"] != "admin":
        raise HTTPException(status_code=400, detail="仅后台管理员需要设置登录密码")
    set_password_hash(user_id, hash_password(req.password))
    _audit(actor, "user.password.reset", "user", user_id)
    return {"success": True, "message": "密码已更新，请使用新密码重新登录"}


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, actor: dict = Depends(admin_user)):
    """PC 后台删除小程序账号。采用业务删除，保留历史任务引用。"""
    try:
        deleted = delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="用户不存在或已删除")
    _audit(actor, "user.delete", "user", user_id)
    return {"success": True, "message": "账号已删除"}


@app.post("/api/admin/login")
def api_admin_login(req: AdminLoginRequest, request: HttpRequest):
    _check_admin_source_allowed(request)
    _check_login_rate_limit(request, "admin", req.username)
    user = get_user_by_openid(req.username.strip())
    if not user or user["role"] != "admin" or not user["is_active"]:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not verify_password(req.password, user.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    _audit(user, "admin.login", "user", user["id"])
    return {"access_token": create_access_token(user, auth_method="password"), "token_type": "bearer", "user": UserOut(**user)}


@app.post("/api/login")
def api_login(req: LoginRequest, request: HttpRequest):
    """小程序登录；生产通过微信 code2session 获取可信 openid。"""
    _check_login_rate_limit(request, "worker")
    code = req.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="登录凭证不能为空")
    auth_method = "wechat"
    if os.getenv("INVENTORY_DEV_LOGIN", "0") == "1" and code.startswith("worker_"):
        _check_dev_login_source(request)
        openid = code
        auth_method = "dev"
    else:
        appid = os.getenv("WECHAT_APPID", "")
        secret = os.getenv("WECHAT_SECRET", "")
        if not appid or not secret:
            raise HTTPException(status_code=503, detail="服务器未配置微信登录")
        url = "https://api.weixin.qq.com/sns/jscode2session?" + urlencode({
            "appid": appid, "secret": secret, "js_code": code, "grant_type": "authorization_code"
        })
        try:
            result = json.loads(urlopen(url, timeout=8).read())
        except Exception as exc:
            raise HTTPException(status_code=502, detail="微信登录服务暂不可用") from exc
        openid = result.get("openid", "")
        if not isinstance(openid, str) or not openid.strip():
            raise HTTPException(status_code=401, detail="微信登录凭证无效，请重新登录")
    user = get_user_by_openid(openid)
    if not user:
        if os.getenv("INVENTORY_ALLOW_LOGIN_REQUESTS", "0") == "1":
            user = create_user(openid, "待绑定用户", "worker", USER_AREA_CORE, is_active=0)
            _audit(None, "worker.login_request", "user", user["id"], {"wx_openid": openid})
            raise HTTPException(status_code=403, detail="账号待管理员启用，请联系管理员")
        raise HTTPException(status_code=401, detail="登录失败，用户不存在")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="账号未启用，请联系管理员")
    _audit(user, "worker.login", "user", user["id"])
    return _worker_session_payload(user, auth_method=auth_method)


@app.post("/api/me/profile")
def api_bind_profile(req: ProfileBindRequest, user: dict = Depends(current_user)):
    """小程序提交现场人员姓名和手机号并更新当前账号资料。"""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    phone = req.phone.strip()
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise HTTPException(status_code=400, detail="请输入正确的11位手机号")
    updated_user = update_user(user["id"], {"name": name, "phone": phone})
    if updated_user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _audit(user, "worker.profile.bind", "user", user["id"], {"name_updated": True, "phone_updated": True})
    return {
        "success": True,
        "message": "手机号已更新",
        "user": _worker_session_payload(updated_user),
    }


@app.get("/api/me/profile")
def api_get_profile(user: dict = Depends(current_user)):
    """小程序进入首页时读取当前账号的最新资料，避免使用过期本地缓存。"""
    return {"user": _worker_session_payload(user)}


@app.post("/api/upload_rooms")
async def api_upload_rooms(
    file: UploadFile = File(...),
    force_import: bool = Form(False),
    actor: dict = Depends(admin_user),
):
    """上传包含多个机房的清单，按机房自动生成片区待领取任务。"""
    wb = _load_workbook(await _read_excel_upload(file))
    rooms = _parse_excel_devices(wb.active)
    preview = _preview_rooms(rooms)
    if not preview["valid"] and not force_import:
        raise HTTPException(status_code=400, detail="清单预检未通过，请先处理空资产条码、重复网元、重复标签或空设备型号")

    if any(row["问题类型"] == "同一机房片区不一致" for row in preview["issue_rows"]):
        raise HTTPException(status_code=400, detail="同一机房片区不一致，无法生成可靠的片区任务；请修正清单")
    code_conn = get_conn()
    try:
        three_way.validate_room_codes(code_conn, {name: devices[0]["room_code"] for name, devices in rooms.items()})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        code_conn.close()
    try:
        batch_id, created = import_room_tasks(rooms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    total_devices = sum(item["device_count"] for item in created)
    error_count = sum(1 for row in preview.get("issue_rows", []) if row.get("级别") == "错误")
    for item in created:
        _audit(
            actor,
            "task.import",
            "task",
            item["task_id"],
            {**item, "force_import": force_import, "error_count": error_count},
        )

    message_prefix = "已按管理员确认继续导入；" if force_import and error_count else ""
    return {
        "import_batch_id": batch_id,
        "task_count": len(created),
        "device_count": total_devices,
        "tasks": created,
        "message": f"{message_prefix}成功识别 {len(created)} 个机房，导入 {total_devices} 台设备，任务已按片区进入待领取池。",
    }


@app.post("/api/upload_rooms/preview")
async def api_preview_upload_rooms(file: UploadFile = File(...), _: dict = Depends(admin_user)):
    """预检多机房清单，不写入数据库。"""
    wb = _load_workbook(await _read_excel_upload(file))
    return _preview_rooms(_parse_excel_devices(wb.active))


@app.get("/api/upload_template")
def api_upload_template(_: dict = Depends(admin_user)):
    """下载统一任务导入模板。"""
    return Response(
        content=TASK_TEMPLATE_PATH.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''inventory_template.xlsx"},
    )


# ──────────────────────────────────────────────
# 静态资产信息表
# ──────────────────────────────────────────────

@app.get("/api/asset-info/status")
def api_asset_info_status(_: dict = Depends(admin_user)):
    """返回当前资产信息表快照和最近导入记录。"""
    return {
        "current": get_asset_info_status(),
        "history": list_asset_info_imports(),
    }


@app.post("/api/asset-info/preview")
async def api_preview_asset_info(file: UploadFile = File(...), _: dict = Depends(admin_user)):
    """预览自定义资产信息表，供管理员选择资产条码列。"""
    workbook = _load_workbook(await _read_excel_upload(file))
    preview = _asset_info_preview(workbook, file.filename or "")
    return {
        "file_name": preview["file_name"],
        "sheet_name": preview["sheet_name"],
        "sheets": preview["sheets"],
        "headers": preview["headers"],
        "row_count": preview["row_count"],
        "barcode_candidates": preview["barcode_candidates"],
    }


@app.post("/api/asset-info/import")
async def api_import_asset_info(
    file: UploadFile = File(...),
    barcode_column: str = Form(...),
    actor: dict = Depends(admin_user),
):
    """管理员确认条码列后导入资产信息表，重复条码整表拒绝。"""
    file_content = await _read_excel_upload(file)
    workbook = _load_workbook(file_content)
    preview = _asset_info_preview(workbook, file.filename or "")
    records = _validate_asset_info_rows(preview, barcode_column.strip())
    backup_path = backup_database("asset_info_import")
    status = save_asset_info_snapshot(
        file_name=preview["file_name"],
        sheet_name=preview["sheet_name"],
        barcode_column=barcode_column.strip(),
        headers=preview["headers"],
        records=records,
        imported_by=actor["id"],
        imported_by_name=actor.get("name", ""),
    )
    _audit(
        actor,
        "asset_info.batch_import",
        "asset_info_import",
        status["id"],
        {"file_name": preview["file_name"], "row_count": len(records), "barcode_column": barcode_column.strip(), "backup_path": backup_path or ""},
    )
    return {"success": True, "message": f"成功导入 {len(records)} 条资产信息", "current": status}


@app.get("/api/asset-info/query")
def api_query_asset_info(barcode: str = Query(..., min_length=1, max_length=200), _: dict = Depends(admin_user)):
    """查询当前资产信息表中的一条资产记录。"""
    normalized = normalize_barcode(barcode)
    if not normalized or normalized == "无条码":
        raise HTTPException(status_code=400, detail="无效的资产条码")
    result = query_asset_info(barcode)
    if result is None:
        return {"found": False, "barcode": barcode.strip(), "fields": {}}
    result["found"] = True
    return result


@app.post("/api/asset-info/display-fields")
def api_set_asset_info_display_fields(fields: list[str] = Body(..., embed=True), actor: dict = Depends(admin_user)):
    """配置资产条码查询弹窗展示的字段。"""
    if not fields:
        raise HTTPException(status_code=400, detail="请至少选择一个展示字段")
    status = set_asset_info_display_fields(fields)
    if status is None:
        raise HTTPException(status_code=409, detail="请先导入资产信息表")
    _audit(actor, "asset_info.display_fields.update", "asset_info_import", status["id"], {"fields": status["display_fields"]})
    return {"success": True, "message": "弹窗展示字段已保存", "current": status}


# ──────────────────────────────────────────────
# 固定二维码资产
# ──────────────────────────────────────────────

@app.get("/api/qr-assets")
def api_qr_assets(
    query: str = Query("", max_length=100),
    limit: int = Query(100, ge=1, le=500),
    _: dict = Depends(admin_user),
):
    """管理员查询二维码资产及其关联历史。"""
    return list_qr_assets(query, limit)


@app.get("/api/qr-assets/import-template")
def api_qr_asset_import_template(_: dict = Depends(admin_user)):
    """下载单表批量导入模板。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "二维码资产"
    ws.append(["资产条码", "条码型号", "条码所在机房", "当前绑定传输设备名称", "资产条码历史关联设备记录"])
    ws.append(["ASSET-001", "资产标签", "核心机房A", "PTN-02", "PTN-00；PTN-01"])
    ws.append(["ASSET-002", "资产标签", "汇聚机房B", "OTN-01", ""])
    ws.freeze_panes = "A2"
    output = io.BytesIO()
    wb.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''qr_asset_import_template.xlsx"},
    )


@app.post("/api/qr-assets/import")
async def api_import_qr_assets(file: UploadFile = File(...), actor: dict = Depends(admin_user)):
    """批量导入二维码资产；历史设备用中文分号、英文分号或换行分隔。"""
    wb = _load_workbook(await _read_excel_upload(file))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="导入文件没有数据")
    headers = [str(value or "").strip() for value in rows[0]]
    required = ["资产条码", "条码型号", "条码所在机房", "当前绑定传输设备名称", "资产条码历史关联设备记录"]
    missing = [name for name in required if name not in headers]
    if missing:
        raise HTTPException(status_code=400, detail=f"导入文件缺少列：{'、'.join(missing)}")
    indexes = {name: headers.index(name) for name in required}
    records = []
    for row in rows[1:]:
        barcode = str(row[indexes["资产条码"]] or "").strip() if indexes["资产条码"] < len(row) else ""
        if not barcode and not any(str(value or "").strip() for value in row):
            continue
        history_raw = str(row[indexes["资产条码历史关联设备记录"]] or "") if indexes["资产条码历史关联设备记录"] < len(row) else ""
        history_names = [item.strip() for item in re.split(r"[；;\r\n]+", history_raw) if item.strip()]
        records.append({
            "asset_barcode": barcode,
            "barcode_model": row[indexes["条码型号"]] if indexes["条码型号"] < len(row) else "",
            "room_name": row[indexes["条码所在机房"]] if indexes["条码所在机房"] < len(row) else "",
            "current_device_name": row[indexes["当前绑定传输设备名称"]] if indexes["当前绑定传输设备名称"] < len(row) else "",
            "history_device_names": history_names,
        })
    if not records:
        raise HTTPException(status_code=400, detail="导入文件没有有效资产数据")
    try:
        assets = batch_import_qr_assets(records, actor["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(actor, "qr_asset.batch_import", "qr_asset", None, {"created": len(assets)})
    return {"success": True, "created": len(assets), "assets": assets, "message": f"成功导入 {len(assets)} 组资产"}


@app.post("/api/qr-assets")
def api_create_qr_asset(req: QrAssetCreateRequest, actor: dict = Depends(admin_user)):
    """创建资产并生成永久不变的二维码 token。"""
    try:
        asset = create_qr_asset(
            asset_barcode=req.asset_barcode,
            barcode_model=req.barcode_model,
            room_name=req.room_name,
            current_device_name=req.current_device_name,
            current_device_ref=req.current_device_ref,
            created_by=actor["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(actor, "qr_asset.create", "qr_asset", asset["id"], {"asset_barcode": asset["asset_barcode"]})
    return asset


@app.patch("/api/qr-assets/{asset_id}")
def api_update_qr_asset(asset_id: int, req: QrAssetUpdateRequest, actor: dict = Depends(admin_user)):
    """修改资产基础字段；token 和二维码图片不变。"""
    try:
        asset = update_qr_asset(
            asset_id=asset_id,
            asset_barcode=req.asset_barcode,
            barcode_model=req.barcode_model,
            room_name=req.room_name,
            current_device_name=req.current_device_name,
            current_device_ref=req.current_device_ref,
            changed_by=actor["id"],
            change_reason=req.change_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="二维码资产不存在")
    _audit(actor, "qr_asset.update", "qr_asset", asset_id, {"fields": sorted(req.dict(exclude_unset=True).keys())})
    return asset


@app.delete("/api/qr-assets/{asset_id}")
def api_delete_qr_asset(asset_id: int, actor: dict = Depends(admin_user)):
    """停用资产组并永久保留固定编码，避免二维码被重复使用。"""
    if not delete_qr_asset(asset_id):
        raise HTTPException(status_code=404, detail="二维码资产不存在")
    _audit(actor, "qr_asset.delete", "qr_asset", asset_id)
    return {"success": True, "message": "资产组已删除，固定编码不会重新分配"}


@app.post("/api/qr-assets/{asset_id}/binding")
def api_rebind_qr_asset(asset_id: int, req: QrAssetBindingRequest, actor: dict = Depends(admin_user)):
    """变更当前传输设备，并将原设备封存到不可变历史记录。"""
    asset = rebind_qr_asset(
        asset_id=asset_id,
        device_name=req.device_name,
        device_ref=req.device_ref,
        changed_by=actor["id"],
        change_reason=req.change_reason,
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="二维码资产不存在")
    _audit(
        actor,
        "qr_asset.rebind",
        "qr_asset",
        asset_id,
        {"current_device_name": asset.get("current_device_name", ""), "change_reason": req.change_reason},
    )
    return asset


@app.post("/api/qr-assets/{asset_id}/history")
def api_create_qr_asset_history(
    asset_id: int,
    req: QrAssetHistoryCreateRequest,
    actor: dict = Depends(admin_user),
):
    try:
        history = create_qr_asset_history(
            asset_id=asset_id,
            device_name=req.device_name,
            room_name=req.room_name,
            bound_at=req.bound_at,
            unbound_at=req.unbound_at,
            change_reason=req.change_reason,
            changed_by=actor["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if history is None:
        raise HTTPException(status_code=404, detail="二维码资产不存在")
    _audit(actor, "qr_asset.history.create", "qr_asset", asset_id, {"history_id": history["id"]})
    return history


@app.patch("/api/qr-assets/{asset_id}/history/{history_id}")
def api_update_qr_asset_history(
    asset_id: int,
    history_id: int,
    req: QrAssetHistoryUpdateRequest,
    actor: dict = Depends(admin_user),
):
    try:
        history = update_qr_asset_history(
            asset_id=asset_id,
            history_id=history_id,
            device_name=req.device_name,
            room_name=req.room_name,
            bound_at=req.bound_at,
            unbound_at=req.unbound_at,
            change_reason=req.change_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if history is None:
        raise HTTPException(status_code=404, detail="历史记录不存在，或该记录为当前关联")
    _audit(actor, "qr_asset.history.update", "qr_asset", asset_id, {"history_id": history_id})
    return history


@app.delete("/api/qr-assets/{asset_id}/history/{history_id}")
def api_delete_qr_asset_history(asset_id: int, history_id: int, actor: dict = Depends(admin_user)):
    if not delete_qr_asset_history(asset_id, history_id):
        raise HTTPException(status_code=404, detail="历史记录不存在，或该记录为当前关联")
    _audit(actor, "qr_asset.history.delete", "qr_asset", asset_id, {"history_id": history_id})
    return {"success": True, "message": "历史记录已删除"}


@app.get("/api/qr-assets/{asset_id}/qrcode")
def api_qr_asset_image(asset_id: int, _: dict = Depends(admin_user)):
    """返回二维码 PNG；编码内容只包含固定 public URL。"""
    asset = get_qr_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="二维码资产不存在")
    try:
        png = render_qr_png(_public_qr_url(asset["token"]))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/qr-assets/{asset_id}")
def api_qr_asset(asset_id: int, _: dict = Depends(admin_user)):
    asset = get_qr_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="二维码资产不存在")
    return asset


@app.get("/api/public/qr/{token}")
def api_public_qr(token: str, response: Response):
    """扫码端的轻量 JSON 接口；token 是二维码中的唯一标识。"""
    asset = get_qr_asset_by_token(token)
    if asset is None:
        raise HTTPException(status_code=404, detail="二维码无效或资产已停用")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return {"asset": asset}


@app.get("/qr/{token}", response_class=HTMLResponse)
def public_qr_page(token: str):
    """手机浏览器/微信扫码后显示当前信息和历史关联设备。"""
    asset = get_qr_asset_by_token(token)
    if asset is None:
        raise HTTPException(status_code=404, detail="二维码无效或资产已停用")
    return HTMLResponse(render_public_html(asset), headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/tasks")
def api_tasks(_: dict = Depends(admin_user)):
    """查询任务列表。"""
    return get_all_tasks()


@app.get("/api/tasks/area")
def api_area_tasks(user: dict = Depends(current_user)):
    """小程序领任务页：查询当前用户所在片区的任务池。"""
    return get_area_tasks(user["id"])


@app.get("/api/tasks/my")
def api_my_tasks(user: dict = Depends(current_user)):
    """小程序我的任务页：查询当前用户已经领取的任务。"""
    return get_claimed_tasks(user["id"])


@app.post("/api/tasks/{task_id}/claim")
def api_claim_task(task_id: int, user: dict = Depends(current_user)):
    """现场人员领取所在片区的待领取任务。"""
    if user["role"] != "worker":
        raise HTTPException(status_code=403, detail="仅现场人员可领取任务")
    success, message = claim_task(task_id, user["id"])
    if not success:
        raise HTTPException(status_code=400, detail=message)
    _audit(user, "task.claim", "task", task_id)
    return {"success": True, "message": message}


@app.post("/api/tasks/{task_id}/release")
def api_release_task(
    task_id: int,
    reason: str = Body("", embed=True),
    user: dict = Depends(current_user),
):
    """现场人员释放自己的盘点中任务，任务回到待领取池。"""
    details = {}
    try:
        success, message = release_task(task_id, user["id"], reason, details=details)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=400, detail=message)
    _audit(user, "task.release", "task", task_id, {"reason": reason})
    return {"success": True, "message": message, **details}


@app.post("/api/tasks/{task_id}/complete")
def api_complete_inventory_task(task_id: int, user: dict = Depends(current_user)):
    _require_task_access(user, task_id)
    if user["role"] != "worker":
        raise HTTPException(status_code=403, detail="请由领取任务的现场人员确认完成")
    try:
        result = complete_inventory_task(task_id, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "message": "任务已确认完成并锁定，有误数据请联系管理员修正", **result}


@app.post("/api/tasks/{task_id}/assign")
def api_assign_task(task_id: int, assigned_to: int = Body(..., embed=True), actor: dict = Depends(admin_user)):
    """单个机房任务下发给指定现场人员。"""
    try:
        updated = assign_task(task_id, assigned_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="任务不存在")
    _audit(actor, "task.assign", "task", task_id, {"assigned_to": assigned_to})
    return {"success": True, "message": "任务已下发"}


@app.post("/api/tasks/batch_assign")
def api_batch_assign_tasks(assignments: list[dict[str, int]] = Body(..., embed=True), actor: dict = Depends(admin_user)):
    """批量下发多个机房任务。"""
    if not assignments:
        raise HTTPException(status_code=400, detail="请至少选择一个任务")
    if any(set(item) != {"task_id", "assigned_to"} or min(item.values()) < 1 for item in assignments):
        raise HTTPException(status_code=400, detail="每项下发必须提供正整数 task_id 和 assigned_to")
    if len({item["task_id"] for item in assignments}) != len(assignments):
        raise HTTPException(status_code=400, detail="同一任务不能重复下发")
    try:
        count = batch_assign_tasks(assignments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(actor, "task.batch_assign", "task", None, {"assignments": assignments, "updated": count})
    return {"success": True, "updated": count, "message": f"已下发 {count} 个机房任务"}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int, reason: str = "管理员作废", actor: dict = Depends(admin_user)):
    """作废某个机房盘点任务，保留审计数据但不再参与业务展示。"""
    if not reason.strip():
        raise HTTPException(status_code=400, detail="请填写作废原因")
    try:
        deleted = delete_task(task_id, reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    _audit(actor, "task.delete", "task", task_id, {"reason": reason})
    return {"success": True, "message": "任务已作废，不再参与统计"}


@app.get("/api/tasks/{task_id}/items")
def api_task_items(task_id: int, user: dict = Depends(current_user)):
    """查询某个任务下的设备清单。"""
    task = _require_task_access(user, task_id)
    items = get_task_items(task_id)
    cabinet_data = get_task_cabinet_data(task_id, items)
    for item in items:
        item["write_locked"] = task["status"] != "doing" or bool(task.get("superseded_by_task_id") or task.get("waiting_for_task_id") or item.get("has_admin_correction"))
    return {
        "task": task,
        "items": items,
        "cabinet_records": cabinet_data["cabinets"],
        "cabinet_summary": cabinet_data["summary"],
        "rack_reference": get_prior_room_racks(task_id),
    }


@app.post("/api/cabinets")
def api_create_cabinet(req: CabinetCreateRequest, user: dict = Depends(current_user)):
    """现场单独登记 ODF/IODF 机架，传输综合柜随设备盘点提交。"""
    _require_task_access(user, req.task_id)
    cached = get_idempotent_result(user["id"], "cabinet.register", req.client_request_id)
    if cached:
        if not any(r["id"] == cached["cabinet"]["id"] for r in get_task_cabinet_data(req.task_id)["cabinets"]):
            raise HTTPException(status_code=409, detail="该机架登记已删除，请重新登记")
        return cached
    _require_task_write_access(user, req.task_id)
    if not req.cabinet_type.strip():
        raise HTTPException(status_code=400, detail="请选择机架类型")
    if not req.no_barcode and not req.cabinet_barcode.strip():
        raise HTTPException(status_code=400, detail="请填写机架条码")
    if not req.rack_no.strip():
        raise HTTPException(status_code=400, detail="请填写机架编号")
    try:
        result = create_cabinet(
            task_id=req.task_id,
            cabinet_type=req.cabinet_type,
            cabinet_barcode=req.cabinet_barcode,
            no_barcode=req.no_barcode,
            rack_no=req.rack_no,
            notes=req.notes,
            created_by=user["id"],
            client_request_id=req.client_request_id,
        )
    except CaptureConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    cabinet, created = result
    message = "机架已登记" if created else "该机架已登记，本次未重复新增"
    _audit(
        user,
        "cabinet.register",
        "cabinet_record",
        cabinet.get("id"),
        {"task_id": req.task_id, "created": created, "cabinet_type": cabinet.get("cabinet_type")},
        req.client_request_id,
    )
    response = {"success": True, "created": created, "duplicate": not created, "cabinet": cabinet, "message": message, "warnings": get_collection_warnings(req.task_id, cabinet_id=cabinet["id"])}
    return save_idempotent_result(user["id"], "cabinet.register", req.client_request_id, response, "cabinet_record", cabinet["id"])


@app.delete("/api/tasks/{task_id}/cabinets/{cabinet_id}")
def api_delete_task_rack(task_id: int, cabinet_id: int, user: dict = Depends(current_user)):
    _require_task_write_access(user, task_id)
    try:
        deleted = delete_task_rack(task_id, cabinet_id, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted, "message": "机架已删除" if deleted else "该机架已删除，无需重复操作"}


@app.delete("/api/tasks/{task_id}/surplus/{record_id}")
def api_delete_task_surplus(task_id: int, record_id: int, user: dict = Depends(current_user)):
    """现场删除本次未完成任务中的误录盘盈，保留原记录和审计。"""
    _require_task_write_access(user, task_id)
    try:
        deleted = delete_task_surplus(task_id, record_id, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted, "message": "盘盈已删除" if deleted else "该盘盈已删除，无需重复操作"}


@app.post("/api/admin/tasks/{task_id}/racks")
def api_admin_create_rack(task_id: int, payload: AdminRackCorrectionRequest, actor: dict = Depends(admin_user)):
    return _save_admin_rack(task_id, payload, actor)


@app.put("/api/admin/tasks/{task_id}/racks/{rack_id}")
def api_admin_update_rack(task_id: int, rack_id: int, payload: AdminRackCorrectionRequest, actor: dict = Depends(admin_user)):
    return _save_admin_rack(task_id, payload, actor, rack_id)


def _save_admin_rack(task_id: int, payload: AdminRackCorrectionRequest, actor: dict, rack_id: int | None = None):
    _get_task_or_404(task_id)
    try:
        cabinet = save_admin_rack(task_id, payload.model_dump(), actor["id"], rack_id)
    except CaptureConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "message": "机架已更正" if rack_id else "机架已补录", "cabinet": cabinet, "warnings": get_collection_warnings(task_id, cabinet_id=cabinet["id"])}


@app.delete("/api/admin/tasks/{task_id}/racks/{rack_id}")
def api_admin_void_rack(task_id: int, rack_id: int, payload: CaptureVoidRequest, actor: dict = Depends(admin_user)):
    try:
        deleted = void_capture_record("cabinet_records", rack_id, actor["id"], payload.reason, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted, "message": "机架已作废"}


@app.delete("/api/admin/records/{record_id}")
def api_admin_void_surplus(record_id: int, payload: CaptureVoidRequest, actor: dict = Depends(admin_user)):
    try:
        deleted = void_capture_record("scan_records", record_id, actor["id"], payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted, "message": "盘盈设备已作废"}


@app.post("/api/scan", response_model=ScanResponse)
def api_scan(req: ScanSubmitRequest, user: dict = Depends(current_user)):
    """现场人员提交一条盘点结果。"""
    record = _require_record_access(user, req.record_id)
    action = "scan.submit"
    request_id = req.client_request_id.strip()
    cached = get_idempotent_result(user["id"], action, request_id)
    if cached:
        return cached
    _require_task_write_access(user, record["task_id"])
    try:
        result = submit_scan(
            record_id=req.record_id,
            asset_barcodes=req.asset_barcodes,
            asset_no_barcode=req.asset_no_barcode,
            cabinet_barcodes=req.cabinet_barcodes,
            cabinet_no_barcode=req.cabinet_no_barcode,
            rack_no=req.rack_no,
            actual_model=req.actual_model,
            notes=req.notes,
            created_by=user["id"],
            device_name=req.device_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    response = {
        "success": True,
        "record_id": result["id"],
        "match_status": result["match_status"],
        "asset_match_status": result["asset_match_status"],
        "model_match_status": result["model_match_status"],
        "record_type": result["record_type"],
        "message": f"盘点完成，比对结果：{result['match_status']}",
        "warnings": get_collection_warnings(record["task_id"], record_id=result["id"]),
    }
    response = save_idempotent_result(user["id"], action, request_id, response, "scan_record", result["id"])
    _audit(
        user,
        action,
        "scan_record",
        result["id"],
        {"match_status": result["match_status"], "asset_match_status": result["asset_match_status"], "model_match_status": result["model_match_status"]},
        request_id,
    )
    return response


@app.post("/api/records/{record_id}/missing", response_model=ScanResponse)
def api_mark_missing(record_id: int, req: MissingSubmitRequest, user: dict = Depends(current_user)):
    """把任务清单内的设备标记为盘亏：现场应有但未找到。"""
    record = _require_record_access(user, record_id)
    action = "record.missing"
    request_id = req.client_request_id.strip()
    cached = get_idempotent_result(user["id"], action, request_id)
    if cached:
        return cached
    _require_task_write_access(user, record["task_id"])
    try:
        result = mark_record_missing(record_id, req.reason, req.notes, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    response = {
        "success": True,
        "record_id": result["id"],
        "match_status": result["match_status"],
        "asset_match_status": result["asset_match_status"],
        "model_match_status": result["model_match_status"],
        "message": "已标记为盘亏，可在后台 AI 模型分析中核查",
        "record_type": result["record_type"],
    }
    response = save_idempotent_result(user["id"], action, request_id, response, "scan_record", result["id"])
    _audit(user, action, "scan_record", result["id"], {"reason": req.reason}, request_id)
    return response


@app.post("/api/surplus")
def api_create_surplus(req: SurplusSubmitRequest, user: dict = Depends(current_user)):
    """登记盘盈设备：现场多出、任务清单中没有的设备。"""
    _require_task_access(user, req.task_id)
    action = "record.surplus"
    request_id = req.client_request_id.strip()
    cached = get_idempotent_result(user["id"], action, request_id)
    if cached:
        current = get_record_by_id(cached["record_id"])
        if current and current.get("is_void"):
            raise HTTPException(status_code=409, detail="该盘盈记录已作废，请刷新任务后重新登记")
        return cached
    _require_task_write_access(user, req.task_id)
    try:
        record = create_surplus_record(
            task_id=req.task_id,
            device_name=req.device_name,
            actual_model=req.actual_model,
            asset_barcodes=req.asset_barcodes,
            asset_no_barcode=req.asset_no_barcode,
            cabinet_barcodes=req.cabinet_barcodes,
            cabinet_no_barcode=req.cabinet_no_barcode,
            rack_no=req.rack_no,
            notes=req.notes,
            created_by=user["id"],
            client_request_id=request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    response = {
        "success": True,
        "record_id": record["id"],
        "record": record,
        "match_status": record["match_status"],
        "asset_match_status": record["asset_match_status"],
        "model_match_status": record["model_match_status"],
        "message": "盘盈设备已登记，可在后台 AI 模型分析中核查",
        "warnings": get_collection_warnings(req.task_id, record_id=record["id"]),
    }
    response = save_idempotent_result(user["id"], action, request_id, response, "scan_record", record["id"])
    _audit(user, action, "scan_record", record["id"], {"task_id": req.task_id, "device_name": req.device_name}, request_id)
    return response


@app.get("/api/audit_logs")
def api_audit_logs(limit: int = 200, _: dict = Depends(admin_user)):
    """查询最近操作审计日志。"""
    return get_audit_logs(limit)


@app.get("/api/report/{task_id}")
def api_report(task_id: int, user: dict = Depends(current_user)):
    """获取单个任务盘点报告。"""
    task = _require_task_access(user, task_id)
    records = get_report(task_id)
    cabinet_data = get_task_cabinet_data(task_id, records)
    inventory_records = [r for r in records if (r.get("record_type") or "normal") != "surplus"]
    return {
        "task": task,
        "records": records,
        "cabinet_records": cabinet_data["cabinets"],
        "cabinet_summary": cabinet_data["summary"],
        "summary": {
            "total": len(inventory_records),
            "已盘": sum(1 for r in inventory_records if r["match_status"] != "未扫"),
            "未盘": sum(1 for r in inventory_records if r["match_status"] == "未扫"),
        },
    }


@app.get("/api/admin/records/{record_id}/corrections")
def api_record_corrections(record_id: int, _: dict = Depends(admin_user)):
    """管理员查看一条现场反馈的历次修正记录。"""
    record = get_record_by_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    return {"record": record, "corrections": list_record_corrections(record_id)}


@app.post("/api/admin/records/{record_id}/adjustments")
def api_manual_record_adjustment(
    record_id: int, payload: ManualRecordAdjustmentRequest, actor: dict = Depends(admin_user),
):
    """管理员人工确认严重问题，直接加入待审核整改台账。"""
    record = _require_record_access(actor, record_id)
    task = _get_task_or_404(record["task_id"])
    if task["status"] != "done":
        raise HTTPException(status_code=409, detail="请待现场确认任务完成后再转整改")
    if not all(value.strip() for value in (payload.problem_type, payload.basis, payload.suggestion)):
        raise HTTPException(status_code=400, detail="请填写问题类型、确认依据和整改要求")
    result = _three_way_call(three_way.read, [task["id"]])
    if result["warnings"]:
        raise HTTPException(status_code=400, detail="；".join(result["warnings"]))
    candidates = [g for g in result["groups"] if any(r.get("record_id") == record_id for system in three_way.SYSTEMS for r in three_way.source_records(g, system))]
    group = next((g for g in candidates if any(r.get("record_id") == record_id for r in three_way.source_records(g, "现场"))), candidates[0] if candidates else None)
    if not group:
        raise HTTPException(status_code=400, detail="设备暂未形成三方对照组，请先核查条码和来源")
    case_id = _three_way_call(three_way.create_case, [task["id"]], group["group_key"], group["fingerprint"], actor["id"], payload.model_dump())
    return {"success": True, "message": "已转入整改台账，待管理员审核处理责任", "item": three_way.case_detail(case_id)}


@app.post("/api/admin/records/{record_id}/corrections")
def api_create_record_correction(
    record_id: int,
    payload: AdminRecordCorrectionRequest,
    actor: dict = Depends(admin_user),
):
    """管理员新增一版现场反馈修正，保留小程序原始值。"""
    before = get_record_by_id(record_id)
    if before is None:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    try:
        record = create_record_correction(
            record_id,
            payload.model_dump(),
            payload.correction_reason,
            payload.correction_note,
            actor["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    changed_fields = {
        field: {"before": before.get(field), "after": record.get(field)}
        for field in (
            "device_name", "actual_model", "asset_barcodes", "asset_no_barcode", "cabinet_barcodes",
            "cabinet_no_barcode", "rack_no", "notes", "record_type", "missing_reason",
        )
        if before.get(field) != record.get(field)
    }
    _audit(actor, "scan_record.correct", "scan_record", record_id, {
        "correction_id": record.get("latest_correction_id"),
        "correction_reason": payload.correction_reason,
        "correction_note": payload.correction_note,
        "changed_fields": changed_fields,
        "related_adjustment_ids": record.get("related_adjustment_ids") or [],
    })
    return {
        "success": True,
        "message": "现场反馈已修正并重新核对",
        "record": record,
        "related_adjustment_ids": record.get("related_adjustment_ids") or [],
        "warnings": get_collection_warnings(record["task_id"], record_id=record_id),
    }


def _autosize_workbook(wb: openpyxl.Workbook) -> None:
    """根据内容自动调整 Excel 列宽，避免导出后字段挤在一起。"""
    for sheet in wb.worksheets:
        for column in sheet.columns:
            max_len = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 36)


def _excel_response(wb: openpyxl.Workbook, filename: str) -> Response:
    """把工作簿统一转成下载响应。"""
    # 用户字段（如备注以 = 开头）按文本导出，不能变成可执行 Excel 公式。
    for sheet in wb.worksheets:
        for row in sheet:
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
    _autosize_workbook(wb)
    output = io.BytesIO()
    wb.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


INVENTORY_DATA_HEADERS = [
    "任务ID", "导入批次", "任务名称", "机房名称", "片区", "领取人", "任务状态", "任务创建时间",
    "领取时间", "现场完成时间", "设备名称", "清单设备型号", "清单资产条码", "清单设备机架",
    "现场设备型号", "现场资产条码", "资产无条码", "现场机柜条码", "机柜无条码",
    "现场机架号", "现场状态", "盘点时间", "盘亏原因", "现场备注",
]
CABINET_COMMON_HEADERS = ["任务ID", "导入批次", "任务名称", "机房名称", "片区"]
EQUIPMENT_CABINET_HEADERS = CABINET_COMMON_HEADERS + ["机柜资产条码", "无条码", "设备机柜位", "登记来源", "登记人", "登记时间", "备注"]
RACK_DATA_HEADERS = CABINET_COMMON_HEADERS + ["机架类型", "机架资产条码", "无条码", "资源名称/机架编号", "登记来源", "登记人", "登记时间", "备注"]


def _append_inventory_data_sheet(wb: openpyxl.Workbook, tasks: list[dict]) -> None:
    """导出任务清单和现场盘点合并后的原始数据。"""
    ws = wb.active
    ws.title = "资产盘点数据"
    labels = {"清单设备机架": "清单设备机柜位", "现场机架号": "现场设备机柜位"}
    ws.append([labels.get(header, header) for header in INVENTORY_DATA_HEADERS])
    for row in build_inventory_rows(tasks):
        ws.append([row.get(header) or "" for header in INVENTORY_DATA_HEADERS])


def _append_cabinet_data_sheet(wb: openpyxl.Workbook, tasks: list[dict]) -> None:
    """传输综合柜随设备、ODF/IODF 独立盘点，分别导出。"""
    equipment_ws = wb.create_sheet("传输综合柜明细")
    rack_ws = wb.create_sheet("ODF-IODF机架明细")
    equipment_ws.append(EQUIPMENT_CABINET_HEADERS)
    rack_ws.append(RACK_DATA_HEADERS)
    seen_ids: set[int | str] = set()
    for task in tasks:
        for cabinet in task.get("cabinets") or []:
            if cabinet.get("id") in seen_ids:
                continue
            seen_ids.add(cabinet.get("id"))
            row = {
                "任务ID": task.get("id"),
                "导入批次": task.get("import_batch_id") or "",
                "任务名称": task.get("task_name"),
                "机房名称": task.get("room_name"),
                "片区": task.get("area_name") or USER_AREA_CORE,
                "无条码": "是" if cabinet.get("no_barcode") else "否",
                "登记来源": cabinet.get("source_label") or cabinet.get("source"),
                "登记人": cabinet.get("created_by_name") or "",
                "登记时间": cabinet.get("scanned_at"),
                "备注": cabinet.get("notes"),
            }
            barcode = cabinet.get("cabinet_barcode") or ("无条码" if cabinet.get("no_barcode") else "")
            name = cabinet.get("rack_no") or cabinet.get("cabinet_name")
            if cabinet.get("cabinet_type") == "equipment":
                row.update({"机柜资产条码": barcode, "设备机柜位": name})
                equipment_ws.append([row.get(header) or "" for header in EQUIPMENT_CABINET_HEADERS])
            else:
                row.update({"机架类型": cabinet.get("cabinet_type_label"), "机架资产条码": barcode, "资源名称/机架编号": name})
                rack_ws.append([row.get(header) or "" for header in RACK_DATA_HEADERS])


@app.get("/api/export/{task_id}")
def api_export(task_id: int, _: dict = Depends(admin_user)):
    """导出单个任务的资产盘点原始数据。"""
    task = _get_task_or_404(task_id)
    records = get_report(task_id)
    task["records"] = records
    task["cabinets"] = get_task_cabinet_data(task_id, task["records"])["cabinets"]

    wb = openpyxl.Workbook()
    _append_inventory_data_sheet(wb, [task])
    _append_cabinet_data_sheet(wb, [task])
    return _excel_response(wb, f"资产盘点数据_{task['room_name']}_{task_id}.xlsx")


@app.get("/api/ai/export/inventory")
def api_ai_export_inventory(task_ids: list[int] = Query(default=[]), actor: dict = Depends(admin_user)):
    """管理员确认后导出当前选定任务的资产盘点数据。"""
    selected_ids = sorted({int(task_id) for task_id in task_ids if int(task_id) > 0})
    if not selected_ids:
        raise HTTPException(status_code=400, detail="请先选择需要导出的任务")
    tasks = []
    for task_id in selected_ids:
        task = _get_task_or_404(task_id)
        task["records"] = get_report(task_id)
        task["cabinets"] = get_task_cabinet_data(task_id, task["records"])["cabinets"]
        tasks.append(task)

    wb = openpyxl.Workbook()
    _append_inventory_data_sheet(wb, tasks)
    _append_cabinet_data_sheet(wb, tasks)
    _audit(actor, "ai.export_inventory", "ai_export", None, {"task_ids": selected_ids})
    return _excel_response(wb, f"资产盘点数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")


@app.get("/api/summary")
def api_summary(_: dict = Depends(admin_user)):
    """一次性返回所有任务和盘点记录，供 PC 报告页使用。"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT t.id, t.task_name, t.room_name, t.area_name, t.status, t.created_at, t.assigned_to,
                   t.import_batch_id, t.room_code,
                   t.assigned_at, t.completed_at, t.field_completed_at,
                   u.name as assigned_name,
                   sr.id as record_id, sr.device_name, sr.device_extra, sr.expected_model,
                   sr.expected_barcode, sr.asset_barcodes, sr.asset_no_barcode,
                   sr.cabinet_barcodes, sr.cabinet_no_barcode, sr.rack_no,
                    sr.actual_model, sr.notes, sr.match_status, sr.scanned_at,
                    sr.record_type, sr.field_status, sr.missing_reason, sr.created_by
            FROM tasks t
            LEFT JOIN users u ON u.id = t.assigned_to
            LEFT JOIN scan_records sr ON sr.task_id = t.id AND sr.is_void=0
            WHERE t.is_deleted = 0
            ORDER BY t.created_at DESC, sr.id
        """).fetchall()

        tasks_map = {}
        baseline_cache = {}
        for row in rows:
            data = dict(row)
            task_id = data["id"]
            if task_id not in tasks_map:
                tasks_map[task_id] = {
                    "id": task_id,
                    "import_batch_id": data["import_batch_id"],
                    "task_name": data["task_name"],
                    "room_name": data["room_name"],
                    "room_code": data["room_code"],
                    "area_name": data["area_name"] or USER_AREA_CORE,
                    "status": data["status"],
                    "created_at": data["created_at"],
                    "assigned_at": data["assigned_at"],
                    "completed_at": data["completed_at"],
                    "field_completed_at": data["field_completed_at"],
                    "assigned_name": data["assigned_name"],
                    "total": 0,
                    "scanned": 0,
                    "last_scanned_at": "",
                    "records": [],
                }
            if data["record_id"]:
                record = add_compare_status(apply_resource_baseline(conn, apply_latest_record_correction(conn, {
                    "id": data["record_id"],
                    "task_id": task_id,
                    "device_name": data["device_name"],
                    "expected_rack_no": device_extra_value(data, "设备机架"),
                    "expected_model": data["expected_model"],
                    "expected_barcode": data["expected_barcode"],
                    "asset_barcodes": data["asset_barcodes"],
                    "asset_no_barcode": data["asset_no_barcode"],
                    "cabinet_barcodes": data["cabinet_barcodes"],
                    "cabinet_no_barcode": data["cabinet_no_barcode"],
                    "rack_no": data["rack_no"],
                    "actual_model": data["actual_model"],
                    "notes": data["notes"],
                    "match_status": data["match_status"],
                    "scanned_at": data["scanned_at"],
                    "record_type": data["record_type"],
                    "field_status": data["field_status"],
                    "missing_reason": data["missing_reason"],
                    "created_by": data["created_by"],
                }), baseline_cache))
                tasks_map[task_id]["records"].append(record)
                if record["scanned_at"]:
                    current_last = tasks_map[task_id]["last_scanned_at"]
                    tasks_map[task_id]["last_scanned_at"] = max(current_last, record["scanned_at"])

        result = list(tasks_map.values())
        for task in result:
            inventory_records = [r for r in task["records"] if (r.get("record_type") or "normal") != "surplus"]
            task["total"] = len(inventory_records)
            task["scanned"] = sum(1 for r in inventory_records if r["match_status"] != "未扫")
            cabinet_data = get_task_cabinet_data(task["id"], task["records"])
            task["cabinets"] = cabinet_data["cabinets"]
            task["cabinet_summary"] = cabinet_data["summary"]
            add_task_field_status(task)
        return result
    finally:
        conn.close()


def _three_way_call(function, *args):
    try:
        return function(*args)
    except ValueError as exc:
        raise HTTPException(status_code=409 if any(word in str(exc) for word in ("已有整改单", "已更新", "已变化")) else 400, detail=str(exc)) from exc


@app.get("/api/reconciliation")
def api_reconciliation(task_ids: list[int] = Query(default=[]), _: dict = Depends(admin_user)):
    return _three_way_call(three_way.read_completed, task_ids)


@app.get("/api/reconciliation/export")
def api_export_comparison(task_ids: list[int] = Query(default=[]), _: dict = Depends(admin_user)):
    result = _three_way_call(three_way.read_completed, task_ids)
    if result["warnings"]:
        raise HTTPException(status_code=400, detail="；".join(result["warnings"]))
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "三系统比对"
    ws.append(["关联键：网元 / 资源名称", "系统", "资产条码", "设备机柜位", "地点编码", "规格型号",
               "比对结果", "问题明细", "机房", "资产版本", "综资版本", "数据来源"])
    for group in result["groups"]:
        start_row = ws.max_row + 1
        manual_values = (group.get("manual_decision") or {}).get("values", {})
        key = ""
        for system in ("综资", "现场", "资产"):
            manual = manual_values.get(system, {})
            key = manual.get("name") if "name" in manual else next(
                (row["name"] for row in three_way.source_records(group, system) if row["name"]), "")
            if key:
                break
        key = key or "未填写"
        checks = "\n".join(f"{pair}：" + "；".join(f"{'设备机柜位' if label == '机架号' else label}{status}"
                              for label, status in values.items()) for pair, values in group["checks"].items())
        for system in ("综资", "现场", "资产"):
            rows = three_way.source_records(group, system)
            manual = manual_values.get(system, {})
            missing = "无对应设备" if system == "综资" else (
                "登记盘亏" if group["stage_one_status"] == "现场登记盘亏" else "未反馈") if system == "现场" else "缺失"
            values = []
            for field in ("barcode", "rack_no", "location_code", "model"):
                if field == "rack_no" and system == "资产":
                    value = "不参与"
                elif field in manual:
                    value = manual[field] or "未填写"
                elif not rows:
                    value = missing
                else:
                    value = "\n".join(row.get(field) or (
                        row.get("barcode_state") or "未填写（待补录）" if field == "barcode" else "未填写") for row in rows)
                values.append(value)
            provenance = {"综资": "综资基线", "资产": "最新资产信息表"}.get(system, "")
            if system == "现场":
                provenance = "；".join(dict.fromkeys(row.get("source_label") or "现场原始反馈" for row in rows))
            if group.get("pending_sources", {}).get(system):
                provenance += "；已有记录，条码待补录"
            if manual:
                provenance += "；管理员人工核对"
            ws.append([key, system, *values, checks, "；".join(group["problems"]), group["room_name"],
                       group["versions"]["asset_batch_id"], json.dumps(group["versions"]["resources"]), provenance])
        for column in (1, 7, 8, 9, 10, 11):
            ws.merge_cells(start_row=start_row, start_column=column, end_row=ws.max_row, end_column=column)
    ws.freeze_panes = "C2"
    if result.get("cabinet_comparison"):
        cabinet_ws = workbook.create_sheet("传输综合柜核对")
        cabinet_ws.append(["机柜资源 / 柜位", "系统", "机柜资产条码", "机柜位", "地点编码", "核对结论", "问题明细", "机房"])
        for cabinet in result["cabinet_comparison"]:
            start_row = cabinet_ws.max_row + 1
            checks = f"{cabinet['status']}；综资与现场柜位{cabinet['position_status']}；现场与资产条码{cabinet['barcode_status']}、地点{cabinet['location_status']}"
            for system in ("综资", "现场", "资产"):
                if system == "综资":
                    barcode, position = cabinet["resource_barcode_status"], cabinet["resource_position"] or "未找到"
                    code = cabinet["room_code"] if cabinet["resource_devices"] else "未提供"
                elif system == "现场":
                    barcode, position = cabinet["field_barcodes"], cabinet["field_position"] or "未反馈"
                    code = cabinet["room_code"] if cabinet["field_devices"] else "未反馈"
                else:
                    barcode = cabinet["asset_barcodes"] or "未找到"
                    position = cabinet["position"] if cabinet["asset_records"] else "未找到"
                    code = "、".join(dict.fromkeys(a["location_code"] for a in cabinet["asset_records"])) or "未找到"
                cabinet_ws.append([cabinet["resource_name"], system, barcode, position, code, checks,
                                   "；".join(cabinet["problems"]), cabinet["room_name"]])
            for column in (1, 6, 7, 8):
                cabinet_ws.merge_cells(start_row=start_row, start_column=column, end_row=cabinet_ws.max_row, end_column=column)
        cabinet_ws.freeze_panes = "C2"
    for sheet in workbook:
        for row in sheet:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    return _excel_response(workbook, "three_way_comparison.xlsx")


@app.post("/api/reconciliation/decisions")
def api_comparison_decision(payload: ComparisonDecisionRequest, actor: dict = Depends(admin_user)):
    payload = payload.model_dump()
    return _three_way_call(three_way.save_decision, payload.get("task_ids", []), payload.get("group_key"),
                           payload.get("fingerprint"), payload.get("kind"), payload.get("decision", {}), actor["id"])


@app.post("/api/reconciliation/adjustments")
def api_create_reconciliation_adjustment(payload: ComparisonRequest, actor: dict = Depends(admin_user)):
    payload = payload.model_dump()
    case_id = _three_way_call(three_way.create_case, payload.get("task_ids", []), payload.get("group_key"),
                              payload.get("fingerprint"), actor["id"])
    return {"success": True, "message": "已建单，进入整改台账待审核", "item": three_way.case_detail(case_id)}


@app.post("/api/reconciliation/tasks/{task_id}/location")
def api_fill_location(task_id: int, payload: LocationCodeRequest, actor: dict = Depends(admin_user)):
    _three_way_call(three_way.set_room_code, task_id, payload.location_code, actor["id"])
    return {"success": True}


@app.post("/api/reconciliation/tasks/{task_id}/resources")
async def api_update_resources(task_id: int, file: UploadFile = File(...), actor: dict = Depends(admin_user)):
    task = _get_task_or_404(task_id)
    rooms = _parse_excel_devices(_load_workbook(await _read_excel_upload(file)).active)
    if set(rooms) != {task["room_name"]}:
        raise HTTPException(status_code=400, detail="本次更新仅接受所选机房的完整综资清单，请勿混入其他机房")
    _three_way_call(three_way.replace_baseline, task_id, rooms[task["room_name"]], actor["id"])
    return {"success": True, "message": "综资基线已更新，现场结果和历史清单已保留"}


@app.get("/api/admin/records/{record_id}/resource")
def api_resource_record(record_id: int, _: dict = Depends(admin_user)):
    return _three_way_call(three_way.resource_record, record_id)


@app.post("/api/admin/records/{record_id}/resource")
def api_update_resource_record(record_id: int, payload: AdminResourceCorrectionRequest, actor: dict = Depends(admin_user)):
    return _three_way_call(three_way.update_resource_record, record_id, payload.model_dump(), payload.fingerprint, actor["id"], payload.note)


@app.get("/api/reconciliation/cases/{case_id}")
def api_comparison_case(case_id: int, _: dict = Depends(admin_user)):
    return _three_way_call(three_way.case_detail, case_id)


@app.post("/api/reconciliation/cases/{case_id}/{action}")
def api_comparison_case_action(case_id: int, action: str, payload: ComparisonCaseActionRequest, actor: dict = Depends(admin_user)):
    if action not in {"approve", "admin_complete", "reject_review", "return", "verify"}:
        raise HTTPException(status_code=400, detail="操作无效")
    item = _three_way_call(three_way.case_action, case_id, action, actor["id"], payload.model_dump())
    return {"success": True, "item": item}


@app.post("/api/ai/analyze")
def api_ai_analyze(payload: AIAnalysisRequest, actor: dict = Depends(admin_user)):
    """对选中任务做只读 AI 分析，发送前先脱敏。"""
    ai_status = _ai_status_payload()
    if not ai_status["enabled"]:
        raise HTTPException(status_code=400, detail="AI 模型分析已关闭")
    if not ai_status["available"]:
        raise HTTPException(status_code=400, detail="AI 模型未配置，请先配置 DeepSeek API Key 或本地 agent 服务")

    task_ids = sorted({int(task_id) for task_id in payload.task_ids if int(task_id) > 0})
    if not task_ids:
        raise HTTPException(status_code=400, detail="请先选择需要分析的任务")

    tasks = []
    for task_id in task_ids:
        task = _get_task_or_404(task_id)
        task["records"] = get_report(task_id)
        tasks.append(task)

    if len({task.get("import_batch_id") for task in tasks}) > 1:
        raise HTTPException(status_code=400, detail="不同导入批次请分别生成 AI 分析报告，避免混用往期现场数据")

    unfinished_tasks = [
        task for task in tasks
        if task.get("status") != "done" or not (task.get("field_completed_at") or task.get("completed_at"))
    ]
    if unfinished_tasks:
        labels = "、".join(f"{task['room_name']}(#{task['id']})" for task in unfinished_tasks[:5])
        raise HTTPException(status_code=400, detail=f"只能分析现场已完成的任务：{labels} 尚未完成")

    comparison = _three_way_call(three_way.read, task_ids)
    if comparison["warnings"]:
        raise HTTPException(status_code=400, detail="；".join(comparison["warnings"]))
    # 同一现场完成批次可在源数据或人工判断变化后重新分析；重复请求受内容版本约束。
    content_version = three_way.fingerprint([{
        "key": g["group_key"], "data": g["fingerprint"], "model": g["model_status"],
        "manual": g["manual_decision"], "versions": g["versions"],
    } for g in comparison["groups"]])
    task_completion_versions = [(int(task["id"]), "three-way:" + content_version) for task in tasks]
    existing = list_analyzed_ai_task_versions(task_completion_versions)
    if existing:
        return _saved_ai_report(get_ai_analysis_result(existing[0]["analysis_id"]), reused=True)

    analysis_scope = "、".join(f"{task['room_name']}({task['id']})" for task in tasks)
    try:
        result = analyze_inventory(tasks, comparison=comparison)
        analysis_result = json.dumps(result["analysis_items"], ensure_ascii=False)
        status = "success"
        error_message = ""
    except Exception as exc:
        result = {
            "model_provider": "deepseek",
            "model_name": os.getenv("DEEPSEEK_MODEL", ""),
            "prompt_version": "",
            "input_summary": "",
            "desensitized_payload": "",
            "context_meta": {},
            "tool_manifest": [],
            "analysis_items": [],
        }
        analysis_result = "[]"
        status = "failed"
        error_message = str(exc)

    try:
        analysis_id = save_ai_analysis_result(
            task_ids=task_ids,
            analysis_scope=analysis_scope,
            model_provider=result["model_provider"],
            model_name=result["model_name"],
            prompt_version=result["prompt_version"],
            input_summary=result["input_summary"],
            desensitized_payload=result["desensitized_payload"],
            context_meta=json.dumps(result.get("context_meta") or {}, ensure_ascii=False),
            analysis_result=analysis_result,
            status=status,
            error_message=error_message,
            created_by=actor.get("id"),
            task_completion_versions=task_completion_versions,
        )
    except ValueError as exc:
        existing = list_analyzed_ai_task_versions(task_completion_versions)
        if existing:
            return _saved_ai_report(get_ai_analysis_result(existing[0]["analysis_id"]), reused=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(actor, "ai.analyze", "ai_analysis", analysis_id, {
        "task_ids": task_ids,
        "status": status,
        "context_meta": result.get("context_meta") or {},
        "adjustment_draft_count": 0,
    })
    return {
        **_saved_ai_report(get_ai_analysis_result(analysis_id)),
        "id": analysis_id,
        "status": status,
        "error_message": error_message,
        "analysis_items": result["analysis_items"],
        "input_summary": result["input_summary"],
        "model_provider": result["model_provider"],
        "model_name": result["model_name"],
        "context_meta": result.get("context_meta") or {},
        "tool_manifest": result.get("tool_manifest") or [],
        "adjustment_drafts": [],
    }


@app.post("/api/ai/chat")
def api_ai_chat(payload: AIChatRequest, actor: dict = Depends(admin_user)):
    """管理员与本地 agent 对话；后端受控取数并脱敏后再发送。"""
    ai_status = _ai_status_payload()
    if not ai_status["enabled"]:
        raise HTTPException(status_code=400, detail="AI 模型分析已关闭")
    if not ai_status["available"]:
        raise HTTPException(status_code=400, detail="AI 模型未配置，请先配置 DeepSeek API Key 或本地 agent 服务")

    task_ids = sorted({int(task_id) for task_id in payload.task_ids if int(task_id) > 0})
    tasks = []
    for task_id in task_ids:
        task = _get_task_or_404(task_id)
        task["records"] = get_report(task_id)
        tasks.append(task)

    adjustments = list_ai_adjustment_tasks()
    if task_ids:
        selected = set(task_ids)
        adjustments = [
            item for item in adjustments
            if selected.intersection(item.get("source_task_ids") or [])
        ]
    safe_adjustments = [
        {
            "adjustment_id": item.get("id"),
            "source_task_ids": item.get("source_task_ids") or [],
            "status": item.get("status") or "",
            "risk_level": item.get("risk_level") or "",
            "priority": item.get("priority") or "",
            "created_at": item.get("created_at") or "",
            "dispatched_at": item.get("dispatched_at") or "",
            "completed_at": item.get("completed_at") or "",
            "reminder_count": item.get("reminder_count") or 0,
            "reminded_at": item.get("reminded_at") or "",
        }
        for item in adjustments
    ]

    try:
        result = chat_inventory(
            message=payload.message,
            tasks=tasks,
            history=[item.dict() for item in payload.history],
            adjustments=safe_adjustments,
            comparison=_three_way_call(three_way.read, task_ids) if tasks and all(t["status"] == "done" for t in tasks) else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _audit(actor, "ai.chat", "ai_chat", None, {
        "task_ids": task_ids,
        "context_meta": result.get("context_meta") or {},
    })
    return result


AI_REPORT_HEADERS = ["序号", "问题类型", "风险等级", "涉及片区", "涉及机房", "涉及设备", "判断依据", "整改建议", "优先级"]


def _load_ai_analysis_items(result: dict) -> list[dict]:
    try:
        items = json.loads(result.get("analysis_result") or "[]")
    except json.JSONDecodeError:
        return []
    return items if isinstance(items, list) else []


def _saved_ai_report(result: dict | None, reused: bool = False) -> dict:
    if result is None:
        raise HTTPException(status_code=404, detail="AI 分析报告不存在")
    fields = ("id", "analysis_scope", "model_provider", "model_name", "prompt_version", "input_summary",
              "status", "error_message", "created_at")
    return {**{key: result.get(key) for key in fields}, "task_ids": json.loads(result["task_ids_json"]),
            "analysis_items": _load_ai_analysis_items(result), "reused": reused}


@app.get("/api/ai/results")
def api_ai_results(task_ids: list[int] = Query(default=[]), page: int = Query(default=1, ge=1),
                   page_size: int = Query(default=20, ge=1, le=100), _: dict = Depends(admin_user)):
    return list_ai_analysis_results(task_ids, page, page_size)


@app.get("/api/ai/results/{result_id}")
def api_ai_result(result_id: int, _: dict = Depends(admin_user)):
    return _saved_ai_report(get_ai_analysis_result(result_id))


def _item_value(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _append_ai_report_sheets(wb: openpyxl.Workbook, result: dict) -> None:
    info = wb.active
    info.title = "AI分析概览"
    info.append(["字段", "内容"])
    info.append(["分析ID", result.get("id")])
    info.append(["分析范围", result.get("analysis_scope")])
    info.append(["模型来源", result.get("model_provider")])
    info.append(["模型名称", result.get("model_name")])
    info.append(["Prompt版本", result.get("prompt_version")])
    info.append(["状态", result.get("status")])
    info.append(["错误原因", result.get("error_message")])
    info.append(["生成时间", result.get("created_at")])
    info.append(["输入摘要", result.get("input_summary")])

    items_sheet = wb.create_sheet("整改清单")
    items_sheet.append(AI_REPORT_HEADERS)
    for index, item in enumerate(_load_ai_analysis_items(result), start=1):
        items_sheet.append([
            index,
            _item_value(item, "问题类型", "problem_type"),
            _item_value(item, "风险等级", "risk_level"),
            _item_value(item, "涉及片区", "area_name"),
            _item_value(item, "涉及机房", "room_name"),
            _item_value(item, "涉及设备", "device_name"),
            _item_value(item, "判断依据", "basis"),
            _item_value(item, "整改建议", "suggestion"),
            _item_value(item, "优先级", "priority"),
        ])


def _ai_report_response(result: dict | None) -> Response:
    if not result:
        raise HTTPException(status_code=404, detail="暂无 AI 分析报告")
    wb = openpyxl.Workbook()
    _append_ai_report_sheets(wb, result)
    return _excel_response(wb, f"AI分析报告_{result.get('id')}.xlsx")


@app.get("/api/ai/results/latest/download")
def api_download_latest_ai_result(_: dict = Depends(admin_user)):
    """下载最近一次数据库内保存的 AI 分析报告。"""
    return _ai_report_response(get_latest_ai_analysis_result())


@app.get("/api/ai/results/{result_id}/download")
def api_download_ai_result(result_id: int, _: dict = Depends(admin_user)):
    """下载指定 AI 分析报告。"""
    return _ai_report_response(get_ai_analysis_result(result_id))


@app.get("/api/ai/adjustments")
def api_ai_adjustments(status: str = "", _: dict = Depends(admin_user)):
    """管理员查询 AI 整改草稿、已下发任务和闭环状态。"""
    try:
        return list_ai_adjustment_tasks(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ai/adjustments/{adjustment_id}/dispatch")
def api_dispatch_ai_adjustment(
    adjustment_id: int,
    payload: AIAdjustmentDispatchRequest,
    actor: dict = Depends(admin_user),
):
    """管理员审核通过后，将 AI 整改任务下发给现场人员。"""
    try:
        item = dispatch_ai_adjustment_task(adjustment_id, payload.assigned_to, actor["id"], payload.review_note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="整改任务不存在")
    _audit(actor, "ai.adjustment.dispatch", "ai_adjustment_task", adjustment_id, {
        "assigned_to": payload.assigned_to,
        "review_note": payload.review_note,
    })
    return {"success": True, "message": "整改任务已下发", "item": item}


@app.post("/api/ai/adjustments/{adjustment_id}/reject")
def api_reject_ai_adjustment(
    adjustment_id: int,
    payload: AIAdjustmentRejectRequest,
    actor: dict = Depends(admin_user),
):
    """管理员驳回 AI 整改草稿。"""
    try:
        item = reject_ai_adjustment_task(adjustment_id, actor["id"], payload.review_note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="整改任务不存在")
    _audit(actor, "ai.adjustment.reject", "ai_adjustment_task", adjustment_id, {"review_note": payload.review_note})
    return {"success": True, "message": "整改草稿已驳回", "item": item}


@app.post("/api/ai/adjustments/{adjustment_id}/remind")
def api_remind_ai_adjustment(
    adjustment_id: int,
    payload: AIAdjustmentReminderRequest,
    actor: dict = Depends(admin_user),
):
    """管理员确认后记录系统内催办；不发送外部通知，不改变整改状态。"""
    try:
        item = remind_ai_adjustment_task(adjustment_id, actor["id"], payload.reminder_note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="整改任务不存在")
    _audit(actor, "ai.adjustment.remind", "ai_adjustment_task", adjustment_id, {
        "reminder_note": payload.reminder_note,
        "external_notification_sent": False,
    })
    return {
        "success": True,
        "message": "已记录系统内催办，未发送微信、短信等外部通知",
        "external_notification_sent": False,
        "item": item,
    }


@app.get("/api/adjustments/my")
def api_my_adjustments(user: dict = Depends(current_user)):
    """现场人员查询管理员下发给自己的资产整改任务。"""
    if user["role"] != "worker":
        raise HTTPException(status_code=403, detail="仅现场人员可查看整改任务")
    return [three_way.worker_case(item) for item in get_my_adjustment_tasks(user["id"]) if user_can_access_area(user["area_name"], item["area_name"])]


@app.post("/api/adjustments/{adjustment_id}/complete")
def api_complete_adjustment(
    adjustment_id: int,
    payload: AdjustmentCompleteRequest,
    user: dict = Depends(current_user),
):
    """现场人员提交整改结果，等待 PC 管理员验收。"""
    if user["role"] != "worker":
        raise HTTPException(status_code=403, detail="仅现场人员可处理整改任务")
    try:
        if three_way.is_comparison_case(adjustment_id):
            item = three_way.case_action(adjustment_id, "field_complete", user["id"], {"note": payload.completion_note, "records": [r.model_dump() for r in payload.records], "new_record": payload.new_record.model_dump() if payload.new_record else None})
        else:
            item = complete_ai_adjustment_task(adjustment_id, user["id"], payload.completion_note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="整改任务不存在")
    _audit(user, "ai.adjustment.complete", "ai_adjustment_task", adjustment_id, {
        "completion_note": payload.completion_note,
    })
    return {"success": True, "message": "整改结果已提交，等待管理员处理或验收", "item": three_way.worker_case(item) if item.get("comparison") else item}


@app.post("/api/ai/adjustments/{adjustment_id}/verify")
def api_verify_adjustment(
    adjustment_id: int,
    payload: AdjustmentVerifyRequest,
    actor: dict = Depends(admin_user),
):
    """PC 管理员验收现场整改结果并确认最终闭环。"""
    try:
        item = verify_ai_adjustment_task(adjustment_id, actor["id"], payload.verification_note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="整改任务不存在")
    _audit(actor, "ai.adjustment.verify", "ai_adjustment_task", adjustment_id, {
        "verification_note": payload.verification_note,
    })
    return {"success": True, "message": "管理员验收通过，整改任务已闭环", "item": item}


def _ai_status_payload() -> dict:
    external_agent_url = _clean_env_setting(os.getenv("INVENTORY_AI_AGENT_URL", ""))
    api_key = _clean_env_setting(os.getenv("DEEPSEEK_API_KEY", ""))
    api_key_configured = bool(api_key) and len(api_key) >= 10 and not _has_control_chars(api_key)
    enabled = get_system_setting("ai_enabled", "1") == "1"
    model = _clean_env_setting(os.getenv("DEEPSEEK_MODEL", ""))
    model_name = model or _local_agent_model_name(external_agent_url)
    configured = bool(external_agent_url or (api_key_configured and model_name))
    model_provider = "local-agent" if external_agent_url else "deepseek"
    return {
        "enabled": enabled,
        "configured": configured,
        "available": enabled and configured,
        "api_key_configured": api_key_configured,
        "model_provider": model_provider,
        "model_name": model_name,
        "base_url": _clean_env_setting(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
        "external_agent_url": external_agent_url,
    }


def _local_agent_model_name(external_agent_url: str) -> str:
    if not external_agent_url:
        return ""
    parsed = urlparse(external_agent_url)
    path = parsed.path.rstrip("/")
    health_path = f"{path[:-8]}/health" if path.endswith("/analyze") else f"{path}/health"
    health_url = urlunparse(parsed._replace(path=health_path, query="", fragment=""))
    try:
        data = json.loads(urlopen(health_url, timeout=2).read())
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return _clean_env_setting(data.get("model_name", ""))


def _clean_env_setting(value: str) -> str:
    return str(value or "").strip().strip("\"'")


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in str(value or ""))


@app.get("/api/ai/status")
def api_ai_status(_: dict = Depends(admin_user)):
    """查询 AI 模型状态。"""
    return _ai_status_payload()


@app.post("/api/ai/check")
def api_ai_check(_: dict = Depends(admin_user)):
    """真实检测 AI 模型是否可用。"""
    ai_status = _ai_status_payload()
    if not ai_status["enabled"]:
        return {"ok": False, "message": "AI 模型分析已关闭，请先启用模型", "status": ai_status}
    if not ai_status["configured"]:
        missing = []
        if not ai_status["external_agent_url"]:
            if not ai_status["api_key_configured"]:
                missing.append("DEEPSEEK_API_KEY")
            if not ai_status["model_name"]:
                missing.append("DEEPSEEK_MODEL")
        return {"ok": False, "message": f"模型配置不完整：缺少 {', '.join(missing) or '模型配置'}", "status": ai_status}
    result = check_model_available()
    status = _ai_status_payload()
    if result.get("model_name"):
        status["model_name"] = result["model_name"]
    return {**result, "status": status}


@app.patch("/api/ai/status")
def api_update_ai_status(enabled: bool = Body(..., embed=True), actor: dict = Depends(admin_user)):
    """启用或关闭 AI 模型分析。"""
    set_system_setting("ai_enabled", "1" if enabled else "0")
    status = _ai_status_payload()
    _audit(actor, "ai.status.update", "system_setting", None, {"enabled": enabled})
    return status


@app.get("/health")
def health():
    """健康检查。"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
