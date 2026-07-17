"""数据库操作模块"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 数据库文件放在 data 目录下
DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "inventory.db"
EVIDENCE_DIR = DB_DIR / "evidence"
BACKUP_DIR = DB_DIR / "backups"
EVIDENCE_RETENTION_DAYS = 30
DB_TIMEOUT_SECONDS = float(os.getenv("INVENTORY_SQLITE_TIMEOUT_SECONDS", "30"))
DB_BUSY_TIMEOUT_MS = int(DB_TIMEOUT_SECONDS * 1000)
DB_BACKUP_KEEP = int(os.getenv("INVENTORY_BACKUP_KEEP", "10"))
EVIDENCE_TYPE_COLUMNS = {
    "general": "evidence_photo",
    "asset": "asset_evidence_photo",
    "cabinet": "cabinet_evidence_photo",
}


# ──────────────────────────────────────────────
# 初始化 & 工具函数
# ──────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    """获取数据库连接，每次用完后要 close()"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row  # 让查询结果可以用列名访问
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
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


def split_area_names(value: str) -> list[str]:
    """把账号可负责片区拆成列表，支持：北区,南区,西区 / 北区、南区、西区。"""
    raw_value = (value or "默认片区").strip()
    if raw_value in {"全部片区", "全片区", "*"}:
        return ["全部片区"]
    parts = re.split(r"[,，、/;；\s]+", raw_value)
    return [part.strip() for part in parts if part.strip()]


def user_can_access_area(user_area_name: str, task_area_name: str) -> bool:
    """判断现场账号是否可以查看/领取某个片区的任务。"""
    areas = split_area_names(user_area_name)
    return "全部片区" in areas or (task_area_name or "默认片区") in areas


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


def evidence_photo_for_type(record: dict[str, Any], evidence_type: str) -> str:
    """按凭证类型取照片；兼容旧版本单张 evidence_photo。"""
    column = EVIDENCE_TYPE_COLUMNS.get(evidence_type, "evidence_photo")
    return record.get(column) or record.get("evidence_photo") or ""


def evidence_photo_is_valid_for_type(record: dict[str, Any], evidence_type: str) -> bool:
    return evidence_photo_is_valid(evidence_photo_for_type(record, evidence_type))


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


