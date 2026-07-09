"""机房资产盘点系统 - FastAPI 后端入口。"""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import openpyxl
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from .database import (
    assign_task,
    batch_assign_tasks,
    batch_create_records,
    create_task,
    create_user,
    delete_task,
    get_all_tasks,
    get_report,
    get_record_by_id,
    get_task_by_id,
    get_task_items,
    get_tasks_by_user,
    get_user_by_id,
    get_workers,
    init_db,
    review_record,
    save_evidence_photo,
    submit_scan,
    add_compare_status,
)
from .models import (
    LoginRequest,
    ScanResponse,
    ScanSubmitRequest,
    UploadResponse,
    UserOut,
)

app = FastAPI(title="机房资产盘点系统", version="1.0.0")
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "evidence"

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
    create_user("admin_001", "管理员", "admin")
    create_user("worker_001", "张三", "worker")
    create_user("worker_002", "李四", "worker")


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


def _find_col(col_map: dict[str, int], candidates: tuple[str, ...]) -> str | None:
    """按候选列名查找 Excel 表头，兼容不同资源表命名。"""
    for col_name in candidates:
        if col_name in col_map:
            return col_name
    return None


def _parse_excel_devices(ws, fixed_room_name: str | None = None) -> dict[str, list[dict]]:
    """解析设备清单，并按机房名称分组。"""
    col_map = _build_col_map(ws)
    room_col = _find_room_col(col_map)
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
            "device_name": str(raw_device_name).strip(),
            "expected_model": str(raw_model).strip() if raw_model else "",
            "expected_barcode": str(raw_barcode).strip() if raw_barcode else "",
            "device_extra": extra,
        })

    if not rooms:
        raise HTTPException(status_code=400, detail="Excel 中没有有效的设备数据")
    return rooms


def _preview_rooms(rooms: dict[str, list[dict]]) -> dict:
    """汇总导入质量，重复网元或资产标签会阻止正式导入。"""
    device_keys: list[str] = []
    barcode_keys: list[str] = []
    device_rows: dict[str, list[dict]] = {}
    barcode_rows: dict[str, list[dict]] = {}
    missing_model = 0
    missing_barcode = 0
    issue_rows = []
    room_rows = []
    for room_name, devices in rooms.items():
        room_rows.append({"room_name": room_name, "device_count": len(devices)})
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
                    "级别": "提醒",
                    "Excel行号": row_no,
                    "机房名称": room_name,
                    "设备名称": device["device_name"],
                    "问题类型": "空资产条码",
                    "处理建议": "可继续导入；现场盘点时会显示无期望资产条码，建议管理员后续补齐。",
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
    return {
        "valid": not duplicate_devices and not duplicate_barcodes and missing_model == 0,
        "room_count": len(rooms),
        "device_count": sum(len(devices) for devices in rooms.values()),
        "missing_model": missing_model,
        "missing_barcode": missing_barcode,
        "duplicate_devices": duplicate_devices,
        "duplicate_barcodes": duplicate_barcodes,
        "issue_rows": sorted(issue_rows, key=lambda item: (item["级别"] != "错误", item["Excel行号"] or 0)),
        "rooms": room_rows,
    }


@app.get("/api/workers", response_model=list[UserOut])
def api_workers():
    """查询所有现场人员。"""
    return get_workers()


@app.get("/api/users/{user_id}", response_model=UserOut | None)
def api_user(user_id: int):
    """按 ID 查询用户。"""
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@app.post("/api/login")
def api_login(req: LoginRequest):
    """小程序登录。开发阶段直接用 code 模拟 openid。"""
    from .database import get_conn

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE wx_openid = ?", (req.code,)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="登录失败，用户不存在")
        return {"user_id": row["id"], "name": row["name"], "role": row["role"]}
    finally:
        conn.close()


