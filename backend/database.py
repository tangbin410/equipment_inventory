"""数据库操作模块"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 数据库文件放在 data 目录下
DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "inventory.db"
EVIDENCE_DIR = DB_DIR / "evidence"
EVIDENCE_RETENTION_DAYS = 30


# ──────────────────────────────────────────────
# 初始化 & 工具函数
# ──────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    """获取数据库连接，每次用完后要 close()"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row  # 让查询结果可以用列名访问
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def dict_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """将 sqlite3.Row 转为普通字典，方便返回 JSON"""
    if row is None:
        return None
    return dict(row)


def normalize_model(value: str) -> str:
    """忽略型号中的空格、连接符和大小写差异，减少录入格式导致的误报。"""
    return re.sub(r"[\s_\-/]+", "", (value or "").strip()).upper()


def normalize_barcode(value: str) -> str:
    """资产条码按去首尾空格和大小写统一后比对。"""
    return (value or "").strip().upper()


def split_codes(value: str) -> set[str]:
    """把逗号分隔的条码拆成集合，自动过滤空值。"""
    return {normalize_barcode(code) for code in (value or "").split(",") if normalize_barcode(code)}


def evidence_photo_path(photo_path: str) -> Path | None:
    """把现场照片相对路径转成安全的绝对路径，防止路径穿越。"""
    if not photo_path:
        return None
    path = (EVIDENCE_DIR / photo_path).resolve()
    evidence_root = EVIDENCE_DIR.resolve()
    if evidence_root not in path.parents and path != evidence_root:
        return None
    return path


def evidence_photo_is_valid(photo_path: str) -> bool:
    """判断现场照片是否仍在 30 天有效期内。"""
    path = evidence_photo_path(photo_path)
    if path is None or not path.exists():
        return False
    uploaded_at = datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.now() - uploaded_at <= timedelta(days=EVIDENCE_RETENTION_DAYS)


def add_evidence_countdown(record: dict[str, Any]) -> dict[str, Any]:
    """给现场凭证照片补充 30 天倒计时信息，供 PC 异常复核提醒使用。"""
    photo_path = record.get("evidence_photo") or ""
    record["evidence_photo_valid"] = False
    record["evidence_photo_uploaded_at"] = ""
    record["evidence_photo_expires_at"] = ""
    record["evidence_photo_days_left"] = None
    if not photo_path:
        return record

    path = evidence_photo_path(photo_path)
    if path is None or not path.exists():
        return record

    uploaded_at = datetime.fromtimestamp(path.stat().st_mtime)
    expires_at = uploaded_at + timedelta(days=EVIDENCE_RETENTION_DAYS)
    seconds_left = (expires_at - datetime.now()).total_seconds()
    record["evidence_photo_uploaded_at"] = uploaded_at.isoformat(timespec="seconds")
    record["evidence_photo_expires_at"] = expires_at.isoformat(timespec="seconds")
    record["evidence_photo_days_left"] = max(0, math.ceil(seconds_left / 86400))
    record["evidence_photo_valid"] = seconds_left > 0
    return record