def backup_database(reason: str = "manual") -> str | None:
    """生成一份 SQLite 在线备份，并按保留数量清理旧备份。"""
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "_", reason).strip("_") or "backup"
    backup_path = BACKUP_DIR / f"inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_reason}.db"
    source = sqlite3.connect(str(DB_PATH), timeout=DB_TIMEOUT_SECONDS)
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    backups = sorted(BACKUP_DIR.glob("inventory_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_backup in backups[DB_BACKUP_KEEP:]:
        old_backup.unlink(missing_ok=True)
    return str(backup_path)


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


def calc_overall_match_status(asset_match_status: str, model_match_status: str) -> str:
    """总体比对结果：资产条码和型号必须同时一致。"""
    return "一致" if asset_match_status == "一致" and model_match_status == "一致" else "不一致"


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


def add_task_closure_status(task: dict[str, Any]) -> dict[str, Any]:
    """给任务补充现场完成和管理闭环状态，不改变原有 status 含义。"""
    status = task.get("status")
    review_pending = int(task.get("review_pending") or 0)
    if status != "done":
        task["field_status"] = "现场未完成"
        task["closure_status"] = "现场未完成"
    elif review_pending > 0:
        task["field_status"] = "现场已完成"
        task["closure_status"] = "待闭环"
    else:
        task["field_status"] = "现场已完成"
        task["closure_status"] = "已闭环"
    return task


def normalize_existing_scan_match_status(conn: sqlite3.Connection) -> None:
    """把历史已扫描记录按资产条码+型号的新总体口径重算一次。"""
    rows = conn.execute("""
        SELECT * FROM scan_records
        WHERE COALESCE(record_type, 'normal') = 'normal'
          AND match_status != '未扫'
    """).fetchall()
    affected_task_ids: set[int] = set()
    for row in rows:
        asset_match_status = calc_asset_match_status(
            row["asset_barcodes"] or "",
            bool(row["asset_no_barcode"]),
            row["expected_barcode"] or "",
        )
        model_match_status = calc_model_match_status(row["actual_model"] or "", row["expected_model"] or "")
        overall_status = calc_overall_match_status(asset_match_status, model_match_status)
        if row["match_status"] == overall_status:
            continue
        conn.execute(
            """UPDATE scan_records
               SET match_status = ?,
                   review_status = CASE
                       WHEN ? = '一致' THEN COALESCE(review_status, '')
                       WHEN COALESCE(review_status, '') = '' THEN '待复核'
                       ELSE review_status
                   END
               WHERE id = ?""",
            (overall_status, overall_status, row["id"]),
        )
        affected_task_ids.add(row["task_id"])
    for task_id in affected_task_ids:
        refresh_task_status(conn, task_id)


def init_db() -> None:
    """初始化数据库，建表（重复运行不会覆盖）"""
    if DB_PATH.exists():
        backup_database("startup")
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            wx_openid   TEXT UNIQUE,
            name        TEXT NOT NULL,
            role        TEXT NOT NULL CHECK(role IN ('admin', 'worker')),
            area_name   TEXT DEFAULT '默认片区',
            phone       TEXT DEFAULT '',
            employee_no TEXT DEFAULT '',
            is_active   INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name   TEXT NOT NULL,
            room_name   TEXT NOT NULL,
            area_name   TEXT DEFAULT '默认片区',
            assigned_to INTEGER REFERENCES users(id),
            claimed_by  INTEGER REFERENCES users(id),
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'doing', 'done')),
            created_at  TEXT NOT NULL,
            claimed_at  TEXT,
            field_completed_at TEXT,
            closed_at   TEXT
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

        CREATE TABLE IF NOT EXISTS audit_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id    INTEGER,
            actor_name  TEXT DEFAULT '',
            actor_role  TEXT DEFAULT '',
            action      TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id   INTEGER,
            detail_json TEXT DEFAULT '{}',
            request_id  TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS idempotency_keys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            action      TEXT NOT NULL,
            request_id  TEXT NOT NULL,
            target_type TEXT DEFAULT '',
            target_id   INTEGER,
            response_json TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE(user_id, action, request_id)
        );

    """)
    # 兼容已经投入使用的数据库，按列增量升级，不清空历史数据。
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    migrations = {
        "area_name": "ALTER TABLE tasks ADD COLUMN area_name TEXT DEFAULT '默认片区'",
        "assigned_at": "ALTER TABLE tasks ADD COLUMN assigned_at TEXT",
        "completed_at": "ALTER TABLE tasks ADD COLUMN completed_at TEXT",
        "claimed_by": "ALTER TABLE tasks ADD COLUMN claimed_by INTEGER",
        "claimed_at": "ALTER TABLE tasks ADD COLUMN claimed_at TEXT",
        "released_at": "ALTER TABLE tasks ADD COLUMN released_at TEXT",
        "release_reason": "ALTER TABLE tasks ADD COLUMN release_reason TEXT DEFAULT ''",
        "is_deleted": "ALTER TABLE tasks ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "deleted_at": "ALTER TABLE tasks ADD COLUMN deleted_at TEXT",
        "deleted_reason": "ALTER TABLE tasks ADD COLUMN deleted_reason TEXT DEFAULT ''",
        "field_completed_at": "ALTER TABLE tasks ADD COLUMN field_completed_at TEXT",
        "closed_at": "ALTER TABLE tasks ADD COLUMN closed_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in task_columns:
            conn.execute(statement)
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    user_migrations = {
        "area_name": "ALTER TABLE users ADD COLUMN area_name TEXT DEFAULT '默认片区'",
        "phone": "ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''",
        "employee_no": "ALTER TABLE users ADD COLUMN employee_no TEXT DEFAULT ''",
        "is_active": "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
        "is_deleted": "ALTER TABLE users ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "deleted_at": "ALTER TABLE users ADD COLUMN deleted_at TEXT",
        "password_hash": "ALTER TABLE users ADD COLUMN password_hash TEXT DEFAULT ''",
    }
    for column, statement in user_migrations.items():
        if column not in user_columns:
            conn.execute(statement)
    record_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scan_records)")}
    record_migrations = {
        "evidence_photo": "ALTER TABLE scan_records ADD COLUMN evidence_photo TEXT DEFAULT ''",
        "asset_evidence_photo": "ALTER TABLE scan_records ADD COLUMN asset_evidence_photo TEXT DEFAULT ''",
        "cabinet_evidence_photo": "ALTER TABLE scan_records ADD COLUMN cabinet_evidence_photo TEXT DEFAULT ''",
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
        "client_request_id": "ALTER TABLE scan_records ADD COLUMN client_request_id TEXT DEFAULT ''",
    }
    for column, statement in record_migrations.items():
        if column not in record_columns:
            conn.execute(statement)
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS audit_logs_no_update
        BEFORE UPDATE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs are immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete
        BEFORE DELETE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs are immutable');
        END;

        CREATE INDEX IF NOT EXISTS idx_tasks_deleted_status_area
            ON tasks(is_deleted, status, area_name);
        CREATE INDEX IF NOT EXISTS idx_tasks_claimed_by
            ON tasks(claimed_by);
        CREATE INDEX IF NOT EXISTS idx_scan_records_task_status
            ON scan_records(task_id, match_status, record_type);
        CREATE INDEX IF NOT EXISTS idx_scan_records_review
            ON scan_records(task_id, review_status);
        CREATE INDEX IF NOT EXISTS idx_scan_records_created_by
            ON scan_records(created_by);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_records_client_request
            ON scan_records(created_by, client_request_id)
            WHERE client_request_id != '';
        CREATE INDEX IF NOT EXISTS idx_users_openid_deleted
            ON users(wx_openid, is_deleted);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_created
            ON audit_logs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_target
            ON audit_logs(target_type, target_id);
        CREATE INDEX IF NOT EXISTS idx_idempotency_lookup
            ON idempotency_keys(user_id, action, request_id);
    """)
    conn.execute("""
        UPDATE tasks
        SET assigned_at = created_at
        WHERE assigned_to IS NOT NULL AND assigned_at IS NULL
    """)
    conn.execute("""
        UPDATE tasks
        SET area_name = '默认片区'
        WHERE area_name IS NULL OR area_name = ''
    """)
    conn.execute("""
        UPDATE users
        SET area_name = '默认片区'
        WHERE role = 'worker' AND (area_name IS NULL OR area_name = '')
    """)
    conn.execute("""
        UPDATE tasks
        SET claimed_by = assigned_to,
            claimed_at = assigned_at
        WHERE claimed_by IS NULL AND assigned_to IS NOT NULL
    """)
    conn.execute("""
        UPDATE tasks
        SET completed_at = (
            SELECT MAX(scanned_at) FROM scan_records WHERE scan_records.task_id = tasks.id
        )
        WHERE status = 'done' AND completed_at IS NULL
    """)
    conn.execute("""
        UPDATE tasks
        SET field_completed_at = completed_at
        WHERE status = 'done' AND field_completed_at IS NULL
    """)
    normalize_existing_scan_match_status(conn)
    conn.commit()
    conn.close()


