"""机房资产盘点系统 - FastAPI 后端入口。"""

from __future__ import annotations

import io
import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import openpyxl
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

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
    batch_create_records,
    complete_ai_adjustment_task,
    create_audit_log,
    create_ai_adjustment_drafts,
    create_surplus_record,
    create_task,
    create_user,
    delete_user,
    delete_task,
    dispatch_ai_adjustment_task,
    claim_task,
    get_all_tasks,
    get_area_tasks,
    get_audit_logs,
    get_ai_analysis_result,
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
    get_task_by_id,
    get_task_items,
    get_user_by_id,
    get_user_by_openid,
    get_user_for_auth,
    get_users,
    get_workers,
    USER_AREA_CHOICES,
    USER_AREA_CORE,
    init_db,
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
    set_password_hash,
    add_compare_status,
    add_task_field_status,
)
from .models import (
    AdjustmentCompleteRequest,
    AIAdjustmentDispatchRequest,
    AIAdjustmentRejectRequest,
    AIAdjustmentReminderRequest,
    AIChatRequest,
    AIAnalysisRequest,
    AdminLoginRequest,
    LoginRequest,
    MissingSubmitRequest,
    ProfileBindRequest,
    ScanResponse,
    ScanSubmitRequest,
    SurplusSubmitRequest,
    UserCreateRequest,
    UserUpdateRequest,
    UserOut,
    QrAssetBindingRequest,
    QrAssetCreateRequest,
    QrAssetHistoryCreateRequest,
    QrAssetHistoryUpdateRequest,
    QrAssetUpdateRequest,
)
from .auth import create_access_token, decode_access_token, hash_password, require_secret_configured, verify_password

app = FastAPI(title="机房资产盘点系统", version="1.0.0")
MAX_EXCEL_BYTES = int(os.getenv("INVENTORY_MAX_EXCEL_MB", "20")) * 1024 * 1024
_WECHAT_ACCESS_TOKEN_CACHE: dict[str, str | int] = {"token": "", "expires_at": 0}


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


@app.on_event("startup")
def on_startup():
    """启动时初始化数据库，并创建默认用户。"""
    require_secret_configured()
    init_db()
    admin = create_user("admin_001", "管理员", "admin", USER_AREA_CORE)
    configured_password = os.getenv("INVENTORY_ADMIN_PASSWORD", "")
    if configured_password:
        set_password_hash(admin["id"], hash_password(configured_password))
    elif not admin.get("password_hash"):
        raise RuntimeError("首次启动缺少 INVENTORY_ADMIN_PASSWORD，无法创建管理员密码")
    create_user("worker_001", "张三", "worker", "北区")
    create_user("worker_002", "李四", "worker", "南区")
    create_user("worker_003", "王五", "worker", "西区")
    create_user("worker_004", "赵六", "worker", "南区")
    create_user("worker_005", "周班长", "worker", USER_AREA_CORE)


