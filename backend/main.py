"""机房资产盘点系统 - FastAPI 后端入口。"""

from __future__ import annotations

import io
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import urlopen
from uuid import uuid4

import openpyxl
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response

from .ai_agent import analyze_inventory, build_inventory_rows, check_model_available
from .database import (
    assign_task,
    batch_assign_tasks,
    batch_create_records,
    cleanup_expired_evidence_photos,
    create_audit_log,
    create_surplus_record,
    create_task,
    create_user,
    delete_user,
    delete_task,
    claim_task,
    get_all_tasks,
    get_area_tasks,
    get_audit_logs,
    get_claimed_tasks,
    get_conn,
    get_idempotent_result,
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
    evidence_photo_is_valid,
    evidence_photo_path,
    init_db,
    mark_record_missing,
    release_task,
    save_evidence_photo,
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
    AIAnalysisRequest,
    AdminLoginRequest,
    LoginRequest,
    MissingSubmitRequest,
    ScanResponse,
    ScanSubmitRequest,
    SurplusSubmitRequest,
    UserCreateRequest,
    UploadResponse,
    UserUpdateRequest,
    UserOut,
)
from .auth import create_access_token, decode_access_token, hash_password, verify_password

app = FastAPI(title="机房资产盘点系统", version="1.0.0")
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "evidence"


def expired_evidence_response() -> HTMLResponse:
    """照片过期时给浏览器弹出明确提示。"""
    return HTMLResponse(
        content="""
        <!doctype html>
        <meta charset="utf-8">
        <script>alert("照片已过期，请重新盘查");</script>
        <body style="font-family: sans-serif; padding: 24px;">照片已过期，请重新盘查</body>
        """,
        status_code=410,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """启动时初始化数据库，并创建默认用户。"""
    init_db()
    cleanup_expired_evidence_photos()
    admin = create_user("admin_001", "管理员", "admin", "默认片区")
    configured_password = os.getenv("INVENTORY_ADMIN_PASSWORD", "")
    if configured_password:
        set_password_hash(admin["id"], hash_password(configured_password))
    elif not admin.get("password_hash"):
        set_password_hash(admin["id"], hash_password("admin_001"))
    create_user("worker_001", "张三", "worker", "北区")
    create_user("worker_002", "李四", "worker", "南区")
    create_user("worker_003", "王五", "worker", "西区")
    create_user("worker_004", "赵六", "worker", "南区")
    create_user("worker_005", "周班长", "worker", "北区,南区,西区")


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


def _load_workbook(file_content: bytes):
    """从上传文件内容读取 Excel 工作簿。"""
    try:
        return openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Excel 文件读取失败：{exc}") from exc


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


def _parse_excel_devices(ws, fixed_room_name: str | None = None, fixed_area_name: str = "默认片区") -> dict[str, list[dict]]:
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

    extra_cols = ["网元类型", "设备类别", "所属机房业务级别", "生命周期状态", "维护专业"]
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
        area_name = fixed_area_name or "默认片区"
        if fixed_room_name is None and area_col is not None:
            raw_area_name = row[col_map[area_col]]
            area_name = str(raw_area_name).strip() if raw_area_name else ""

        raw_model = row[col_map[model_col]]
        raw_barcode = row[col_map[barcode_col]]
        extra = {}
        for col_name in extra_cols:
            if col_name not in col_map:
                continue
            col_idx = col_map[col_name]
            if col_idx < len(row) and row[col_idx] is not None:
                extra[col_name] = str(row[col_idx]).strip()

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
    if os.getenv("INVENTORY_DEV_LOGIN", "1") == "1" and code.startswith("worker_"):
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
            user = create_user(openid, "待绑定用户", "worker", "默认片区", is_active=0)
            _audit(None, "worker.login_request", "user", user["id"], {"wx_openid": openid})
            raise HTTPException(status_code=403, detail="账号待管理员启用，请联系管理员")
        raise HTTPException(status_code=401, detail="登录失败，用户不存在")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="账号未启用，请联系管理员")
    _audit(user, "worker.login", "user", user["id"])
    return {
        "user_id": user["id"], "name": user["name"], "role": user["role"],
        "area_name": user["area_name"] or "默认片区", "access_token": create_access_token(user),
    }