def get_user_for_auth(user_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        return dict_from_row(conn.execute(
            "SELECT * FROM users WHERE id=? AND is_active=1 AND is_deleted=0", (user_id,)
        ).fetchone())
    finally:
        conn.close()


def get_user_by_openid(wx_openid: str) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        return dict_from_row(conn.execute(
            "SELECT * FROM users WHERE wx_openid=? AND is_deleted=0", (wx_openid,)
        ).fetchone())
    finally:
        conn.close()


def set_password_hash(user_id: int, password_hash: str) -> None:
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        conn.commit()
    finally:
        conn.close()


def create_audit_log(
    actor: dict[str, Any] | None,
    action: str,
    target_type: str,
    target_id: int | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str = "",
) -> int:
    """追加一条不可变操作审计日志。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO audit_logs
               (actor_id, actor_name, actor_role, action, target_type, target_id, detail_json, request_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                actor.get("id") if actor else None,
                actor.get("name", "") if actor else "",
                actor.get("role", "") if actor else "",
                action,
                target_type,
                target_id,
                json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                request_id.strip(),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_audit_logs(limit: int = 200) -> list[dict[str, Any]]:
    """查询最近的操作审计日志。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, actor_id, actor_name, actor_role, action, target_type, target_id,
                      detail_json, request_id, created_at
               FROM audit_logs
               ORDER BY id DESC
               LIMIT ?""",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        result = []
        for row in rows:
            item = dict_from_row(row)
            try:
                item["detail"] = json.loads(item.pop("detail_json") or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            result.append(item)
        return result
    finally:
        conn.close()


def get_idempotent_result(user_id: int, action: str, request_id: str) -> dict[str, Any] | None:
    """按客户端请求 ID 查询已经成功处理过的响应。"""
    request_id = (request_id or "").strip()
    if not request_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT response_json FROM idempotency_keys
               WHERE user_id = ? AND action = ? AND request_id = ?""",
            (user_id, action, request_id),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["response_json"])
    finally:
        conn.close()