@app.post("/api/upload", response_model=UploadResponse)
async def api_upload(
    file: UploadFile = File(...),
    room_name: str = Form(...),
    task_name: str = Form(""),
    assigned_to: int = Form(...),
):
    """上传单个机房清单，保留给旧页面或外部调用。"""
    wb = _load_workbook(await file.read())
    rooms = _parse_excel_devices(wb.active, fixed_room_name=room_name)
    devices = rooms[room_name]

    final_task_name = task_name if task_name else f"{room_name}-盘点"
    task_id = create_task(final_task_name, room_name, assigned_to)
    count = batch_create_records(task_id, devices)
    return UploadResponse(
        task_id=task_id,
        task_name=final_task_name,
        device_count=count,
        message=f"成功导入 {count} 台设备，任务已创建。",
    )


@app.post("/api/upload_rooms")
async def api_upload_rooms(file: UploadFile = File(...)):
    """上传包含多个机房的清单，按机房自动生成待下发任务。"""
    wb = _load_workbook(await file.read())
    rooms = _parse_excel_devices(wb.active)
    preview = _preview_rooms(rooms)
    if not preview["valid"]:
        raise HTTPException(status_code=400, detail="清单预检未通过，请先处理重复网元、重复标签或空设备型号")

    created = []
    total_devices = 0
    for room_name, devices in rooms.items():
        task_name = f"{room_name}-盘点"
        task_id = create_task(task_name, room_name, None)
        count = batch_create_records(task_id, devices)
        total_devices += count
        created.append({
            "task_id": task_id,
            "task_name": task_name,
            "room_name": room_name,
            "device_count": count,
        })

    return {
        "task_count": len(created),
        "device_count": total_devices,
        "tasks": created,
        "message": f"成功识别 {len(created)} 个机房，导入 {total_devices} 台设备，请选择现场人员后下发。",
    }


@app.post("/api/upload_rooms/preview")
async def api_preview_upload_rooms(file: UploadFile = File(...)):
    """预检多机房清单，不写入数据库。"""
    wb = _load_workbook(await file.read())
    return _preview_rooms(_parse_excel_devices(wb.active))