def cleanup_expired_evidence_photos() -> int:
    """删除超过 30 天的现场凭证照片，返回删除数量。"""
    if not EVIDENCE_DIR.exists():
        return 0
    deleted = 0
    cutoff = datetime.now() - timedelta(days=EVIDENCE_RETENTION_DAYS)
    for path in EVIDENCE_DIR.rglob("*"):
        if not path.is_file():
            continue
        if datetime.fromtimestamp(path.stat().st_mtime) <= cutoff:
            path.unlink(missing_ok=True)
            deleted += 1
    # 清理空目录，让 evidence 目录保持干净。
    for path in sorted(EVIDENCE_DIR.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return deleted


def calc_model_match_status(actual_model: str, expected_model: str) -> str:
    """计算型号比对结果。"""
    return "一致" if normalize_model(actual_model) == normalize_model(expected_model) else "不一致"


def calc_asset_match_status(
    asset_barcodes: str,
    asset_no_barcode: bool,
    expected_barcode: str,
) -> str:
    """计算资产条码比对结果，作为盘点核心结果。"""
    expected_codes = split_codes(expected_barcode)
    submitted_codes = split_codes(asset_barcodes)
    if asset_no_barcode:
        return "无条码"
    if not submitted_codes:
        return "未扫"
    if not expected_codes:
        return "无期望条码"
    return "一致" if submitted_codes & expected_codes else "不一致"


def add_compare_status(record: dict[str, Any]) -> dict[str, Any]:
    """给盘点记录补充资产条码和型号的独立比对结果。"""
    record = add_evidence_countdown(record)
    record_type = record.get("record_type") or "normal"
    if record_type == "surplus":
        record["field_status"] = record.get("field_status") or "盘盈"
        record["asset_match_status"] = "盘盈"
        record["model_match_status"] = "盘盈"
        return record
    if record_type == "missing":
        record["field_status"] = record.get("field_status") or "盘亏"
        record["asset_match_status"] = "盘亏"
        record["model_match_status"] = "盘亏"
        return record

    record["record_type"] = record_type
    record["field_status"] = record.get("field_status") or "正常"
    record["asset_match_status"] = calc_asset_match_status(
        record.get("asset_barcodes") or "",
        bool(record.get("asset_no_barcode")),
        record.get("expected_barcode") or "",
    )
    record["model_match_status"] = (
        "未扫"
        if record.get("match_status") == "未扫"
        else calc_model_match_status(
            record.get("actual_model") or "",
            record.get("expected_model") or "",
        )
    )
    return record


def init_db() -> None:
    """初始化数据库，建表（重复运行不会覆盖）"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            wx_openid   TEXT UNIQUE,
            name        TEXT NOT NULL,
            role        TEXT NOT NULL CHECK(role IN ('admin', 'worker'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name   TEXT NOT NULL,
            room_name   TEXT NOT NULL,
            assigned_to INTEGER REFERENCES users(id),
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'doing', 'done')),
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_records (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id           INTEGER NOT NULL REFERENCES tasks(id),
            device_name       TEXT NOT NULL,
            device_extra      TEXT DEFAULT '{}',
            expected_model    TEXT NOT NULL,
            expected_barcode  TEXT DEFAULT '',
            asset_barcodes    TEXT DEFAULT '',
            asset_no_barcode  INTEGER DEFAULT 0,
            cabinet_barcodes  TEXT DEFAULT '',
            cabinet_no_barcode INTEGER DEFAULT 0,
            rack_no           TEXT DEFAULT '',
            actual_model      TEXT DEFAULT '',
            notes             TEXT DEFAULT '',
            record_type       TEXT DEFAULT 'normal',
            field_status      TEXT DEFAULT '正常',
            missing_reason    TEXT DEFAULT '',
            created_by        INTEGER,
            match_status      TEXT DEFAULT '未扫'
                              CHECK(match_status IN ('一致', '不一致', '未扫')),
            scanned_at        TEXT
        );
    """)
    # 兼容已经投入使用的数据库，按列增量升级，不清空历史数据。
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    migrations = {
        "assigned_at": "ALTER TABLE tasks ADD COLUMN assigned_at TEXT",
        "completed_at": "ALTER TABLE tasks ADD COLUMN completed_at TEXT",
        "is_deleted": "ALTER TABLE tasks ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "deleted_at": "ALTER TABLE tasks ADD COLUMN deleted_at TEXT",
        "deleted_reason": "ALTER TABLE tasks ADD COLUMN deleted_reason TEXT DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in task_columns:
            conn.execute(statement)
    record_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scan_records)")}
    record_migrations = {
        "evidence_photo": "ALTER TABLE scan_records ADD COLUMN evidence_photo TEXT DEFAULT ''",
        "review_status": "ALTER TABLE scan_records ADD COLUMN review_status TEXT DEFAULT '待复核'",
        "review_note": "ALTER TABLE scan_records ADD COLUMN review_note TEXT DEFAULT ''",
        "review_reason": "ALTER TABLE scan_records ADD COLUMN review_reason TEXT DEFAULT ''",
        "review_handler": "ALTER TABLE scan_records ADD COLUMN review_handler TEXT DEFAULT ''",
        "review_result": "ALTER TABLE scan_records ADD COLUMN review_result TEXT DEFAULT ''",
        "reviewed_at": "ALTER TABLE scan_records ADD COLUMN reviewed_at TEXT",
        "rack_no": "ALTER TABLE scan_records ADD COLUMN rack_no TEXT DEFAULT ''",
        "record_type": "ALTER TABLE scan_records ADD COLUMN record_type TEXT DEFAULT 'normal'",
        "field_status": "ALTER TABLE scan_records ADD COLUMN field_status TEXT DEFAULT '正常'",
        "missing_reason": "ALTER TABLE scan_records ADD COLUMN missing_reason TEXT DEFAULT ''",
        "created_by": "ALTER TABLE scan_records ADD COLUMN created_by INTEGER",
    }
    for column, statement in record_migrations.items():
        if column not in record_columns:
            conn.execute(statement)
    conn.execute("""
        UPDATE tasks
        SET assigned_at = created_at
        WHERE assigned_to IS NOT NULL AND assigned_at IS NULL
    """)
    conn.execute("""
        UPDATE tasks
        SET completed_at = (
            SELECT MAX(scanned_at) FROM scan_records WHERE scan_records.task_id = tasks.id
        )
        WHERE status = 'done' AND completed_at IS NULL
    """)
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
# 用户相关
# ──────────────────────────────────────────────