@app.post("/api/upload", response_model=UploadResponse)
async def api_upload(
    file: UploadFile = File(...),
    room_name: str = Form(...),
    task_name: str = Form(""),
    assigned_to: int | None = Form(None),
    area_name: str = Form("默认片区"),
    force_import: bool = Form(False),
    actor: dict = Depends(admin_user),
):
    """上传单个机房清单，保留给旧页面或外部调用。"""
    wb = _load_workbook(await file.read())
    rooms = _parse_excel_devices(wb.active, fixed_room_name=room_name, fixed_area_name=area_name)
    preview = _preview_rooms(rooms)
    if not preview["valid"] and not force_import:
        raise HTTPException(status_code=400, detail="清单预检未通过，请先处理空资产条码、重复网元、重复标签或空设备型号")
    devices = rooms[room_name]

    final_task_name = task_name if task_name else f"{room_name}-盘点"
    task_id = create_task(final_task_name, room_name, assigned_to, area_name)
    count = batch_create_records(task_id, devices)
    error_count = sum(1 for row in preview.get("issue_rows", []) if row.get("级别") == "错误")
    _audit(
        actor,
        "task.import",
        "task",
        task_id,
        {"room_name": room_name, "area_name": area_name, "device_count": count, "force_import": force_import, "error_count": error_count},
    )
    message_prefix = "已按管理员确认继续导入；" if force_import and error_count else ""
    return UploadResponse(
        task_id=task_id,
        task_name=final_task_name,
        device_count=count,
        message=f"{message_prefix}成功导入 {count} 台设备，任务已创建。",
    )


@app.post("/api/upload_rooms")
async def api_upload_rooms(
    file: UploadFile = File(...),
    force_import: bool = Form(False),
    actor: dict = Depends(admin_user),
):
    """上传包含多个机房的清单，按机房自动生成片区待领取任务。"""
    wb = _load_workbook(await file.read())
    rooms = _parse_excel_devices(wb.active)
    preview = _preview_rooms(rooms)
    if not preview["valid"] and not force_import:
        raise HTTPException(status_code=400, detail="清单预检未通过，请先处理空资产条码、重复网元、重复标签或空设备型号")

    created = []
    total_devices = 0
    error_count = sum(1 for row in preview.get("issue_rows", []) if row.get("级别") == "错误")
    for room_name, devices in rooms.items():
        area_name = (devices[0].get("_area_name") or "默认片区").strip() if devices else "默认片区"
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
    wb = _load_workbook(await file.read())
    return _preview_rooms(_parse_excel_devices(wb.active))