def save_idempotent_result(
    user_id: int,
    action: str,
    request_id: str,
    response: dict[str, Any],
    target_type: str = "",
    target_id: int | None = None,
) -> dict[str, Any]:
    """保存成功响应；若并发重复写入，返回已存在的响应。"""
    request_id = (request_id or "").strip()
    if not request_id:
        return response
    conn = get_conn()
    try:
        try:
            conn.execute(
                """INSERT INTO idempotency_keys
                   (user_id, action, request_id, target_type, target_id, response_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    action,
                    request_id,
                    target_type,
                    target_id,
                    json.dumps(response, ensure_ascii=False, sort_keys=True),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            return response
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute(
                """SELECT response_json FROM idempotency_keys
                   WHERE user_id = ? AND action = ? AND request_id = ?""",
                (user_id, action, request_id),
            ).fetchone()
            return json.loads(row["response_json"]) if row else response
    finally:
        conn.close()




# ──────────────────────────────────────────────
# 用户相关
# ──────────────────────────────────────────────

def create_user(
    wx_openid: str,
    name: str,
    role: str,
    area_name: str = "默认片区",
    phone: str = "",
    employee_no: str = "",
    is_active: int = 1,
) -> dict[str, Any]:
    """创建或更新用户，供默认账号初始化和 PC 账号管理共用。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO users
               (wx_openid, name, role, area_name, phone, employee_no, is_active, is_deleted, deleted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)""",
            (
                wx_openid.strip(),
                name.strip(),
                role.strip(),
                area_name.strip() or "默认片区",
                phone.strip(),
                employee_no.strip(),
                1 if is_active else 0,
            ),
        )
        conn.commit()
        return dict_from_row(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError:
        conn.execute(
            """UPDATE users
               SET name = ?, role = ?, area_name = ?, phone = ?, employee_no = ?, is_active = ?,
                   is_deleted = 0, deleted_at = NULL
               WHERE wx_openid = ?""",
            (
                name.strip(),
                role.strip(),
                area_name.strip() or "默认片区",
                phone.strip(),
                employee_no.strip(),
                1 if is_active else 0,
                wx_openid.strip(),
            ),
        )
        conn.commit()
        # 已存在则返回已有的
        return dict_from_row(conn.execute(
            "SELECT * FROM users WHERE wx_openid = ? AND COALESCE(is_deleted, 0) = 0", (wx_openid.strip(),)
        ).fetchone())
    finally:
        conn.close()


def get_users() -> list[dict[str, Any]]:
    """查询全部小程序账号，供 PC 后台账号管理使用。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, wx_openid, name, role, area_name, phone, employee_no, is_active
               FROM users
               WHERE COALESCE(is_deleted, 0) = 0
               ORDER BY role, area_name, id"""
        ).fetchall()
        return [dict_from_row(row) for row in rows]
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
            "SELECT * FROM users WHERE role = 'worker' AND COALESCE(is_deleted, 0) = 0 ORDER BY id"
        ).fetchall()
        return [add_compare_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 任务相关
# ──────────────────────────────────────────────

def create_task(task_name: str, room_name: str, assigned_to: int | None, area_name: str = "默认片区") -> int:
    """创建盘点任务，返回新任务ID"""
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        status = "doing" if assigned_to is not None else "pending"
        cur = conn.execute(
            """INSERT INTO tasks
               (task_name, room_name, area_name, assigned_to, claimed_by, status, created_at, assigned_at, claimed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_name,
                room_name,
                area_name or "默认片区",
                assigned_to,
                assigned_to,
                status,
                now,
                now if assigned_to is not None else None,
                now if assigned_to is not None else None,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_user(user_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    """修改小程序账号信息。只更新 PC 账号管理允许维护的字段。"""
    allowed_fields = ["wx_openid", "name", "role", "area_name", "phone", "employee_no", "is_active"]
    update_data = {field: data[field] for field in allowed_fields if field in data}
    if not update_data:
        return get_user_by_id(user_id)

    for text_field in ["wx_openid", "name", "role", "area_name", "phone", "employee_no"]:
        if text_field in update_data and update_data[text_field] is not None:
            update_data[text_field] = str(update_data[text_field]).strip()
    if "area_name" in update_data and not update_data["area_name"]:
        update_data["area_name"] = "默认片区"
    if "is_active" in update_data:
        update_data["is_active"] = 1 if update_data["is_active"] else 0

    set_sql = ", ".join(f"{field} = ?" for field in update_data)
    params = list(update_data.values()) + [user_id]
    conn = get_conn()
    try:
        try:
            cur = conn.execute(
                f"UPDATE users SET {set_sql} WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
                params,
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("登录编码已存在，请换一个编码") from exc
        if cur.rowcount == 0:
            return None
        return dict_from_row(conn.execute(
            "SELECT * FROM users WHERE id = ? AND COALESCE(is_deleted, 0) = 0", (user_id,)
        ).fetchone())
    finally:
        conn.close()


def delete_user(user_id: int) -> bool:
    """业务删除小程序账号：隐藏账号并禁止登录，保留历史任务引用。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            """UPDATE users
               SET is_deleted = 1, is_active = 0, deleted_at = ?
               WHERE id = ? AND COALESCE(is_deleted, 0) = 0""",
            (datetime.now().isoformat(), user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def assign_task(task_id: int, assigned_to: int) -> bool:
    """给某个机房盘点任务指定现场人员。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            """UPDATE tasks
               SET assigned_to = ?, claimed_by = ?, assigned_at = ?, claimed_at = ?, status = 'doing',
                   completed_at = NULL, field_completed_at = NULL, closed_at = NULL
               WHERE id = ? AND is_deleted = 0""",
            (assigned_to, assigned_to, datetime.now().isoformat(), datetime.now().isoformat(), task_id),
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
                """UPDATE tasks
                   SET assigned_to = ?, claimed_by = ?, assigned_at = ?, claimed_at = ?, status = 'doing',
                       completed_at = NULL, field_completed_at = NULL, closed_at = NULL
                   WHERE id = ? AND is_deleted = 0""",
                (item["assigned_to"], item["assigned_to"], datetime.now().isoformat(), datetime.now().isoformat(), item["task_id"]),
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
    """查询某人已领取的任务列表（含每个任务的设备统计）。"""
    return get_claimed_tasks(user_id)


def _task_stat_select(where_sql: str) -> str:
    """任务列表通用查询片段，避免多处统计口径不一致。"""
    return f"""
            SELECT
                t.id, t.task_name, t.room_name, t.area_name, t.status, t.created_at,
                t.assigned_to, t.assigned_at, t.claimed_by, t.claimed_at,
                t.completed_at, t.field_completed_at, t.closed_at, t.released_at, t.release_reason,
                assigned_user.name AS assigned_name,
                claimed_user.name AS claimed_name,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' THEN 1 ELSE 0 END) AS total,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' AND sr.match_status != '未扫' THEN 1 ELSE 0 END) AS scanned,
                SUM(CASE WHEN sr.match_status = '不一致' THEN 1 ELSE 0 END) AS mismatch,
                SUM(CASE WHEN sr.id IS NOT NULL AND (COALESCE(sr.record_type, 'normal') IN ('surplus', 'missing') OR sr.match_status = '不一致') THEN 1 ELSE 0 END) AS anomaly_count,
                SUM(CASE WHEN sr.id IS NOT NULL AND (COALESCE(sr.record_type, 'normal') IN ('surplus', 'missing') OR sr.match_status = '不一致') AND COALESCE(sr.review_status, '待复核') != '已闭环' THEN 1 ELSE 0 END) AS review_pending
            FROM tasks t
            LEFT JOIN scan_records sr ON sr.task_id = t.id
            LEFT JOIN users assigned_user ON assigned_user.id = t.assigned_to
            LEFT JOIN users claimed_user ON claimed_user.id = t.claimed_by
            WHERE {where_sql} AND t.is_deleted = 0
            GROUP BY t.id
        """


def get_area_tasks(user_id: int) -> list[dict[str, Any]]:
    """查询现场人员所在片区的任务池：待领取、盘点中、已完成都展示。"""
    conn = get_conn()
    try:
        user = conn.execute(
            "SELECT area_name FROM users WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
            (user_id,),
        ).fetchone()
        if user is None:
            return []
        user_areas = split_area_names(user["area_name"])
        if "全部片区" in user_areas:
            where_sql = "1 = 1"
            params = ()
        else:
            placeholders = ",".join("?" for _ in user_areas)
            where_sql = f"t.area_name IN ({placeholders})"
            params = tuple(user_areas)
        rows = conn.execute(
            _task_stat_select(where_sql) + " ORDER BY CASE t.status WHEN 'pending' THEN 0 WHEN 'doing' THEN 1 ELSE 2 END, t.area_name, t.created_at DESC",
            params,
        ).fetchall()
        return [add_task_closure_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


def get_claimed_tasks(user_id: int) -> list[dict[str, Any]]:
    """查询某个现场人员已经领取的任务。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            _task_stat_select("t.claimed_by = ?") + " ORDER BY t.claimed_at DESC, t.created_at DESC",
            (user_id,),
        ).fetchall()
        return [add_task_closure_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


def claim_task(task_id: int, user_id: int) -> tuple[bool, str]:
    """现场人员领取片区任务；用条件更新保证多人同时领取时只有一人成功。"""
    conn = get_conn()
    try:
        user = conn.execute(
            "SELECT id, role, area_name, is_active FROM users WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
            (user_id,),
        ).fetchone()
        if user is None:
            return False, "用户不存在"
        if user["role"] != "worker" or not user["is_active"]:
            return False, "当前账号不能领取现场任务"

        now = datetime.now().isoformat()
        user_areas = split_area_names(user["area_name"])
        if "全部片区" in user_areas:
            area_limit_sql = "1 = 1"
            params = (user_id, user_id, now, now, task_id)
        else:
            placeholders = ",".join("?" for _ in user_areas)
            area_limit_sql = f"area_name IN ({placeholders})"
            params = (user_id, user_id, now, now, task_id, *user_areas)
        cur = conn.execute(
            f"""UPDATE tasks
               SET claimed_by = ?, assigned_to = ?, claimed_at = ?, assigned_at = ?,
                   status = 'doing', release_reason = ''
               WHERE id = ?
                 AND is_deleted = 0
                 AND status = 'pending'
                 AND claimed_by IS NULL
                 AND {area_limit_sql}""",
            params,
        )
        conn.commit()
        if cur.rowcount > 0:
            return True, "领取成功"

        task = conn.execute(
            """SELECT t.status, t.area_name, t.claimed_by, u.name AS claimed_name
               FROM tasks t
               LEFT JOIN users u ON u.id = t.claimed_by
               WHERE t.id = ? AND t.is_deleted = 0""",
            (task_id,),
        ).fetchone()
        if task is None:
            return False, "任务不存在"
        if not user_can_access_area(user["area_name"], task["area_name"]):
            return False, "该任务不属于你的片区"
        if task["status"] != "pending" or task["claimed_by"]:
            return False, f"任务已被{task['claimed_name'] or '其他人员'}领取"
        return False, "任务当前不可领取"
    finally:
        conn.close()


def release_task(task_id: int, user_id: int, reason: str = "") -> tuple[bool, str]:
    """现场人员释放已领取任务，任务回到片区待领取池。"""
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        cur = conn.execute(
            """UPDATE tasks
               SET claimed_by = NULL, assigned_to = NULL, claimed_at = NULL, assigned_at = NULL,
                   status = 'pending', released_at = ?, release_reason = ?
               WHERE id = ?
                 AND is_deleted = 0
                 AND status = 'doing'
                 AND claimed_by = ?""",
            (now, reason.strip(), task_id, user_id),
        )
        conn.commit()
        if cur.rowcount > 0:
            return True, "任务已释放，其他同片区人员可重新领取"
        task = conn.execute(
            "SELECT status, claimed_by FROM tasks WHERE id = ? AND is_deleted = 0",
            (task_id,),
        ).fetchone()
        if task is None:
            return False, "任务不存在"
        if task["claimed_by"] != user_id:
            return False, "只能释放自己领取的任务"
        if task["status"] == "done":
            return False, "已完成任务不能释放"
        return False, "任务当前不能释放"
    finally:
        conn.close()


def get_task_by_id(task_id: int) -> dict[str, Any] | None:
    """查询单个任务"""
    conn = get_conn()
    try:
        row = conn.execute(
            _task_stat_select("t.id = ?"),
            (task_id,),
        ).fetchone()
        return add_task_closure_status(dict_from_row(row)) if row else None
    finally:
        conn.close()


def get_all_tasks() -> list[dict[str, Any]]:
    """查询所有任务（管理员看板用）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            _task_stat_select("1 = 1") + " ORDER BY t.created_at DESC"
        ).fetchall()
        return [add_task_closure_status(dict_from_row(r)) for r in rows]
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


def save_evidence_photo(record_id: int, photo_path: str, evidence_type: str = "general") -> bool:
    """保存无条码现场照片相对路径，支持区分资产/机柜凭证。"""
    column = EVIDENCE_TYPE_COLUMNS.get(evidence_type)
    if column is None:
        raise ValueError("凭证类型无效")
    conn = get_conn()
    try:
        if column == "evidence_photo":
            cur = conn.execute(
                "UPDATE scan_records SET evidence_photo = ? WHERE id = ?",
                (photo_path, record_id),
            )
        else:
            cur = conn.execute(
                f"UPDATE scan_records SET {column} = ?, evidence_photo = COALESCE(NULLIF(evidence_photo, ''), ?) WHERE id = ?",
                (photo_path, photo_path, record_id),
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
        record = conn.execute(
            "SELECT task_id FROM scan_records WHERE id = ? AND match_status != '未扫'",
            (record_id,),
        ).fetchone()
        if record is None:
            return False
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
        if cur.rowcount > 0:
            refresh_task_closure(conn, record["task_id"])
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def refresh_task_closure(conn: sqlite3.Connection, task_id: int, task_items: list[sqlite3.Row] | None = None) -> None:
    """刷新管理闭环时间；只有现场完成且异常全部闭环后才写 closed_at。"""
    if task_items is None:
        task_items = conn.execute(
            "SELECT match_status, record_type, review_status FROM scan_records WHERE task_id = ?",
            (task_id,),
        ).fetchall()
    normal_items = [r for r in task_items if (r["record_type"] or "normal") != "surplus"]
    all_normal_done = bool(normal_items) and all(r["match_status"] != "未扫" for r in normal_items)
    pending_review = [
        r for r in task_items
        if ((r["record_type"] or "normal") in {"surplus", "missing"} or r["match_status"] == "不一致")
        and (r["review_status"] or "待复核") != "已闭环"
    ]
    if all_normal_done and not pending_review:
        conn.execute(
            "UPDATE tasks SET closed_at = COALESCE(closed_at, ?) WHERE id = ?",
            (datetime.now().isoformat(), task_id),
        )
    else:
        conn.execute("UPDATE tasks SET closed_at = NULL WHERE id = ?", (task_id,))


def refresh_task_status(conn: sqlite3.Connection, task_id: int) -> None:
    """按现有记录刷新任务现场状态；盘盈不影响原清单是否全部完成。"""
    task_items = conn.execute(
        "SELECT match_status, record_type, review_status FROM scan_records WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    normal_items = [r for r in task_items if (r["record_type"] or "normal") != "surplus"]
    all_normal_done = bool(normal_items) and all(r["match_status"] != "未扫" for r in normal_items)
    any_done = any(r["match_status"] != "未扫" for r in task_items)
    if all_normal_done:
        conn.execute(
            """UPDATE tasks
               SET status = 'done',
                   completed_at = COALESCE(completed_at, ?),
                   field_completed_at = COALESCE(field_completed_at, ?)
               WHERE id = ?""",
            (datetime.now().isoformat(), datetime.now().isoformat(), task_id),
        )
    elif any_done:
        conn.execute(
            "UPDATE tasks SET status = 'doing', completed_at = NULL, field_completed_at = NULL WHERE id = ? AND status != 'done'",
            (task_id,),
        )
    refresh_task_closure(conn, task_id, task_items)


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
                review_status = '待复核',
                review_note = '',
                review_reason = '',
                review_handler = '',
                review_result = '',
                reviewed_at = NULL
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
    client_request_id: str = "",
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

        try:
            cur = conn.execute("""
                INSERT INTO scan_records (
                    task_id, device_name, expected_model, expected_barcode,
                    asset_barcodes, asset_no_barcode, cabinet_barcodes, cabinet_no_barcode,
                    rack_no, actual_model, notes, record_type, field_status,
                    match_status, scanned_at, created_by, review_status, client_request_id
                ) VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, 'surplus', '盘盈', '不一致', ?, ?, '待复核', ?)
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
                client_request_id.strip(),
            ))
        except sqlite3.IntegrityError:
            conn.rollback()
            if created_by is not None and client_request_id.strip():
                existing = conn.execute(
                    "SELECT * FROM scan_records WHERE created_by = ? AND client_request_id = ?",
                    (created_by, client_request_id.strip()),
                ).fetchone()
                if existing:
                    return add_compare_status(dict_from_row(existing))
            raise
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

        if asset_no_barcode and not evidence_photo_for_type(record, "asset"):
            raise ValueError("资产无条码设备必须上传资产无条码凭证")
        if asset_no_barcode and not evidence_photo_is_valid_for_type(record, "asset"):
            raise ValueError("资产无条码凭证已过期，请重新盘查")
        if cabinet_no_barcode and not evidence_photo_for_type(record, "cabinet"):
            raise ValueError("机柜无条码设备必须上传机柜无条码凭证")
        if cabinet_no_barcode and not evidence_photo_is_valid_for_type(record, "cabinet"):
            raise ValueError("机柜无条码凭证已过期，请重新盘查")

        model_match_status = calc_model_match_status(actual_model, record["expected_model"])
        asset_match_status = calc_asset_match_status(
            asset_barcodes,
            asset_no_barcode,
            record["expected_barcode"],
        )
        match_status = calc_overall_match_status(asset_match_status, model_match_status)

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
                scanned_at = ?,
                review_status = ?,
                review_note = '',
                review_reason = '',
                review_handler = '',
                review_result = '',
                reviewed_at = NULL
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
            "" if match_status == "一致" else "待复核",
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