@app.get("/api/upload_template")
def api_upload_template():
    """下载标准导入模板。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "盘点任务清单"
    ws.append(["机房名称", "网元名称", "设备型号", "资产标签号", "网元类型", "设备类别", "所属机房业务级别", "生命周期状态", "维护专业"])
    ws.append(["示例机房", "NE-001", "OptiX PTN 980B", "ASSET-001", "传输网元", "传输设备", "重要", "在网", "传输"])
    output = io.BytesIO()
    wb.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''inventory_template.xlsx"},
    )


@app.get("/api/tasks")
def api_tasks(user_id: int | None = None):
    """查询任务列表。传 user_id 时只查该现场人员任务。"""
    if user_id:
        return get_tasks_by_user(user_id)
    return get_all_tasks()


@app.post("/api/tasks/{task_id}/assign")
def api_assign_task(task_id: int, assigned_to: int = Body(..., embed=True)):
    """单个机房任务下发给指定现场人员。"""
    if not assign_task(task_id, assigned_to):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "message": "任务已下发"}


@app.post("/api/tasks/batch_assign")
def api_batch_assign_tasks(assignments: list[dict[str, int]] = Body(..., embed=True)):
    """批量下发多个机房任务。"""
    if not assignments:
        raise HTTPException(status_code=400, detail="请至少选择一个任务")
    count = batch_assign_tasks(assignments)
    return {"success": True, "updated": count, "message": f"已下发 {count} 个机房任务"}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int, reason: str = "管理员作废"):
    """作废某个机房盘点任务，保留审计数据但不再参与业务展示。"""
    if not reason.strip():
        raise HTTPException(status_code=400, detail="请填写作废原因")
    if not delete_task(task_id, reason):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "message": "任务已作废，不再参与统计"}


@app.get("/api/tasks/{task_id}/items")
def api_task_items(task_id: int):
    """查询某个任务下的设备清单。"""
    task = get_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task": task, "items": get_task_items(task_id)}


@app.post("/api/scan", response_model=ScanResponse)
def api_scan(req: ScanSubmitRequest):
    """现场人员提交一条盘点结果。"""
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
    return ScanResponse(
        success=True,
        match_status=result["match_status"],
        asset_match_status=result["asset_match_status"],
        model_match_status=result["model_match_status"],
        message=f"盘点完成，比对结果：{result['match_status']}",
    )


@app.post("/api/records/{record_id}/evidence")
async def api_upload_evidence(record_id: int, file: UploadFile = File(...)):
    """上传无条码设备的现场照片凭证。"""
    record = get_record_by_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    suffix = Path(file.filename or "photo.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="仅支持 JPG、PNG 或 WebP 图片")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="照片不能超过 10MB")
    evidence_dir = EVIDENCE_DIR / f"task_{record['task_id']}" / f"record_{record_id}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{record_id}_{uuid4().hex}{suffix}"
    relative_path = f"task_{record['task_id']}/record_{record_id}/{filename}"
    path = evidence_dir / filename
    path.write_bytes(content)
    if not save_evidence_photo(record_id, relative_path):
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    return {"success": True, "filename": relative_path}


@app.get("/api/evidence/{filename:path}")
def api_evidence(filename: str):
    """查看现场照片凭证。"""
    path = (EVIDENCE_DIR / filename).resolve()
    if EVIDENCE_DIR.resolve() not in path.parents and path != EVIDENCE_DIR.resolve():
        raise HTTPException(status_code=400, detail="照片路径不合法")
    if not path.exists():
        raise HTTPException(status_code=404, detail="照片不存在")
    return FileResponse(path)


@app.post("/api/records/{record_id}/review")
def api_review_record(
    record_id: int,
    status: str = Body(...),
    note: str = Body(""),
    reason: str = Body(""),
    handler: str = Body(""),
    result: str = Body(""),
):
    """复核并闭环异常记录。"""
    if status not in {"待复核", "已确认", "已闭环"}:
        raise HTTPException(status_code=400, detail="无效的复核状态")
    if status == "已闭环" and not note.strip():
        raise HTTPException(status_code=400, detail="闭环时必须填写处理说明")
    if not review_record(record_id, status, note, reason, handler, result):
        raise HTTPException(status_code=404, detail="异常记录不存在")
    return {"success": True, "message": "异常复核状态已更新"}


@app.get("/api/report/{task_id}")
def api_report(task_id: int):
    """获取单个任务盘点报告。"""
    task = get_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    records = get_report(task_id)
    return {
        "task": task,
        "records": records,
        "summary": {
            "total": len(records),
            "一致": sum(1 for r in records if r["match_status"] == "一致"),
            "不一致": sum(1 for r in records if r["match_status"] == "不一致"),
            "未扫": sum(1 for r in records if r["match_status"] == "未扫"),
        },
    }


@app.get("/api/export/{task_id}")
def api_export(task_id: int):
    """导出单个任务盘点结果 Excel。"""
    task = get_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    records = get_report(task_id)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "机房汇总"
    total = len(records)
    scanned = sum(1 for r in records if r["match_status"] != "未扫")
    pending = sum(1 for r in records if r["match_status"] == "未扫")
    asset_ok = sum(1 for r in records if r.get("asset_match_status") == "一致")
    asset_diff = sum(1 for r in records if r.get("asset_match_status") not in {"一致", "未扫", "无条码"})
    no_barcode = sum(1 for r in records if r.get("asset_match_status") == "无条码")
    ws.append(["机房名称", "任务名称", "设备数", "已盘", "未扫", "条码一致", "条码异常", "无条码"])
    ws.append([task["room_name"], task["task_name"], total, scanned, pending, asset_ok, asset_diff, no_barcode])

    ws = wb.create_sheet("设备明细")
    ws.append([
        "机房", "设备", "设备型号", "资产条码",
        "现场设备型号", "现场资产条码", "现场机柜条码", "现场机架号",
        "资产条码对比结果", "设备型号对比结果", "备注", "总体比对结果", "盘点时间",
        "现场凭证",
    ])
    for record in records:
        ws.append([
            task["room_name"],
            record["device_name"],
            record["expected_model"],
            record["expected_barcode"],
            record["actual_model"],
            record["asset_barcodes"] or ("无条码" if record["asset_no_barcode"] else ""),
            record["cabinet_barcodes"] or ("无条码" if record["cabinet_no_barcode"] else ""),
            record.get("rack_no") or "",
            record.get("asset_match_status") or record["match_status"],
            record.get("model_match_status") or "",
            record["notes"],
            record["match_status"],
            record["scanned_at"] or "",
            record.get("evidence_photo") or "",
        ])

    ws = wb.create_sheet("异常复核")
    ws.append([
        "机房", "设备", "期望资产条码", "扫描资产条码", "资产条码对比结果",
        "期望型号", "现场型号", "型号对比结果", "复核状态", "异常原因",
        "处理人", "处理结论", "处理说明", "复核时间", "现场凭证",
    ])
    for record in records:
        is_anomaly = (
            record.get("asset_match_status") not in {"一致", "未扫"}
            or record.get("model_match_status") == "不一致"
        )
        if not is_anomaly:
            continue
        ws.append([
            task["room_name"],
            record["device_name"],
            record["expected_barcode"],
            record["asset_barcodes"] or ("无条码" if record["asset_no_barcode"] else ""),
            record.get("asset_match_status") or record["match_status"],
            record["expected_model"],
            record["actual_model"],
            record.get("model_match_status") or "",
            record.get("review_status") or "",
            record.get("review_reason") or "",
            record.get("review_handler") or "",
            record.get("review_result") or "",
            record.get("review_note") or "",
            record.get("reviewed_at") or "",
            record.get("evidence_photo") or "",
        ])

    for sheet in wb.worksheets:
        for column in sheet.columns:
            max_len = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 12), 36)

    output = io.BytesIO()
    wb.save(output)
    filename = f"盘点结果_{task['room_name']}_{task_id}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/summary")
def api_summary():
    """一次性返回所有任务和盘点记录，供 PC 报告页使用。"""
    from .database import get_conn

    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT t.id, t.task_name, t.room_name, t.status, t.created_at, t.assigned_to,
                   t.assigned_at, t.completed_at,
                   u.name as assigned_name,
                   sr.id as record_id, sr.device_name, sr.expected_model,
                   sr.expected_barcode, sr.asset_barcodes, sr.asset_no_barcode,
                   sr.cabinet_barcodes, sr.cabinet_no_barcode, sr.rack_no,
                    sr.actual_model, sr.notes, sr.match_status, sr.scanned_at,
                    sr.evidence_photo, sr.review_status, sr.review_note,
                    sr.review_reason, sr.review_handler, sr.review_result, sr.reviewed_at
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
                    "status": data["status"],
                    "created_at": data["created_at"],
                    "assigned_at": data["assigned_at"],
                    "completed_at": data["completed_at"],
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
                    "evidence_photo": data["evidence_photo"],
                    "review_status": data["review_status"],
                    "review_note": data["review_note"],
                    "review_reason": data["review_reason"],
                    "review_handler": data["review_handler"],
                    "review_result": data["review_result"],
                    "reviewed_at": data["reviewed_at"],
                })
                tasks_map[task_id]["records"].append(record)
                if record["scanned_at"]:
                    current_last = tasks_map[task_id]["last_scanned_at"]
                    tasks_map[task_id]["last_scanned_at"] = max(current_last, record["scanned_at"])

        result = list(tasks_map.values())
        for task in result:
            task["total"] = len(task["records"])
            task["scanned"] = sum(1 for r in task["records"] if r["match_status"] != "未扫")
        return result
    finally:
        conn.close()


@app.get("/health")
def health():
    """健康检查。"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