def current_user(authorization: str = Header("")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    payload = decode_access_token(authorization[7:].strip())
    user = get_user_for_auth(int(payload["sub"])) if payload else None
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
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
    if require_claim and task.get("claimed_by") != user["id"]:
        raise HTTPException(status_code=403, detail="只能操作本人已领取的任务")
    return task


def _require_record_access(user: dict, record_id: int) -> dict:
    record = get_record_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
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
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Excel 文件不能为空")
    if len(content) > MAX_EXCEL_BYTES:
        max_mb = MAX_EXCEL_BYTES // 1024 // 1024
        raise HTTPException(status_code=400, detail=f"Excel 文件不能超过 {max_mb}MB")
    return content


def _build_col_map(ws) -> dict[str, int]:
    """读取第一行表头，返回列名到列号的映射。"""
    try:
        header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    except StopIteration as exc:
        raise HTTPException(status_code=400, detail="Excel 为空，未找到表头") from exc

    col_map = {}
    for idx, value in enumerate(header):
        if value is not None:
            col_map[str(value).strip()] = idx
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
    room_col = _find_room_col(col_map)
    area_col = _find_area_col(col_map)
    device_col = _find_col(col_map, ("网元名称", "设备名称", "网元", "设备"))
    model_col = _find_col(col_map, ("设备型号", "型号", "设备类型"))
    barcode_col = _find_col(col_map, ("资产标签号", "资产条码", "资产编码", "固资编号", "标签编号"))

    missing = []
    if device_col is None:
        missing.append("网元名称/设备名称")
    if model_col is None:
        missing.append("设备型号")
    if barcode_col is None:
        missing.append("资产标签号/资产条码")
    if fixed_room_name is None and room_col is None:
        missing.append("机房名称")
    if fixed_room_name is None and area_col is None:
        missing.append("机房所在片区")
    if missing:
        raise HTTPException(status_code=400, detail=f"Excel 缺少必要列：{', '.join(missing)}")

    extra_cols = ["设备机架", "设备类别", "所属机房业务级别", "生命周期状态", "维护专业"]
    rooms: dict[str, list[dict]] = {}

    for row_no, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if all(cell is None for cell in row):
            continue

        raw_device_name = row[col_map[device_col]]
        if raw_device_name is None or str(raw_device_name).strip() == "":
            continue

        room_name = fixed_room_name
        if room_name is None and room_col is not None:
            raw_room_name = row[col_map[room_col]]
            room_name = str(raw_room_name).strip() if raw_room_name else ""
        if not room_name:
            continue
        area_name = _normalize_import_area_name(fixed_area_name)
        if fixed_room_name is None and area_col is not None:
            raw_area_name = row[col_map[area_col]]
            area_name = _normalize_import_area_name(raw_area_name, row_no)

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
            "device_name": str(raw_device_name).strip(),
            "expected_model": str(raw_model).strip() if raw_model else "",
            "expected_barcode": str(raw_barcode).strip() if raw_barcode else "",
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
        room_rows.append({"room_name": room_name, "area_name": area_name, "device_count": len(devices)})
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
            if device["expected_barcode"]:
                barcode_keys.append(device["expected_barcode"])
                barcode_rows.setdefault(device["expected_barcode"], []).append(device)
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


def _worker_session_payload(user: dict) -> dict:
    """小程序端保存到本地的用户会话字段。"""
    return {
        "user_id": user["id"],
        "name": user["name"],
        "role": user["role"],
        "area_name": user["area_name"] or USER_AREA_CORE,
        "phone": user.get("phone") or "",
        "access_token": create_access_token(user),
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
    """PC 后台新增或更新小程序账号。"""
    _validate_user_payload(req.wx_openid, req.name, req.role, req.area_name)
    user = create_user(
        wx_openid=req.wx_openid,
        name=req.name,
        role=req.role,
        area_name=req.area_name,
        phone=req.phone,
        employee_no=req.employee_no,
        is_active=1 if req.is_active else 0,
    )
    _audit(actor, "user.upsert", "user", user["id"], {"wx_openid": user.get("wx_openid"), "role": user.get("role")})
    return user


@app.patch("/api/users/{user_id}", response_model=UserOut)
def api_update_user(user_id: int, req: UserUpdateRequest, actor: dict = Depends(admin_user)):
    """PC 后台修改小程序账号信息。"""
    data = req.dict(exclude_unset=True)
    _validate_user_payload(
        data.get("wx_openid"),
        data.get("name"),
        data.get("role"),
        data.get("area_name"),
    )
    try:
        user = update_user(user_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _audit(actor, "user.update", "user", user_id, {"fields": sorted(data.keys())})
    return user


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, actor: dict = Depends(admin_user)):
    """PC 后台删除小程序账号。采用业务删除，保留历史任务引用。"""
    if not delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在或已删除")
    _audit(actor, "user.delete", "user", user_id)
    return {"success": True, "message": "账号已删除"}


@app.post("/api/admin/login")
def api_admin_login(req: AdminLoginRequest):
    user = get_user_by_openid(req.username.strip())
    if not user or user["role"] != "admin" or not user["is_active"]:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not verify_password(req.password, user.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    _audit(user, "admin.login", "user", user["id"])
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": UserOut(**user)}


@app.post("/api/login")
def api_login(req: LoginRequest):
    """小程序登录；生产通过微信 code2session 获取可信 openid。"""
    code = req.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="登录凭证不能为空")
    if os.getenv("INVENTORY_DEV_LOGIN", "0") == "1" and code.startswith("worker_"):
        openid = code
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
    return _worker_session_payload(user)


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

    created = []
    total_devices = 0
    error_count = sum(1 for row in preview.get("issue_rows", []) if row.get("级别") == "错误")
    for room_name, devices in rooms.items():
        area_name = (devices[0].get("_area_name") or USER_AREA_CORE).strip() if devices else USER_AREA_CORE
        task_name = f"{room_name}-盘点"
        task_id = create_task(task_name, room_name, None, area_name)
        count = batch_create_records(task_id, devices)
        total_devices += count
        created.append({
            "task_id": task_id,
            "task_name": task_name,
            "room_name": room_name,
            "area_name": area_name,
            "device_count": count,
        })
        _audit(
            actor,
            "task.import",
            "task",
            task_id,
            {"room_name": room_name, "area_name": area_name, "device_count": count, "force_import": force_import, "error_count": error_count},
        )

    message_prefix = "已按管理员确认继续导入；" if force_import and error_count else ""
    return {
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
    """下载标准导入模板。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "盘点任务清单"
    ws.append(["机房名称", "机房所在片区", "网元名称", "设备型号", "资产标签号", "设备机架", "设备类别", "所属机房业务级别", "生命周期状态", "维护专业"])
    ws.append(["示例机房", USER_AREA_CORE, "NE-001", "OptiX PTN 980B", "ASSET-001", "01架01框", "传输设备", "重要", "在网", "传输"])
    output = io.BytesIO()
    wb.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''inventory_template.xlsx"},
    )


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
    success, message = release_task(task_id, user["id"], reason)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    _audit(user, "task.release", "task", task_id, {"reason": reason})
    return {"success": True, "message": message}


@app.post("/api/tasks/{task_id}/assign")
def api_assign_task(task_id: int, assigned_to: int = Body(..., embed=True), actor: dict = Depends(admin_user)):
    """单个机房任务下发给指定现场人员。"""
    if not assign_task(task_id, assigned_to):
        raise HTTPException(status_code=404, detail="任务不存在")
    _audit(actor, "task.assign", "task", task_id, {"assigned_to": assigned_to})
    return {"success": True, "message": "任务已下发"}


@app.post("/api/tasks/batch_assign")
def api_batch_assign_tasks(assignments: list[dict[str, int]] = Body(..., embed=True), actor: dict = Depends(admin_user)):
    """批量下发多个机房任务。"""
    if not assignments:
        raise HTTPException(status_code=400, detail="请至少选择一个任务")
    count = batch_assign_tasks(assignments)
    _audit(actor, "task.batch_assign", "task", None, {"assignments": assignments, "updated": count})
    return {"success": True, "updated": count, "message": f"已下发 {count} 个机房任务"}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int, reason: str = "管理员作废", actor: dict = Depends(admin_user)):
    """作废某个机房盘点任务，保留审计数据但不再参与业务展示。"""
    if not reason.strip():
        raise HTTPException(status_code=400, detail="请填写作废原因")
    if not delete_task(task_id, reason):
        raise HTTPException(status_code=404, detail="任务不存在")
    _audit(actor, "task.delete", "task", task_id, {"reason": reason})
    return {"success": True, "message": "任务已作废，不再参与统计"}


@app.get("/api/tasks/{task_id}/items")
def api_task_items(task_id: int, user: dict = Depends(current_user)):
    """查询某个任务下的设备清单。"""
    task = _require_task_access(user, task_id)
    return {"task": task, "items": get_task_items(task_id)}


@app.post("/api/scan", response_model=ScanResponse)
def api_scan(req: ScanSubmitRequest, user: dict = Depends(current_user)):
    """现场人员提交一条盘点结果。"""
    _require_record_access(user, req.record_id)
    action = "scan.submit"
    request_id = req.client_request_id.strip()
    cached = get_idempotent_result(user["id"], action, request_id)
    if cached:
        return cached
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
        "message": f"盘点完成，比对结果：{result['match_status']}",
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
    _require_record_access(user, record_id)
    action = "record.missing"
    request_id = req.client_request_id.strip()
    cached = get_idempotent_result(user["id"], action, request_id)
    if cached:
        return cached
    try:
        result = mark_record_missing(record_id, req.reason, req.notes)
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
        return cached
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
    inventory_records = [r for r in records if (r.get("record_type") or "normal") != "surplus"]
    return {
        "task": task,
        "records": records,
        "summary": {
            "total": len(inventory_records),
            "已盘": sum(1 for r in inventory_records if r["match_status"] != "未扫"),
            "未盘": sum(1 for r in inventory_records if r["match_status"] == "未扫"),
        },
    }


def _autosize_workbook(wb: openpyxl.Workbook) -> None:
    """根据内容自动调整 Excel 列宽，避免导出后字段挤在一起。"""
    for sheet in wb.worksheets:
        for column in sheet.columns:
            max_len = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 36)


def _excel_response(wb: openpyxl.Workbook, filename: str) -> Response:
    """把工作簿统一转成下载响应。"""
    _autosize_workbook(wb)
    output = io.BytesIO()
    wb.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


INVENTORY_DATA_HEADERS = [
    "任务ID", "任务名称", "机房名称", "片区", "领取人", "任务状态", "任务创建时间",
    "领取时间", "现场完成时间", "设备名称", "清单设备型号", "清单资产条码", "清单设备机架",
    "现场设备型号", "现场资产条码", "资产无条码", "现场机柜条码", "机柜无条码",
    "现场机架号", "现场状态", "盘点时间", "盘亏原因", "现场备注",
]


def _append_inventory_data_sheet(wb: openpyxl.Workbook, tasks: list[dict]) -> None:
    """导出任务清单和现场盘点合并后的原始数据。"""
    ws = wb.active
    ws.title = "资产盘点数据"
    ws.append(INVENTORY_DATA_HEADERS)
    for row in build_inventory_rows(tasks):
        ws.append([row.get(header) or "" for header in INVENTORY_DATA_HEADERS])


@app.get("/api/export/{task_id}")
def api_export(task_id: int, _: dict = Depends(admin_user)):
    """导出单个任务的资产盘点原始数据。"""
    task = _get_task_or_404(task_id)
    records = get_report(task_id)
    task["records"] = records

    wb = openpyxl.Workbook()
    _append_inventory_data_sheet(wb, [task])
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
        tasks.append(task)

    wb = openpyxl.Workbook()
    _append_inventory_data_sheet(wb, tasks)
    _audit(actor, "ai.export_inventory", "ai_export", None, {"task_ids": selected_ids})
    return _excel_response(wb, f"资产盘点数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")


@app.get("/api/summary")
def api_summary(_: dict = Depends(admin_user)):
    """一次性返回所有任务和盘点记录，供 PC 报告页使用。"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT t.id, t.task_name, t.room_name, t.area_name, t.status, t.created_at, t.assigned_to,
                   t.assigned_at, t.completed_at, t.field_completed_at,
                   u.name as assigned_name,
                   sr.id as record_id, sr.device_name, sr.device_extra, sr.expected_model,
                   sr.expected_barcode, sr.asset_barcodes, sr.asset_no_barcode,
                   sr.cabinet_barcodes, sr.cabinet_no_barcode, sr.rack_no,
                    sr.actual_model, sr.notes, sr.match_status, sr.scanned_at,
                    sr.record_type, sr.field_status, sr.missing_reason, sr.created_by
            FROM tasks t
            LEFT JOIN users u ON u.id = t.assigned_to
            LEFT JOIN scan_records sr ON sr.task_id = t.id
            WHERE t.is_deleted = 0
            ORDER BY t.created_at DESC, sr.id
        """).fetchall()

        tasks_map = {}
        for row in rows:
            data = dict(row)
            task_id = data["id"]
            if task_id not in tasks_map:
                tasks_map[task_id] = {
                    "id": task_id,
                    "task_name": data["task_name"],
                    "room_name": data["room_name"],
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
                record = add_compare_status({
                    "id": data["record_id"],
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
                })
                tasks_map[task_id]["records"].append(record)
                if record["scanned_at"]:
                    current_last = tasks_map[task_id]["last_scanned_at"]
                    tasks_map[task_id]["last_scanned_at"] = max(current_last, record["scanned_at"])

        result = list(tasks_map.values())
        for task in result:
            inventory_records = [r for r in task["records"] if (r.get("record_type") or "normal") != "surplus"]
            task["total"] = len(inventory_records)
            task["scanned"] = sum(1 for r in inventory_records if r["match_status"] != "未扫")
            add_task_field_status(task)
        return result
    finally:
        conn.close()


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

    unfinished_tasks = [
        task for task in tasks
        if task.get("status") != "done" or not (task.get("field_completed_at") or task.get("completed_at"))
    ]
    if unfinished_tasks:
        labels = "、".join(f"{task['room_name']}(#{task['id']})" for task in unfinished_tasks[:5])
        raise HTTPException(status_code=400, detail=f"只能分析现场已完成的任务：{labels} 尚未完成")

    task_completion_versions = [
        (int(task["id"]), str(task.get("field_completed_at") or task.get("completed_at")))
        for task in tasks
    ]
    analyzed_versions = list_analyzed_ai_task_versions(task_completion_versions)
    if analyzed_versions:
        duplicate_ids = {int(item["task_id"]) for item in analyzed_versions}
        labels = "、".join(
            f"{task['room_name']}(#{task['id']})"
            for task in tasks if int(task["id"]) in duplicate_ids
        )
        raise HTTPException(
            status_code=409,
            detail=f"{labels} 当前现场完成批次已经生成过报告；重新盘点并产生新的完成时间后才能再次分析",
        )

    analysis_scope = "、".join(f"{task['room_name']}({task['id']})" for task in tasks)
    try:
        result = analyze_inventory(tasks)
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    adjustment_drafts = []
    if status == "success":
        adjustment_drafts = create_ai_adjustment_drafts(
            analysis_id=analysis_id,
            task_ids=task_ids,
            analysis_items=result["analysis_items"],
            source_tasks=tasks,
            created_by=actor.get("id"),
        )
    _audit(actor, "ai.analyze", "ai_analysis", analysis_id, {
        "task_ids": task_ids,
        "status": status,
        "context_meta": result.get("context_meta") or {},
        "adjustment_draft_count": len(adjustment_drafts),
    })
    return {
        "id": analysis_id,
        "status": status,
        "error_message": error_message,
        "analysis_items": result["analysis_items"],
        "input_summary": result["input_summary"],
        "model_provider": result["model_provider"],
        "model_name": result["model_name"],
        "context_meta": result.get("context_meta") or {},
        "tool_manifest": result.get("tool_manifest") or [],
        "adjustment_drafts": adjustment_drafts,
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
    return get_my_adjustment_tasks(user["id"])


@app.post("/api/adjustments/{adjustment_id}/complete")
def api_complete_adjustment(
    adjustment_id: int,
    payload: AdjustmentCompleteRequest,
    user: dict = Depends(current_user),
):
    """现场人员确认整改完成，形成异常资产闭环记录。"""
    if user["role"] != "worker":
        raise HTTPException(status_code=403, detail="仅现场人员可处理整改任务")
    try:
        item = complete_ai_adjustment_task(adjustment_id, user["id"], payload.completion_note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="整改任务不存在")
    _audit(user, "ai.adjustment.complete", "ai_adjustment_task", adjustment_id, {
        "completion_note": payload.completion_note,
    })
    return {"success": True, "message": "整改任务已完成", "item": item}


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