@app.get("/api/upload_template")
def api_upload_template(_: dict = Depends(admin_user)):
    """下载标准导入模板。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "盘点任务清单"
    ws.append(["机房名称", "机房所在片区", "网元名称", "设备型号", "资产标签号", "网元类型", "设备类别", "所属机房业务级别", "生命周期状态", "维护专业"])
    ws.append(["示例机房", "东区", "NE-001", "OptiX PTN 980B", "ASSET-001", "传输网元", "传输设备", "重要", "在网", "传输"])
    output = io.BytesIO()
    wb.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''inventory_template.xlsx"},
    )


@app.get("/api/tasks")
def api_tasks(user_id: int | None = None, _: dict = Depends(admin_user)):
    """查询任务列表。传 user_id 时兼容旧版小程序：只查该现场人员已领取任务。"""
    if user_id:
        return get_claimed_tasks(user_id)
    return get_all_tasks()


@app.get("/api/tasks/area")
def api_area_tasks(user_id: int | None = None, user: dict = Depends(current_user)):
    """小程序领任务页：查询当前用户所在片区的任务池。"""
    return get_area_tasks(user["id"])


@app.get("/api/tasks/my")
def api_my_tasks(user_id: int | None = None, user: dict = Depends(current_user)):
    """小程序我的任务页：查询当前用户已经领取的任务。"""
    return get_claimed_tasks(user["id"])


@app.post("/api/tasks/{task_id}/claim")
def api_claim_task(task_id: int, user_id: int | None = Body(None, embed=True), user: dict = Depends(current_user)):
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
    user_id: int | None = Body(None, embed=True),
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


@app.post("/api/records/{record_id}/evidence")
async def api_upload_evidence(
    record_id: int,
    file: UploadFile = File(...),
    evidence_type: str = Form("general"),
    user: dict = Depends(current_user),
):
    """上传无条码设备的现场照片凭证。"""
    cleanup_expired_evidence_photos()
    if evidence_type not in {"general", "asset", "cabinet"}:
        raise HTTPException(status_code=400, detail="凭证类型无效")
    record = _require_record_access(user, record_id)
    suffix = Path(file.filename or "photo.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="仅支持 JPG、PNG 或 WebP 图片")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="照片不能超过 10MB")
    evidence_dir = EVIDENCE_DIR / f"task_{record['task_id']}" / f"record_{record_id}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{record_id}_{evidence_type}_{uuid4().hex}{suffix}"
    relative_path = f"task_{record['task_id']}/record_{record_id}/{filename}"
    path = evidence_dir / filename
    path.write_bytes(content)
    if not save_evidence_photo(record_id, relative_path, evidence_type):
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    _audit(user, "record.evidence_upload", "scan_record", record_id, {"filename": relative_path, "evidence_type": evidence_type})
    return {"success": True, "filename": relative_path}


@app.get("/api/evidence/{filename:path}")
def api_evidence(filename: str, access_token: str = Query("")):
    """查看现场照片凭证。"""
    payload = decode_access_token(access_token)
    user = get_user_for_auth(int(payload["sub"])) if payload else None
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    match = __import__("re").search(r"(?:^|/)record_(\d+)(?:/|$)|(?:^|/)(\d+)_", filename)
    if not match:
        raise HTTPException(status_code=400, detail="照片路径不合法")
    _require_record_access(user, int(match.group(1) or match.group(2)))
    cleanup_expired_evidence_photos()
    path = evidence_photo_path(filename)
    if path is None:
        raise HTTPException(status_code=400, detail="照片路径不合法")
    if not evidence_photo_is_valid(filename):
        path.unlink(missing_ok=True)
        return expired_evidence_response()
    return FileResponse(path)


@app.get("/api/audit_logs")
def api_audit_logs(limit: int = 200, _: dict = Depends(admin_user)):
    """查询最近操作审计日志。"""
    return get_audit_logs(limit)


@app.get("/api/report/{task_id}")
def api_report(task_id: int, user: dict = Depends(current_user)):
    """获取单个任务盘点报告。"""
    cleanup_expired_evidence_photos()
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
    "领取时间", "现场完成时间", "设备名称", "清单设备型号", "清单资产条码",
    "现场设备型号", "现场资产条码", "资产无条码", "现场机柜条码", "机柜无条码",
    "现场机架号", "现场状态", "盘点时间", "盘亏原因", "现场备注", "凭证照片状态",
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
    cleanup_expired_evidence_photos()
    task = _get_task_or_404(task_id)
    records = get_report(task_id)
    task["records"] = records

    wb = openpyxl.Workbook()
    _append_inventory_data_sheet(wb, [task])
    return _excel_response(wb, f"资产盘点数据_{task['room_name']}_{task_id}.xlsx")


@app.get("/api/summary")
def api_summary(_: dict = Depends(admin_user)):
    """一次性返回所有任务和盘点记录，供 PC 报告页使用。"""
    cleanup_expired_evidence_photos()
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT t.id, t.task_name, t.room_name, t.area_name, t.status, t.created_at, t.assigned_to,
                   t.assigned_at, t.completed_at, t.field_completed_at,
                   u.name as assigned_name,
                   sr.id as record_id, sr.device_name, sr.expected_model,
                   sr.expected_barcode, sr.asset_barcodes, sr.asset_no_barcode,
                   sr.cabinet_barcodes, sr.cabinet_no_barcode, sr.rack_no,
                    sr.actual_model, sr.notes, sr.match_status, sr.scanned_at,
                    sr.record_type, sr.field_status, sr.missing_reason, sr.created_by,
                    sr.evidence_photo
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
                    "area_name": data["area_name"] or "默认片区",
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
                    "evidence_photo": data["evidence_photo"],
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
            "analysis_items": [],
        }
        analysis_result = "[]"
        status = "failed"
        error_message = str(exc)

    analysis_id = save_ai_analysis_result(
        task_ids=task_ids,
        analysis_scope=analysis_scope,
        model_provider=result["model_provider"],
        model_name=result["model_name"],
        prompt_version=result["prompt_version"],
        input_summary=result["input_summary"],
        desensitized_payload=result["desensitized_payload"],
        analysis_result=analysis_result,
        status=status,
        error_message=error_message,
        created_by=actor.get("id"),
    )
    _audit(actor, "ai.analyze", "ai_analysis", analysis_id, {"task_ids": task_ids, "status": status})
    return {
        "id": analysis_id,
        "status": status,
        "error_message": error_message,
        "analysis_items": result["analysis_items"],
        "input_summary": result["input_summary"],
        "model_provider": result["model_provider"],
        "model_name": result["model_name"],
    }


def _ai_status_payload() -> dict:
    external_agent_url = _clean_env_setting(os.getenv("INVENTORY_AI_AGENT_URL", ""))
    api_key = _clean_env_setting(os.getenv("DEEPSEEK_API_KEY", ""))
    api_key_configured = bool(api_key) and len(api_key) >= 10 and not _has_control_chars(api_key)
    enabled = get_system_setting("ai_enabled", "1") == "1"
    model = _clean_env_setting(os.getenv("DEEPSEEK_MODEL", ""))
    model_name = external_agent_url if external_agent_url else model
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
    return {**result, "status": _ai_status_payload()}


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

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