def create_user(wx_openid: str, name: str, role: str) -> dict[str, Any]:
    """创建用户"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (wx_openid, name, role) VALUES (?, ?, ?)",
            (wx_openid, name, role),
        )
        conn.commit()
        return {"id": cur.lastrowid, "wx_openid": wx_openid, "name": name, "role": role}
    except sqlite3.IntegrityError:
        # 已存在则返回已有的
        return dict_from_row(conn.execute(
            "SELECT * FROM users WHERE wx_openid = ?", (wx_openid,)
        ).fetchone())
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """按ID查用户"""
    conn = get_conn()
    try:
        return dict_from_row(conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone())
    finally:
        conn.close()


def get_workers() -> list[dict[str, Any]]:
    """查询所有现场人员（工人角色）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM users WHERE role = 'worker' ORDER BY id"
        ).fetchall()
        return [add_compare_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 任务相关
# ──────────────────────────────────────────────

def create_task(task_name: str, room_name: str, assigned_to: int | None) -> int:
    """创建盘点任务，返回新任务ID"""
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        cur = conn.execute(
            """INSERT INTO tasks
               (task_name, room_name, assigned_to, status, created_at, assigned_at)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (task_name, room_name, assigned_to, now, now if assigned_to is not None else None),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def assign_task(task_id: int, assigned_to: int) -> bool:
    """给某个机房盘点任务指定现场人员。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE tasks SET assigned_to = ?, assigned_at = ? WHERE id = ? AND is_deleted = 0",
            (assigned_to, datetime.now().isoformat(), task_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def batch_assign_tasks(assignments: list[dict[str, int]]) -> int:
    """批量下发任务，返回实际更新的任务数量。"""
    conn = get_conn()
    try:
        count = 0
        for item in assignments:
            cur = conn.execute(
                "UPDATE tasks SET assigned_to = ?, assigned_at = ? WHERE id = ? AND is_deleted = 0",
                (item["assigned_to"], datetime.now().isoformat(), item["task_id"]),
            )
            count += cur.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def delete_task(task_id: int, reason: str) -> bool:
    """作废任务并保留历史数据，默认业务查询不再显示。"""
    conn = get_conn()
    try:
        task = conn.execute(
            "SELECT id FROM tasks WHERE id = ? AND is_deleted = 0", (task_id,)
        ).fetchone()
        if task is None:
            return False
        conn.execute(
            "UPDATE tasks SET is_deleted = 1, deleted_at = ?, deleted_reason = ? WHERE id = ?",
            (datetime.now().isoformat(), reason.strip(), task_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_tasks_by_user(user_id: int) -> list[dict[str, Any]]:
    """查询某人的任务列表（含每个任务的设备统计）"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                t.id, t.task_name, t.room_name, t.status, t.created_at,
                t.assigned_at, t.completed_at,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' THEN 1 ELSE 0 END) AS total,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' AND sr.match_status != '未扫' THEN 1 ELSE 0 END) AS scanned,
                SUM(CASE WHEN sr.match_status = '不一致' THEN 1 ELSE 0 END) AS mismatch
            FROM tasks t
            LEFT JOIN scan_records sr ON sr.task_id = t.id
            WHERE t.assigned_to = ? AND t.is_deleted = 0
            GROUP BY t.id
            ORDER BY t.created_at DESC
        """, (user_id,)).fetchall()
        return [add_compare_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


def get_task_by_id(task_id: int) -> dict[str, Any] | None:
    """查询单个任务"""
    conn = get_conn()
    try:
        return dict_from_row(conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND is_deleted = 0", (task_id,)
        ).fetchone())
    finally:
        conn.close()


def get_all_tasks() -> list[dict[str, Any]]:
    """查询所有任务（管理员看板用）"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                t.id, t.task_name, t.room_name, t.status, t.created_at,
                t.assigned_at, t.completed_at,
                u.name AS assigned_name,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' THEN 1 ELSE 0 END) AS total,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' AND sr.match_status != '未扫' THEN 1 ELSE 0 END) AS scanned,
                SUM(CASE WHEN sr.match_status = '不一致' THEN 1 ELSE 0 END) AS mismatch
            FROM tasks t
            LEFT JOIN scan_records sr ON sr.task_id = t.id
            LEFT JOIN users u ON u.id = t.assigned_to
            WHERE t.is_deleted = 0
            GROUP BY t.id
            ORDER BY t.created_at DESC
        """).fetchall()
        return [dict_from_row(r) for r in rows]
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 盘点记录相关
# ──────────────────────────────────────────────

def batch_create_records(task_id: int, devices: list[dict[str, Any]]) -> int:
    """批量导入设备清单，返回导入数量"""
    conn = get_conn()
    try:
        data = []
        for dev in devices:
            data.append((
                task_id,
                dev["device_name"],
                json.dumps(dev.get("device_extra", {}), ensure_ascii=False),
                dev["expected_model"],
                dev.get("expected_barcode", ""),
            ))
        conn.executemany(
            "INSERT INTO scan_records (task_id, device_name, device_extra, expected_model, expected_barcode) VALUES (?, ?, ?, ?, ?)",
            data,
        )
        conn.commit()
        return len(data)
    finally:
        conn.close()


def get_task_items(task_id: int) -> list[dict[str, Any]]:
    """查询某个任务下的全部设备清单"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM scan_records WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return [add_compare_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


def get_record_by_id(record_id: int) -> dict[str, Any] | None:
    """按记录 ID 查询单条盘点记录。"""
    conn = get_conn()
    try:
        return dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone())
    finally:
        conn.close()


def save_evidence_photo(record_id: int, photo_path: str) -> bool:
    """保存无条码现场照片相对路径。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE scan_records SET evidence_photo = ? WHERE id = ?",
            (photo_path, record_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def review_record(
    record_id: int,
    status: str,
    note: str,
    reason: str = "",
    handler: str = "",
    result: str = "",
) -> bool:
    """管理员复核异常记录。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            """UPDATE scan_records
               SET review_status = ?, review_note = ?, review_reason = ?,
                   review_handler = ?, review_result = ?, reviewed_at = ?
               WHERE id = ? AND match_status != '未扫'""",
            (
                status,
                note.strip(),
                reason.strip(),
                handler.strip(),
                result.strip(),
                datetime.now().isoformat(),
                record_id,
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def refresh_task_status(conn: sqlite3.Connection, task_id: int) -> None:
    """按现有记录刷新任务状态；盘盈不影响原清单是否全部完成。"""
    task_items = conn.execute(
        "SELECT match_status, record_type FROM scan_records WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    normal_items = [r for r in task_items if (r["record_type"] or "normal") != "surplus"]
    all_normal_done = bool(normal_items) and all(r["match_status"] != "未扫" for r in normal_items)
    any_done = any(r["match_status"] != "未扫" for r in task_items)
    if all_normal_done:
        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), task_id),
        )
    elif any_done:
        conn.execute(
            "UPDATE tasks SET status = 'doing', completed_at = NULL WHERE id = ? AND status != 'done'",
            (task_id,),
        )


def mark_record_missing(record_id: int, reason: str, notes: str = "") -> dict[str, Any] | None:
    """把任务清单内设备标记为盘亏：现场应有但未找到。"""
    conn = get_conn()
    try:
        record = dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone())
        if record is None:
            return None
        if (record.get("record_type") or "normal") == "surplus":
            raise ValueError("盘盈记录不能标记为盘亏")

        final_reason = reason.strip()
        if not final_reason:
            raise ValueError("请选择盘亏原因")
        conn.execute("""
            UPDATE scan_records SET
                record_type = 'missing',
                field_status = '盘亏',
                missing_reason = ?,
                notes = ?,
                match_status = '不一致',
                scanned_at = ?,
                review_status = '待复核'
            WHERE id = ?
        """, (
            final_reason,
            notes.strip() or final_reason,
            datetime.now().isoformat(),
            record_id,
        ))
        refresh_task_status(conn, record["task_id"])
        conn.commit()
        return add_compare_status(dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone()))
    finally:
        conn.close()


def create_surplus_record(
    task_id: int,
    device_name: str,
    actual_model: str,
    asset_barcodes: str,
    asset_no_barcode: bool,
    cabinet_barcodes: str,
    cabinet_no_barcode: bool,
    rack_no: str,
    notes: str,
    created_by: int | None = None,
) -> dict[str, Any] | None:
    """登记盘盈设备：现场多出的、原任务清单中没有的设备。"""
    conn = get_conn()
    try:
        task = conn.execute(
            "SELECT id FROM tasks WHERE id = ? AND is_deleted = 0", (task_id,)
        ).fetchone()
        if task is None:
            return None
        if not device_name.strip():
            raise ValueError("请填写盘盈设备名称")
        if not rack_no.strip():
            raise ValueError("请填写现场机架号")
        if not asset_no_barcode and not asset_barcodes.strip():
            raise ValueError("请扫码资产条码或勾选无条码")
        if not cabinet_no_barcode and not cabinet_barcodes.strip():
            raise ValueError("请扫码机柜条码或勾选无条码")

        cur = conn.execute("""
            INSERT INTO scan_records (
                task_id, device_name, expected_model, expected_barcode,
                asset_barcodes, asset_no_barcode, cabinet_barcodes, cabinet_no_barcode,
                rack_no, actual_model, notes, record_type, field_status,
                match_status, scanned_at, created_by, review_status
            ) VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, 'surplus', '盘盈', '不一致', ?, ?, '待复核')
        """, (
            task_id,
            device_name.strip(),
            asset_barcodes.strip(),
            1 if asset_no_barcode else 0,
            cabinet_barcodes.strip(),
            1 if cabinet_no_barcode else 0,
            rack_no.strip(),
            actual_model.strip(),
            notes.strip(),
            datetime.now().isoformat(),
            created_by,
        ))
        refresh_task_status(conn, task_id)
        conn.commit()
        return add_compare_status(dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (cur.lastrowid,)
        ).fetchone()))
    finally:
        conn.close()


def submit_scan(
    record_id: int,
    asset_barcodes: str,
    asset_no_barcode: bool,
    cabinet_barcodes: str,
    cabinet_no_barcode: bool,
    rack_no: str,
    actual_model: str,
    notes: str,
) -> dict[str, Any] | None:
    """提交一条盘点记录，返回更新后的记录"""
    conn = get_conn()
    try:
        # 先查期望型号，用于比对
        record = dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone())
        if record is None:
            return None

        if not rack_no.strip():
            raise ValueError("请填写现场机架号")

        if asset_no_barcode or cabinet_no_barcode:
            if not record.get("evidence_photo"):
                raise ValueError("无条码设备必须上传现场照片")
            if not evidence_photo_is_valid(record.get("evidence_photo") or ""):
                raise ValueError("照片已过期，请重新盘查")

        model_match_status = calc_model_match_status(actual_model, record["expected_model"])
        asset_match_status = calc_asset_match_status(
            asset_barcodes,
            asset_no_barcode,
            record["expected_barcode"],
        )
        match_status = "一致" if asset_match_status == "一致" else "不一致"

        conn.execute("""
            UPDATE scan_records SET
                asset_barcodes = ?,
                asset_no_barcode = ?,
                cabinet_barcodes = ?,
                cabinet_no_barcode = ?,
                rack_no = ?,
                actual_model = ?,
                notes = ?,
                record_type = 'normal',
                field_status = '正常',
                missing_reason = '',
                match_status = ?,
                scanned_at = ?
            WHERE id = ?
        """, (
            asset_barcodes,
            1 if asset_no_barcode else 0,
            cabinet_barcodes,
            1 if cabinet_no_barcode else 0,
            rack_no.strip(),
            actual_model,
            notes,
            match_status,
            datetime.now().isoformat(),
            record_id,
        ))
        conn.commit()

        # 更新任务状态：如果原清单全部处理完则标记为 done，盘盈只作为异常补充。
        refresh_task_status(conn, record["task_id"])
        conn.commit()

        # 返回更新后的记录
        return add_compare_status(dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone()))
    finally:
        conn.close()


def get_report(task_id: int) -> list[dict[str, Any]]:
    """获取盘点报告"""
    conn = get_conn()
    try:
        task = dict_from_row(conn.execute(
            "SELECT t.*, u.name AS assigned_name FROM tasks t LEFT JOIN users u ON u.id = t.assigned_to WHERE t.id = ?",
            (task_id,),
        ).fetchone())
        if task is None:
            return []

        records = conn.execute(
            "SELECT * FROM scan_records WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return [add_compare_status(dict_from_row(r)) for r in records]
    finally:
        conn.close()
