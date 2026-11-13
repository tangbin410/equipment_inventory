"""数据库操作模块"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Any

# 数据库文件放在 data 目录下
DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "inventory.db"
BACKUP_DIR = DB_DIR / "backups"
DB_TIMEOUT_SECONDS = float(os.getenv("INVENTORY_SQLITE_TIMEOUT_SECONDS", "30"))
DB_BUSY_TIMEOUT_MS = int(DB_TIMEOUT_SECONDS * 1000)
DB_BACKUP_KEEP = int(os.getenv("INVENTORY_BACKUP_KEEP", "10"))
USER_AREA_CORE = "汇聚及核心"
USER_AREA_CHOICES = ("汇聚及核心", "南区", "北区", "西区")
USER_AREA_SET = set(USER_AREA_CHOICES)
USER_AREA_LEGACY_CORE = {"", "全市", "默认片区", "全部片区", "全片区", "*"}
ADMIN_SCOPE_CHOICES = {"full", "collaborator"}
CABINET_TYPE_LABELS = {
    "equipment": "传输综合柜",
    "odf": "ODF",
    "iodf": "IODF",
}
CABINET_SOURCE_LABELS = {
    "device_scan": "设备盘点自动登记",
    "manual": "现场登记",
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
    """兼容中英文逗号、分号和换行，条码本身不拆分。"""
    return {normalize_barcode(code) for code in re.split(r"[,，;；\r\n]+", value or "") if normalize_barcode(code)}


def normalize_user_area_name(value: str | None, *, strict: bool = True) -> str:
    """账号负责片区统一收敛到：汇聚及核心、南区、北区、西区。"""
    raw_value = (value or "").strip()
    if raw_value in USER_AREA_LEGACY_CORE:
        if strict:
            raise ValueError("负责片区只能是：汇聚及核心、南区、北区、西区")
        return USER_AREA_CORE
    if raw_value in USER_AREA_SET:
        return raw_value

    parts = [part.strip() for part in re.split(r"[,，、/;；\s]+", raw_value) if part.strip()]
    part_set = set(parts)
    if {"南区", "北区", "西区"}.issubset(part_set):
        if strict:
            raise ValueError("负责片区只能是：汇聚及核心、南区、北区、西区，且不允许跨片区")
        return USER_AREA_CORE
    if len(part_set) == 1 and next(iter(part_set)) in USER_AREA_SET:
        return next(iter(part_set))
    if strict:
        raise ValueError("负责片区只能是：汇聚及核心、南区、北区、西区")
    return USER_AREA_CORE


def split_area_names(value: str) -> list[str]:
    """账号片区权限列表；账号不允许跨片区。"""
    return [normalize_user_area_name(value, strict=False)]


def user_can_access_area(user_area_name: str, task_area_name: str) -> bool:
    """判断现场账号是否可以查看/领取某个片区的任务。"""
    user_area = normalize_user_area_name(user_area_name, strict=False)
    task_area = normalize_user_area_name(task_area_name, strict=False)
    return user_area == task_area


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
    return "一致" if submitted_codes == expected_codes else "不一致"


def calc_overall_match_status(asset_match_status: str, model_match_status: str) -> str:
    """总体比对结果：资产条码和型号必须同时一致。"""
    return "一致" if asset_match_status == "一致" and model_match_status == "一致" else "不一致"


def add_compare_status(record: dict[str, Any]) -> dict[str, Any]:
    """给盘点记录补充资产条码和型号的独立比对结果。"""
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
    if record.get("match_status") != "未扫":
        record["match_status"] = calc_overall_match_status(
            record["asset_match_status"],
            record["model_match_status"],
        )
    return record


def add_task_field_status(task: dict[str, Any]) -> dict[str, Any]:
    """给任务补充现场阶段，不改变原有 status 含义。"""
    status = task.get("status")
    if status != "done":
        task["field_status"] = "现场未完成"
    else:
        task["field_status"] = "现场已完成"
    task["replacement_notice"] = (
        "该机房有最新任务，请释放当前任务并领取最新任务"
        if task.get("superseded_by_task_id") else
        "请等待原任务领取人释放当前任务后，再领取最新任务"
        if task.get("waiting_for_task_id") else ""
    )
    return task


def normalize_existing_scan_match_status(conn: sqlite3.Connection) -> None:
    """把历史已扫描记录按资产条码+型号的新总体口径重算一次。"""
    rows = conn.execute("""
        SELECT * FROM scan_records
        WHERE COALESCE(record_type, 'normal') = 'normal'
          AND match_status != '未扫'
    """).fetchall()
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
               SET match_status = ?
               WHERE id = ?""",
            (overall_status, row["id"]),
        )


def drop_legacy_scan_record_columns(conn: sqlite3.Connection) -> bool:
    """从旧库 scan_records 中移除已废弃的审核和现场凭证字段。"""
    legacy_columns = {
        "review_status",
        "review_note",
        "review_reason",
        "review_handler",
        "review_result",
        "reviewed_at",
        "evidence_photo",
        "asset_evidence_photo",
        "cabinet_evidence_photo",
    }
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scan_records)")}
    if not legacy_columns & existing_columns:
        return False

    conn.execute("DROP INDEX IF EXISTS idx_scan_records_review")
    conn.execute("""
        CREATE TABLE scan_records_new (
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
            client_request_id TEXT DEFAULT '',
            match_status      TEXT DEFAULT '未扫'
                              CHECK(match_status IN ('一致', '不一致', '未扫')),
            scanned_at        TEXT
        )
    """)
    target_columns = [
        "id",
        "task_id",
        "device_name",
        "device_extra",
        "expected_model",
        "expected_barcode",
        "asset_barcodes",
        "asset_no_barcode",
        "cabinet_barcodes",
        "cabinet_no_barcode",
        "rack_no",
        "actual_model",
        "notes",
        "record_type",
        "field_status",
        "missing_reason",
        "created_by",
        "client_request_id",
        "match_status",
        "scanned_at",
    ]
    copy_columns = [column for column in target_columns if column in existing_columns]
    column_sql = ", ".join(copy_columns)
    select_sql = ", ".join(
        "CASE WHEN match_status IN ('一致', '不一致', '未扫') THEN match_status ELSE '未扫' END AS match_status"
        if column == "match_status" else column
        for column in copy_columns
    )
    conn.execute(f"INSERT INTO scan_records_new ({column_sql}) SELECT {select_sql} FROM scan_records")
    conn.execute("DROP TABLE scan_records")
    conn.execute("ALTER TABLE scan_records_new RENAME TO scan_records")
    return True


def detach_qr_asset_external_foreign_keys(conn: sqlite3.Connection) -> bool:
    """Keep the QR module independent from existing users/tasks/device tables."""
    external_fks = [
        row for table in ("qr_assets", "qr_asset_bindings")
        for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        if row["table"] == "users"
    ]
    if not external_fks:
        return False

    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        BEGIN;
        ALTER TABLE qr_asset_bindings RENAME TO qr_asset_bindings_old;
        ALTER TABLE qr_assets RENAME TO qr_assets_old;

        CREATE TABLE qr_assets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            token               TEXT NOT NULL UNIQUE,
            asset_barcode       TEXT NOT NULL UNIQUE,
            barcode_model       TEXT DEFAULT '',
            room_name           TEXT NOT NULL,
            current_device_name TEXT DEFAULT '',
            current_device_ref  TEXT DEFAULT '',
            created_by          INTEGER,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            is_active           INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE qr_asset_bindings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            qr_asset_id   INTEGER NOT NULL REFERENCES qr_assets(id),
            device_name   TEXT NOT NULL,
            device_ref    TEXT DEFAULT '',
            room_name     TEXT NOT NULL,
            bound_at      TEXT NOT NULL,
            unbound_at    TEXT,
            changed_by    INTEGER,
            change_reason TEXT DEFAULT ''
        );

        INSERT INTO qr_assets
            SELECT id, token, asset_barcode, barcode_model, room_name,
                   current_device_name, current_device_ref, created_by,
                   created_at, updated_at, is_active
            FROM qr_assets_old;
        INSERT INTO qr_asset_bindings
            SELECT id, qr_asset_id, device_name, device_ref, room_name,
                   bound_at, unbound_at, changed_by, change_reason
            FROM qr_asset_bindings_old;

        DROP TABLE qr_asset_bindings_old;
        DROP TABLE qr_assets_old;
        COMMIT;
        PRAGMA foreign_keys=ON;
    """)
    return True


def _backfill_ai_analysis_task_versions(conn: sqlite3.Connection) -> None:
    """用现有成功报告补登记任务当前完成批次；二次完成晚于报告时不登记。"""
    analyses = conn.execute(
        """SELECT id, task_ids_json, created_at
           FROM ai_analysis_results
           WHERE status = 'success'
           ORDER BY created_at DESC, id DESC"""
    ).fetchall()
    for analysis in analyses:
        try:
            task_ids = [int(value) for value in json.loads(analysis["task_ids_json"] or "[]")]
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        for task_id in task_ids:
            task = conn.execute(
                "SELECT status, field_completed_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if not task or task["status"] != "done" or not task["field_completed_at"]:
                continue
            if task["field_completed_at"] > analysis["created_at"]:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO ai_analysis_task_versions
                   (analysis_id, task_id, field_completed_at) VALUES (?, ?, ?)""",
                (analysis["id"], task_id, task["field_completed_at"]),
            )


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
            admin_scope TEXT NOT NULL DEFAULT 'full' CHECK(admin_scope IN ('full','collaborator')),
            area_name   TEXT DEFAULT '汇聚及核心',
            phone       TEXT DEFAULT '',
            employee_no TEXT DEFAULT '',
            is_active   INTEGER NOT NULL DEFAULT 1,
            auth_version INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name   TEXT NOT NULL,
            room_name   TEXT NOT NULL,
            area_name   TEXT DEFAULT '汇聚及核心',
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

        CREATE TABLE IF NOT EXISTS scan_record_corrections (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id           INTEGER NOT NULL REFERENCES scan_records(id),
            actual_model        TEXT DEFAULT '',
            asset_barcodes      TEXT DEFAULT '',
            asset_no_barcode    INTEGER NOT NULL DEFAULT 0,
            cabinet_barcodes    TEXT DEFAULT '',
            cabinet_no_barcode  INTEGER NOT NULL DEFAULT 0,
            rack_no             TEXT DEFAULT '',
            notes               TEXT DEFAULT '',
            record_type         TEXT NOT NULL DEFAULT 'normal'
                                CHECK(record_type IN ('normal', 'missing', 'surplus')),
            missing_reason      TEXT DEFAULT '',
            correction_reason   TEXT NOT NULL,
            correction_note     TEXT DEFAULT '',
            corrected_by        INTEGER REFERENCES users(id),
            corrected_at        TEXT NOT NULL,
            source_scanned_at   TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS cabinet_records (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id             INTEGER NOT NULL REFERENCES tasks(id),
            room_name           TEXT NOT NULL,
            area_name           TEXT DEFAULT '汇聚及核心',
            cabinet_type        TEXT NOT NULL DEFAULT 'equipment'
                                CHECK(cabinet_type IN ('equipment', 'odf', 'iodf')),
            cabinet_barcode     TEXT DEFAULT '',
            no_barcode          INTEGER NOT NULL DEFAULT 0,
            cabinet_name        TEXT DEFAULT '',
            location            TEXT DEFAULT '',
            source              TEXT NOT NULL DEFAULT 'manual',
            notes               TEXT DEFAULT '',
            created_by          INTEGER REFERENCES users(id),
            scanned_at          TEXT NOT NULL,
            client_request_id   TEXT DEFAULT ''
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

        CREATE TABLE IF NOT EXISTS ai_analysis_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_ids_json TEXT NOT NULL,
            analysis_scope TEXT DEFAULT '',
            model_provider TEXT DEFAULT 'deepseek',
            model_name  TEXT DEFAULT '',
            prompt_version TEXT DEFAULT '',
            input_summary TEXT DEFAULT '',
            desensitized_payload TEXT DEFAULT '',
            context_meta TEXT DEFAULT '',
            analysis_result TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'success',
            error_message TEXT DEFAULT '',
            created_by  INTEGER,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_analysis_task_versions (
            analysis_id INTEGER NOT NULL REFERENCES ai_analysis_results(id),
            task_id INTEGER NOT NULL REFERENCES tasks(id),
            field_completed_at TEXT NOT NULL,
            PRIMARY KEY(task_id, field_completed_at)
        );

        CREATE TABLE IF NOT EXISTS ai_adjustment_tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER REFERENCES ai_analysis_results(id),
            source_item_index INTEGER NOT NULL DEFAULT 0,
            source_task_ids_json TEXT DEFAULT '[]',
            source_type TEXT NOT NULL DEFAULT 'ai',
            source_key  TEXT DEFAULT '',
            problem_type TEXT DEFAULT '',
            room_name   TEXT DEFAULT '',
            device_name TEXT DEFAULT '',
            area_name   TEXT DEFAULT '汇聚及核心',
            risk_level  TEXT DEFAULT '',
            basis       TEXT DEFAULT '',
            suggestion  TEXT DEFAULT '',
            priority    TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK(status IN ('pending_review', 'dispatched', 'pending_verify', 'done', 'rejected')),
            assigned_to INTEGER REFERENCES users(id),
            created_by  INTEGER,
            reviewed_by INTEGER,
            review_note TEXT DEFAULT '',
            completed_by INTEGER,
            completion_note TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            reviewed_at TEXT,
            dispatched_at TEXT,
            completed_at TEXT,
            reminder_count INTEGER NOT NULL DEFAULT 0,
            reminded_at TEXT,
            reminder_note TEXT DEFAULT '',
            reminded_by INTEGER REFERENCES users(id),
            verified_by INTEGER REFERENCES users(id),
            verification_note TEXT DEFAULT '',
            verified_at TEXT
        );

        CREATE TABLE IF NOT EXISTS system_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS qr_assets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            token               TEXT NOT NULL UNIQUE,
            asset_barcode       TEXT NOT NULL UNIQUE,
            barcode_model       TEXT DEFAULT '',
            room_name           TEXT NOT NULL,
            current_device_name TEXT DEFAULT '',
            current_device_ref  TEXT DEFAULT '',
            created_by          INTEGER,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            is_active           INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS qr_asset_bindings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            qr_asset_id   INTEGER NOT NULL REFERENCES qr_assets(id),
            device_name   TEXT NOT NULL,
            device_ref    TEXT DEFAULT '',
            room_name     TEXT NOT NULL,
            bound_at      TEXT NOT NULL,
            unbound_at    TEXT,
            changed_by    INTEGER,
            change_reason TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS asset_info_imports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name       TEXT NOT NULL,
            sheet_name      TEXT NOT NULL,
            barcode_column  TEXT NOT NULL,
            headers_json    TEXT NOT NULL,
            display_fields_json TEXT NOT NULL DEFAULT '[]',
            row_count       INTEGER NOT NULL,
            imported_by     INTEGER,
            imported_by_name TEXT DEFAULT '',
            imported_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS asset_info_records (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id        INTEGER NOT NULL REFERENCES asset_info_imports(id),
            barcode_norm    TEXT NOT NULL,
            fields_json     TEXT NOT NULL,
            UNIQUE(batch_id, barcode_norm)
        );

        CREATE TABLE IF NOT EXISTS asset_info_current (
            id              INTEGER PRIMARY KEY CHECK(id = 1),
            batch_id        INTEGER NOT NULL REFERENCES asset_info_imports(id)
        );

    """)
    detach_qr_asset_external_foreign_keys(conn)
    # 兼容已经投入使用的数据库，按列增量升级，不清空历史数据。
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    migrations = {
        "import_batch_id": "ALTER TABLE tasks ADD COLUMN import_batch_id TEXT NOT NULL DEFAULT ''",
        "area_name": "ALTER TABLE tasks ADD COLUMN area_name TEXT DEFAULT '汇聚及核心'",
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
        "superseded_by_task_id": "ALTER TABLE tasks ADD COLUMN superseded_by_task_id INTEGER REFERENCES tasks(id)",
    }
    for column, statement in migrations.items():
        if column not in task_columns:
            conn.execute(statement)
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    asset_import_columns = {row["name"] for row in conn.execute("PRAGMA table_info(asset_info_imports)")}
    if "display_fields_json" not in asset_import_columns:
        conn.execute("ALTER TABLE asset_info_imports ADD COLUMN display_fields_json TEXT NOT NULL DEFAULT '[]'")
    user_migrations = {
        "area_name": "ALTER TABLE users ADD COLUMN area_name TEXT DEFAULT '汇聚及核心'",
        "phone": "ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''",
        "employee_no": "ALTER TABLE users ADD COLUMN employee_no TEXT DEFAULT ''",
        "is_active": "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
        "is_deleted": "ALTER TABLE users ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "deleted_at": "ALTER TABLE users ADD COLUMN deleted_at TEXT",
        "password_hash": "ALTER TABLE users ADD COLUMN password_hash TEXT DEFAULT ''",
        "auth_version": "ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0",
        "admin_scope": "ALTER TABLE users ADD COLUMN admin_scope TEXT NOT NULL DEFAULT 'full' CHECK(admin_scope IN ('full','collaborator'))",
    }
    for column, statement in user_migrations.items():
        if column not in user_columns:
            conn.execute(statement)
    record_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scan_records)")}
    record_migrations = {
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
    analysis_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_analysis_results)")}
    analysis_migrations = {
        "context_meta": "ALTER TABLE ai_analysis_results ADD COLUMN context_meta TEXT DEFAULT ''",
    }
    for column, statement in analysis_migrations.items():
        if column not in analysis_columns:
            conn.execute(statement)
    adjustment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_adjustment_tasks)")}
    adjustment_migrations = {
        "reminder_count": "ALTER TABLE ai_adjustment_tasks ADD COLUMN reminder_count INTEGER NOT NULL DEFAULT 0",
        "reminded_at": "ALTER TABLE ai_adjustment_tasks ADD COLUMN reminded_at TEXT",
        "reminder_note": "ALTER TABLE ai_adjustment_tasks ADD COLUMN reminder_note TEXT DEFAULT ''",
        "reminded_by": "ALTER TABLE ai_adjustment_tasks ADD COLUMN reminded_by INTEGER REFERENCES users(id)",
        "source_type": "ALTER TABLE ai_adjustment_tasks ADD COLUMN source_type TEXT NOT NULL DEFAULT 'ai'",
        "source_key": "ALTER TABLE ai_adjustment_tasks ADD COLUMN source_key TEXT DEFAULT ''",
        "verified_by": "ALTER TABLE ai_adjustment_tasks ADD COLUMN verified_by INTEGER REFERENCES users(id)",
        "verification_note": "ALTER TABLE ai_adjustment_tasks ADD COLUMN verification_note TEXT DEFAULT ''",
        "verified_at": "ALTER TABLE ai_adjustment_tasks ADD COLUMN verified_at TEXT",
    }
    for column, statement in adjustment_migrations.items():
        if column not in adjustment_columns:
            conn.execute(statement)
    adjustment_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ai_adjustment_tasks'"
    ).fetchone()
    adjustment_sql = adjustment_sql_row["sql"] if adjustment_sql_row else ""
    if "pending_verify" not in adjustment_sql or "analysis_id INTEGER NOT NULL" in adjustment_sql:
        conn.executescript("""
            CREATE TABLE ai_adjustment_tasks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER REFERENCES ai_analysis_results(id),
                source_item_index INTEGER NOT NULL DEFAULT 0,
                source_task_ids_json TEXT DEFAULT '[]',
                source_type TEXT NOT NULL DEFAULT 'ai',
                source_key TEXT DEFAULT '',
                problem_type TEXT DEFAULT '', room_name TEXT DEFAULT '', device_name TEXT DEFAULT '',
                area_name TEXT DEFAULT '汇聚及核心', risk_level TEXT DEFAULT '', basis TEXT DEFAULT '',
                suggestion TEXT DEFAULT '', priority TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending_review'
                    CHECK(status IN ('pending_review', 'dispatched', 'pending_verify', 'done', 'rejected')),
                assigned_to INTEGER REFERENCES users(id), created_by INTEGER,
                reviewed_by INTEGER, review_note TEXT DEFAULT '', completed_by INTEGER,
                completion_note TEXT DEFAULT '', created_at TEXT NOT NULL, reviewed_at TEXT,
                dispatched_at TEXT, completed_at TEXT, reminder_count INTEGER NOT NULL DEFAULT 0,
                reminded_at TEXT, reminder_note TEXT DEFAULT '', reminded_by INTEGER REFERENCES users(id),
                verified_by INTEGER REFERENCES users(id), verification_note TEXT DEFAULT '', verified_at TEXT
            );
            INSERT INTO ai_adjustment_tasks_new
                (id, analysis_id, source_item_index, source_task_ids_json, source_type, source_key,
                 problem_type, room_name, device_name, area_name, risk_level, basis, suggestion, priority,
                 status, assigned_to, created_by, reviewed_by, review_note, completed_by, completion_note,
                 created_at, reviewed_at, dispatched_at, completed_at, reminder_count, reminded_at,
                 reminder_note, reminded_by, verified_by, verification_note, verified_at)
            SELECT id, analysis_id, source_item_index, source_task_ids_json, source_type, source_key,
                   problem_type, room_name, device_name, area_name, risk_level, basis, suggestion, priority,
                   status, assigned_to, created_by, reviewed_by, review_note, completed_by, completion_note,
                   created_at, reviewed_at, dispatched_at, completed_at, reminder_count, reminded_at,
                   reminder_note, reminded_by, verified_by, verification_note, verified_at
            FROM ai_adjustment_tasks;
            DROP TABLE ai_adjustment_tasks;
            ALTER TABLE ai_adjustment_tasks_new RENAME TO ai_adjustment_tasks;
        """)
    correction_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scan_record_corrections)")}
    correction_migrations = {
        "record_type": "ALTER TABLE scan_record_corrections ADD COLUMN record_type TEXT NOT NULL DEFAULT 'normal'",
        "missing_reason": "ALTER TABLE scan_record_corrections ADD COLUMN missing_reason TEXT DEFAULT ''",
        "source_scanned_at": "ALTER TABLE scan_record_corrections ADD COLUMN source_scanned_at TEXT DEFAULT ''",
    }
    for column, statement in correction_migrations.items():
        if column not in correction_columns:
            conn.execute(statement)
    removed_legacy_scan_columns = drop_legacy_scan_record_columns(conn)
    for table, source_column in (("scan_records", "inherited_from_record_id"), ("cabinet_records", "inherited_from_cabinet_id")):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in {
            "inherited_from_task_id": "INTEGER REFERENCES tasks(id)",
            source_column: "INTEGER",
            "inherited_at": "TEXT DEFAULT ''",
            "inherited_source_label": "TEXT DEFAULT ''",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    # 作废只退出当前业务读取，保留原始采集和审计记录。
    for table in ("scan_records", "cabinet_records"):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in {
            "is_void": "INTEGER NOT NULL DEFAULT 0",
            "void_reason": "TEXT DEFAULT ''",
            "voided_by": "INTEGER",
            "voided_at": "TEXT DEFAULT ''",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    cabinet_columns = {row["name"] for row in conn.execute("PRAGMA table_info(cabinet_records)")}
    for name, definition in {"updated_by": "INTEGER", "updated_at": "TEXT DEFAULT ''"}.items():
        if name not in cabinet_columns:
            conn.execute(f"ALTER TABLE cabinet_records ADD COLUMN {name} {definition}")
    if "device_name_corrected_at" not in {row["name"] for row in conn.execute("PRAGMA table_info(scan_records)")}:
        conn.execute("ALTER TABLE scan_records ADD COLUMN device_name_corrected_at TEXT DEFAULT ''")
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
        CREATE INDEX IF NOT EXISTS idx_tasks_import_batch
            ON tasks(import_batch_id, is_deleted);
        CREATE INDEX IF NOT EXISTS idx_scan_records_task_status
            ON scan_records(task_id, match_status, record_type);
        CREATE INDEX IF NOT EXISTS idx_scan_records_created_by
            ON scan_records(created_by);
        CREATE INDEX IF NOT EXISTS idx_scan_record_corrections_record
            ON scan_record_corrections(record_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_cabinet_records_task
            ON cabinet_records(task_id, id DESC);
        DROP INDEX IF EXISTS idx_cabinet_records_barcode;
        DROP INDEX IF EXISTS idx_cabinet_records_no_barcode;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cabinet_records_barcode
            ON cabinet_records(task_id, cabinet_barcode)
            WHERE cabinet_barcode != '' AND is_void = 0 AND source = 'manual';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cabinet_records_no_barcode
            ON cabinet_records(task_id, cabinet_type, cabinet_name, location)
            WHERE no_barcode = 1 AND cabinet_barcode = '' AND cabinet_name != '' AND is_void = 0;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cabinet_records_client_request
            ON cabinet_records(created_by, client_request_id)
            WHERE client_request_id != '';
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
        CREATE INDEX IF NOT EXISTS idx_ai_analysis_created
            ON ai_analysis_results(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_analysis_task_versions_analysis
            ON ai_analysis_task_versions(analysis_id);
        CREATE INDEX IF NOT EXISTS idx_ai_adjustment_status
            ON ai_adjustment_tasks(status, area_name, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_adjustment_assignee
            ON ai_adjustment_tasks(assigned_to, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_qr_assets_lookup
            ON qr_assets(is_active, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_qr_asset_bindings_asset
            ON qr_asset_bindings(qr_asset_id, bound_at DESC);
        CREATE INDEX IF NOT EXISTS idx_asset_info_records_lookup
            ON asset_info_records(batch_id, barcode_norm);
    """)
    conn.execute(
        """INSERT OR IGNORE INTO system_settings (key, value, updated_at)
           VALUES ('ai_enabled', '1', ?)""",
        (datetime.now().isoformat(),),
    )
    conn.execute("""
        UPDATE tasks
        SET assigned_at = created_at
        WHERE assigned_to IS NOT NULL AND assigned_at IS NULL
    """)
    # 历史清单没有导入批次依据，逐任务保留，不能猜测或合并为同一轮。
    conn.execute("UPDATE tasks SET import_batch_id = 'legacy-' || id WHERE import_batch_id = ''")
    conn.execute("""
        UPDATE tasks
        SET area_name = '汇聚及核心'
        WHERE area_name IS NULL OR area_name = ''
    """)
    for row in conn.execute("SELECT id, area_name FROM tasks").fetchall():
        normalized_area = normalize_user_area_name(row["area_name"], strict=False)
        if normalized_area != row["area_name"]:
            conn.execute("UPDATE tasks SET area_name = ? WHERE id = ?", (normalized_area, row["id"]))
    conn.execute("""
        UPDATE users
        SET area_name = '汇聚及核心'
        WHERE area_name IS NULL OR area_name = ''
    """)
    for row in conn.execute("SELECT id, area_name FROM users").fetchall():
        normalized_area = normalize_user_area_name(row["area_name"], strict=False)
        if normalized_area != row["area_name"]:
            conn.execute("UPDATE users SET area_name = ? WHERE id = ?", (normalized_area, row["id"]))
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
    _backfill_ai_analysis_task_versions(conn)
    normalize_existing_scan_match_status(conn)
    from .three_way import init_schema
    init_schema(conn)
    from .inventory_pairing import init_schema as init_pairing_schema
    init_pairing_schema(conn)
    conn.commit()
    conn.close()
    if removed_legacy_scan_columns:
        shutil.rmtree(DB_DIR / "evidence", ignore_errors=True)


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
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if current is not None:
            _protect_last_admin(conn, user_id, current["role"], bool(current["is_active"]),
                                current["admin_scope"], password_hash)
        if current is None or current["password_hash"] != password_hash:
            conn.execute(
                """UPDATE users
                   SET password_hash = ?, auth_version = COALESCE(auth_version, 0) + 1
                   WHERE id = ?""",
                (password_hash, user_id),
            )
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


def save_asset_info_snapshot(
    *,
    file_name: str,
    sheet_name: str,
    barcode_column: str,
    headers: list[str],
    records: list[dict[str, Any]],
    imported_by: int | None,
    imported_by_name: str,
) -> dict[str, Any]:
    """保存完整资产信息快照，并在同一事务中切换当前版本。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        from .three_way import asset_values, track_sources
        current_asset = conn.execute("SELECT batch_id FROM asset_info_current WHERE id=1").fetchone()
        previous_values = asset_values(conn, current_asset["batch_id"] if current_asset else None)
        cur = conn.execute(
            """INSERT INTO asset_info_imports
               (file_name, sheet_name, barcode_column, headers_json, display_fields_json, row_count,
                imported_by, imported_by_name, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                file_name,
                sheet_name,
                barcode_column,
                json.dumps(headers, ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                len(records),
                imported_by,
                imported_by_name,
                datetime.now().isoformat(),
            ),
        )
        batch_id = int(cur.lastrowid)
        conn.executemany(
            """INSERT INTO asset_info_records (batch_id, barcode_norm, fields_json)
               VALUES (?, ?, ?)""",
            [
                (batch_id, normalize_barcode(record["barcode"]), json.dumps(record["fields"], ensure_ascii=False))
                for record in records
            ],
        )
        conn.execute(
            """INSERT INTO asset_info_current (id, batch_id) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET batch_id = excluded.batch_id""",
            (batch_id,),
        )
        track_sources(conn, previous_values, asset_values(conn, batch_id))
        conn.commit()
        return get_asset_info_status(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_asset_info_status(conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """返回当前资产信息快照的导入状态。"""
    owns_connection = conn is None
    connection = conn or get_conn()
    try:
        row = connection.execute(
            """SELECT b.id, b.file_name, b.sheet_name, b.barcode_column,
                      b.headers_json, b.display_fields_json, b.row_count, b.imported_by, b.imported_by_name,
                      b.imported_at
               FROM asset_info_current c
               JOIN asset_info_imports b ON b.id = c.batch_id
               WHERE c.id = 1"""
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["headers"] = json.loads(result.pop("headers_json"))
        result["display_fields"] = json.loads(result.pop("display_fields_json") or "[]")
        if not result["display_fields"]:
            result["display_fields"] = [field for field in result["headers"] if field != result["barcode_column"]]
        return result
    finally:
        if owns_connection:
            connection.close()


def list_asset_info_imports(limit: int = 10) -> list[dict[str, Any]]:
    """返回最近资产信息表导入批次。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, file_name, sheet_name, barcode_column, row_count,
                      imported_by_name, imported_at
               FROM asset_info_imports ORDER BY id DESC LIMIT ?""",
            (max(1, min(limit, 50)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def query_asset_info(barcode: str) -> dict[str, Any] | None:
    """按资产条码查询当前快照中的原始字段。"""
    normalized = normalize_barcode(barcode)
    if not normalized:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT r.fields_json, b.id AS batch_id, b.barcode_column, b.imported_at,
                      b.headers_json, b.display_fields_json
               FROM asset_info_current c
               JOIN asset_info_records r ON r.batch_id = c.batch_id
               JOIN asset_info_imports b ON b.id = c.batch_id
               WHERE c.id = 1 AND r.barcode_norm = ?""",
            (normalized,),
        ).fetchone()
        if row is None:
            return None
        fields = json.loads(row["fields_json"])
        headers = json.loads(row["headers_json"] or "[]")
        display_fields = json.loads(row["display_fields_json"] or "[]")
        if not display_fields:
            display_fields = [field for field in headers if field != row["barcode_column"]]
        return {
            "barcode": barcode.strip(),
            "batch_id": row["batch_id"],
            "barcode_column": row["barcode_column"],
            "imported_at": row["imported_at"],
            "fields": {field: fields.get(field, "") for field in display_fields if field in fields},
        }
    finally:
        conn.close()


def set_asset_info_display_fields(fields: list[str]) -> dict[str, Any] | None:
    """保存当前资产信息弹窗需要展示的字段。"""
    conn = get_conn()
    try:
        current = get_asset_info_status(conn)
        if current is None:
            return None
        valid = [field for field in fields if field in current["headers"] and field != current["barcode_column"]]
        conn.execute(
            "UPDATE asset_info_imports SET display_fields_json = ? WHERE id = ?",
            (json.dumps(valid, ensure_ascii=False), current["id"]),
        )
        conn.commit()
        return get_asset_info_status(conn)
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


def save_ai_analysis_result(
    task_ids: list[int],
    analysis_scope: str,
    model_provider: str,
    model_name: str,
    prompt_version: str,
    input_summary: str,
    desensitized_payload: str,
    context_meta: str,
    analysis_result: str,
    status: str,
    error_message: str,
    created_by: int | None,
    task_completion_versions: list[tuple[int, str]] | None = None,
) -> int:
    """保存一次 AI 分析结果。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO ai_analysis_results
               (task_ids_json, analysis_scope, model_provider, model_name, prompt_version,
                input_summary, desensitized_payload, context_meta, analysis_result, status, error_message,
                created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                json.dumps(task_ids, ensure_ascii=False),
                analysis_scope,
                model_provider,
                model_name,
                prompt_version,
                input_summary,
                desensitized_payload,
                context_meta,
                analysis_result,
                status,
                error_message,
                created_by,
                datetime.now().isoformat(),
            ),
        )
        if status == "success" and task_completion_versions:
            try:
                conn.executemany(
                    """INSERT INTO ai_analysis_task_versions
                       (analysis_id, task_id, field_completed_at) VALUES (?, ?, ?)""",
                    [(cur.lastrowid, task_id, completed_at) for task_id, completed_at in task_completion_versions],
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ValueError("选中任务的当前现场完成批次已经生成过 AI 报告") from exc
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_analyzed_ai_task_versions(task_completion_versions: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """查询已经成功生成过报告的任务现场完成批次。"""
    if not task_completion_versions:
        return []
    where_sql = " OR ".join("(v.task_id = ? AND v.field_completed_at = ?)" for _ in task_completion_versions)
    params = tuple(value for version in task_completion_versions for value in version)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT v.task_id, v.field_completed_at, v.analysis_id, r.created_at AS analyzed_at
                FROM ai_analysis_task_versions v
                JOIN ai_analysis_results r ON r.id = v.analysis_id
                WHERE {where_sql}
                ORDER BY v.task_id""",
            params,
        ).fetchall()
        return [dict_from_row(row) for row in rows if row is not None]
    finally:
        conn.close()


def get_ai_analysis_result(result_id: int) -> dict[str, Any] | None:
    """按 ID 读取一次 AI 分析结果。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM ai_analysis_results WHERE id = ?", (result_id,)).fetchone()
        return dict_from_row(row)
    finally:
        conn.close()


def list_ai_analysis_results(task_ids: list[int], page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """分页查询报告概览；机房筛选包括包含该机房的联合分析报告。"""
    ids = sorted(set(task_ids))
    where = ""
    if ids:
        placeholders = ",".join("?" for _ in ids)
        where = f"WHERE EXISTS (SELECT 1 FROM json_each(r.task_ids_json) j WHERE CAST(j.value AS INTEGER) IN ({placeholders}))"
    conn = get_conn()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM ai_analysis_results r {where}", ids).fetchone()[0]
        rows = conn.execute(
            f"""SELECT r.id,r.task_ids_json,r.analysis_scope,r.model_name,r.model_provider,r.status,r.created_at,
                       u.name AS created_by_name FROM ai_analysis_results r
                LEFT JOIN users u ON u.id=r.created_by {where}
                ORDER BY r.created_at DESC,r.id DESC LIMIT ? OFFSET ?""",
            (*ids, page_size, (page - 1) * page_size),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["task_ids"] = json.loads(item.pop("task_ids_json"))
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


def get_latest_ai_analysis_result() -> dict[str, Any] | None:
    """读取最近一次 AI 分析结果。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM ai_analysis_results ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        return dict_from_row(row)
    finally:
        conn.close()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _derive_adjustment_area(room_name: str, tasks: list[dict[str, Any]]) -> str:
    room_text = _text(room_name)
    for task in tasks:
        task_room = _text(task.get("room_name"))
        if task_room and (task_room in room_text or room_text in task_room):
            return normalize_user_area_name(task.get("area_name"), strict=False)
    return normalize_user_area_name(tasks[0].get("area_name"), strict=False) if tasks else USER_AREA_CORE


def _adjustment_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    item = dict_from_row(row)
    if item is None:
        return None
    try:
        item["source_task_ids"] = json.loads(item.pop("source_task_ids_json") or "[]")
    except json.JSONDecodeError:
        item["source_task_ids"] = []
    item["comparison"] = json.loads(item.pop("comparison_json", "") or "null")
    return item


def create_ai_adjustment_drafts(
    analysis_id: int,
    task_ids: list[int],
    analysis_items: list[dict[str, Any]],
    source_tasks: list[dict[str, Any]],
    created_by: int | None,
) -> list[dict[str, Any]]:
    """把 AI 分析清单转成待管理员审核的整改草稿。"""
    conn = get_conn()
    try:
        rows = []
        now = datetime.now().isoformat()
        source_task_ids_json = json.dumps(task_ids, ensure_ascii=False)
        for index, item in enumerate(analysis_items):
            problem_type = _text(item.get("问题类型"))
            suggestion = _text(item.get("整改建议"))
            if not problem_type and not suggestion:
                continue
            room_name = _text(item.get("涉及机房"))
            rows.append((
                analysis_id,
                index,
                source_task_ids_json,
                problem_type,
                room_name,
                _text(item.get("涉及设备")),
                _derive_adjustment_area(room_name, source_tasks),
                _text(item.get("风险等级")),
                _text(item.get("判断依据")),
                suggestion,
                _text(item.get("优先级")),
                created_by,
                now,
            ))
        if not rows:
            return []
        conn.executemany(
            """INSERT INTO ai_adjustment_tasks
               (analysis_id, source_item_index, source_task_ids_json, problem_type, room_name,
                device_name, area_name, risk_level, basis, suggestion, priority, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        task_rows = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name, closer.name AS completed_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               WHERE a.analysis_id = ?
               ORDER BY a.id""",
            (analysis_id,),
        ).fetchall()
        return [_adjustment_from_row(row) for row in task_rows if row is not None]
    finally:
        conn.close()


def list_ai_adjustment_tasks(status: str = "") -> list[dict[str, Any]]:
    """管理员查询 AI 整改草稿和闭环状态。"""
    allowed_status = {"pending_review", "dispatched", "pending_verify", "done", "rejected"}
    where_sql = "1 = 1"
    params: tuple[Any, ...] = ()
    if status:
        if status not in allowed_status:
            raise ValueError("整改任务状态无效")
        where_sql = "a.status = ?"
        params = (status,)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name, closer.name AS completed_name
                FROM ai_adjustment_tasks a
                LEFT JOIN users assignee ON assignee.id = a.assigned_to
                LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
                LEFT JOIN users closer ON closer.id = a.completed_by
                WHERE {where_sql}
                ORDER BY
                  CASE a.status WHEN 'pending_review' THEN 0 WHEN 'dispatched' THEN 1
                       WHEN 'pending_verify' THEN 2 WHEN 'done' THEN 3 ELSE 4 END,
                  a.created_at DESC, a.id DESC""",
            params,
        ).fetchall()
        return [_adjustment_from_row(row) for row in rows if row is not None]
    finally:
        conn.close()


def dispatch_ai_adjustment_task(
    adjustment_id: int,
    assigned_to: int,
    reviewed_by: int,
    review_note: str = "",
) -> dict[str, Any] | None:
    """管理员审核通过后，下发 AI 整改任务给现场人员。"""
    from . import three_way
    if three_way.is_comparison_case(adjustment_id):
        return three_way.case_action(adjustment_id, "approve", reviewed_by, {"needs_field": True, "needs_admin": True, "assigned_to": assigned_to, "note": review_note})
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        adjustment = conn.execute(
            "SELECT * FROM ai_adjustment_tasks WHERE id = ?",
            (adjustment_id,),
        ).fetchone()
        if adjustment is None:
            return None
        if adjustment["status"] != "pending_review":
            raise ValueError("只有待审核整改草稿可以下发")

        worker = conn.execute(
            """SELECT id, area_name FROM users
               WHERE id = ? AND role = 'worker' AND is_active = 1 AND COALESCE(is_deleted, 0) = 0""",
            (assigned_to,),
        ).fetchone()
        if worker is None:
            raise ValueError("请选择有效的现场人员")
        if not user_can_access_area(worker["area_name"], adjustment["area_name"]):
            raise ValueError("现场人员片区与整改任务片区不一致")

        now = datetime.now().isoformat()
        conn.execute(
            """UPDATE ai_adjustment_tasks
               SET status = 'dispatched', assigned_to = ?, reviewed_by = ?, review_note = ?,
                   reviewed_at = ?, dispatched_at = ?
               WHERE id = ?""",
            (assigned_to, reviewed_by, review_note.strip(), now, now, adjustment_id),
        )
        conn.commit()
        row = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name, closer.name AS completed_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               WHERE a.id = ?""",
            (adjustment_id,),
        ).fetchone()
        return _adjustment_from_row(row)
    finally:
        conn.close()


def reject_ai_adjustment_task(adjustment_id: int, reviewed_by: int, review_note: str = "") -> dict[str, Any] | None:
    """管理员驳回 AI 整改草稿。"""
    from . import three_way
    if three_way.is_comparison_case(adjustment_id):
        return three_way.case_action(adjustment_id, "reject_review", reviewed_by, {"note": review_note})
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        adjustment = conn.execute("SELECT status FROM ai_adjustment_tasks WHERE id = ?", (adjustment_id,)).fetchone()
        if adjustment is None:
            return None
        if adjustment["status"] != "pending_review":
            raise ValueError("只有待审核整改草稿可以驳回")
        conn.execute(
            """UPDATE ai_adjustment_tasks
               SET status = 'rejected', reviewed_by = ?, review_note = ?, reviewed_at = ?
               WHERE id = ?""",
            (reviewed_by, review_note.strip(), datetime.now().isoformat(), adjustment_id),
        )
        conn.commit()
        row = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name, closer.name AS completed_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               WHERE a.id = ?""",
            (adjustment_id,),
        ).fetchone()
        return _adjustment_from_row(row)
    finally:
        conn.close()


def remind_ai_adjustment_task(
    adjustment_id: int,
    reminded_by: int,
    reminder_note: str = "",
) -> dict[str, Any] | None:
    """记录管理员在系统内发起的催办，不发送外部通知且不改变任务状态。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        adjustment = conn.execute(
            "SELECT status FROM ai_adjustment_tasks WHERE id = ?",
            (adjustment_id,),
        ).fetchone()
        if adjustment is None:
            return None
        if adjustment["status"] != "dispatched":
            raise ValueError("只有已下发且未完成的整改任务可以记录催办")
        conn.execute(
            """UPDATE ai_adjustment_tasks
               SET reminder_count = COALESCE(reminder_count, 0) + 1,
                   reminded_at = ?, reminder_note = ?, reminded_by = ?
               WHERE id = ?""",
            (datetime.now().isoformat(), reminder_note.strip(), reminded_by, adjustment_id),
        )
        conn.commit()
        row = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name, closer.name AS completed_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               WHERE a.id = ?""",
            (adjustment_id,),
        ).fetchone()
        return _adjustment_from_row(row)
    finally:
        conn.close()


def get_my_adjustment_tasks(user_id: int) -> list[dict[str, Any]]:
    """现场人员查询自己收到的资产整改任务。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name, closer.name AS completed_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               WHERE a.assigned_to = ? AND a.status IN ('dispatched', 'pending_verify', 'done')
               ORDER BY CASE a.status WHEN 'dispatched' THEN 0 WHEN 'pending_verify' THEN 1 ELSE 2 END,
                        a.dispatched_at DESC, a.id DESC""",
            (user_id,),
        ).fetchall()
        return [_adjustment_from_row(row) for row in rows if row is not None]
    finally:
        conn.close()


def complete_ai_adjustment_task(adjustment_id: int, user_id: int, completion_note: str = "") -> dict[str, Any] | None:
    """现场人员提交整改结果，等待管理员验收。"""
    from . import three_way
    if three_way.is_comparison_case(adjustment_id):
        return three_way.case_action(adjustment_id, "field_complete", user_id, {"note": completion_note})
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        adjustment = conn.execute(
            "SELECT status, assigned_to FROM ai_adjustment_tasks WHERE id = ?",
            (adjustment_id,),
        ).fetchone()
        if adjustment is None:
            return None
        if adjustment["assigned_to"] != user_id:
            raise ValueError("只能处理下发给自己的整改任务")
        if adjustment["status"] != "dispatched":
            raise ValueError("只有已下发的整改任务可以确认完成")
        now = datetime.now().isoformat()
        conn.execute(
            """UPDATE ai_adjustment_tasks
               SET status = 'pending_verify', completed_by = ?, completion_note = ?, completed_at = ?
               WHERE id = ?""",
            (user_id, completion_note.strip(), now, adjustment_id),
        )
        conn.commit()
        row = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name, closer.name AS completed_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               WHERE a.id = ?""",
            (adjustment_id,),
        ).fetchone()
        return _adjustment_from_row(row)
    finally:
        conn.close()


def verify_ai_adjustment_task(
    adjustment_id: int,
    verified_by: int,
    verification_note: str = "",
) -> dict[str, Any] | None:
    """管理员验收现场整改结果，验收后才进入最终闭环状态。"""
    from . import three_way
    if three_way.is_comparison_case(adjustment_id):
        return three_way.case_action(adjustment_id, "verify", verified_by, {"note": verification_note})
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        adjustment = conn.execute(
            "SELECT status FROM ai_adjustment_tasks WHERE id = ?",
            (adjustment_id,),
        ).fetchone()
        if adjustment is None:
            return None
        if adjustment["status"] != "pending_verify":
            raise ValueError("只有待管理员验收的整改任务可以闭环")
        now = datetime.now().isoformat()
        conn.execute(
            """UPDATE ai_adjustment_tasks
               SET status = 'done', verified_by = ?, verification_note = ?, verified_at = ?
               WHERE id = ?""",
            (verified_by, verification_note.strip(), now, adjustment_id),
        )
        conn.commit()
        row = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name,
                      closer.name AS completed_name, verifier.name AS verified_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               LEFT JOIN users verifier ON verifier.id = a.verified_by
               WHERE a.id = ?""",
            (adjustment_id,),
        ).fetchone()
        return _adjustment_from_row(row)
    finally:
        conn.close()


def create_rule_adjustment(issue: dict[str, Any], created_by: int, *, source_type: str = "rule") -> dict[str, Any]:
    """管理员把规则问题或人工确认问题加入整改台账。"""
    if source_type not in {"rule", "manual"}:
        raise ValueError("整改来源无效")
    source_key = _text(issue.get("issue_key"))
    if not source_key:
        raise ValueError("核对问题缺少唯一标识")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT id FROM ai_adjustment_tasks
               WHERE source_type = ? AND source_key = ? AND status != 'rejected'""",
            (source_type, source_key),
        ).fetchone()
        if existing:
            raise ValueError(f"该问题已加入整改台账（任务 #{existing['id']}）")
        now = datetime.now().isoformat()
        cursor = conn.execute(
            """INSERT INTO ai_adjustment_tasks
               (analysis_id, source_item_index, source_task_ids_json, source_type, source_key,
                problem_type, room_name, device_name, area_name, risk_level, basis,
                suggestion, priority, created_by, created_at)
               VALUES (NULL, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                json.dumps(issue.get("source_task_ids") or [issue.get("task_id")], ensure_ascii=False),
                source_type,
                source_key,
                _text(issue.get("issue_type")),
                _text(issue.get("room_name")),
                _text(issue.get("device_name")),
                normalize_user_area_name(issue.get("area_name"), strict=False),
                _text(issue.get("risk_level")) or "中",
                _text(issue.get("basis")),
                _text(issue.get("suggestion")),
                _text(issue.get("priority")) or "中",
                created_by,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            """SELECT a.*, assignee.name AS assigned_name, reviewer.name AS reviewed_name,
                      closer.name AS completed_name
               FROM ai_adjustment_tasks a
               LEFT JOIN users assignee ON assignee.id = a.assigned_to
               LEFT JOIN users reviewer ON reviewer.id = a.reviewed_by
               LEFT JOIN users closer ON closer.id = a.completed_by
               WHERE a.id = ?""",
            (cursor.lastrowid,),
        ).fetchone()
        return _adjustment_from_row(row) or {}
    finally:
        conn.close()


def get_system_setting(key: str, default: str = "") -> str:
    """读取系统配置。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_system_setting(key: str, value: str) -> None:
    """写入系统配置。"""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO system_settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value, datetime.now().isoformat()),
        )
        conn.commit()
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

def _protect_last_admin(
    conn: sqlite3.Connection, user_id: int, role: str, is_active: bool,
    admin_scope: str | None = None, password_hash: str | None = None,
) -> None:
    current = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if (current is None or current["role"] != "admin" or current["admin_scope"] != "full"
            or not current["is_active"] or current["is_deleted"] or not current["password_hash"]):
        return
    target_scope = admin_scope if admin_scope is not None else current["admin_scope"]
    target_password = password_hash if password_hash is not None else current["password_hash"]
    if role == "admin" and is_active and target_scope == "full" and target_password:
        return
    other = conn.execute(
        """SELECT id FROM users WHERE id != ? AND role = 'admin' AND admin_scope = 'full' AND is_active = 1
           AND is_deleted = 0 AND COALESCE(password_hash, '') != '' LIMIT 1""", (user_id,),
    ).fetchone()
    if other is None:
        raise ValueError("不能删除、停用、降级或清除最后一个可登录完整管理员的密码")


def create_user(
    wx_openid: str,
    name: str,
    role: str,
    area_name: str = USER_AREA_CORE,
    phone: str = "",
    employee_no: str = "",
    is_active: int = 1,
    *,
    admin_scope: str | None = None,
    password_hash: str | None = None,
    update_existing: bool = True,
) -> dict[str, Any]:
    """创建或更新用户，供默认账号初始化和 PC 账号管理共用。"""
    if not wx_openid.strip() or not name.strip() or role.strip() not in {"admin", "worker"}:
        raise ValueError("登录编码、姓名和角色无效")
    if admin_scope is not None and admin_scope not in ADMIN_SCOPE_CHOICES:
        raise ValueError("管理员权限只能是 full 或 collaborator")
    normalized_area = normalize_user_area_name(area_name)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM users WHERE wx_openid=?", (wx_openid.strip(),)).fetchone()
        if current is not None and not update_existing:
            raise ValueError("登录编码已存在，请编辑已有账号")
        scope = admin_scope if admin_scope is not None else current["admin_scope"] if current else "full"
        effective_hash = password_hash if password_hash is not None else current["password_hash"] if current else ""
        if current:
            _protect_last_admin(conn, current["id"], role.strip(), bool(is_active), scope, effective_hash)
            auth_changed = (current["role"] != role.strip() or current["admin_scope"] != scope
                            or int(current["is_active"] or 0) != int(bool(is_active))
                            or bool(current["is_deleted"]) or current["password_hash"] != effective_hash)
            conn.execute(
                """UPDATE users SET name=?,role=?,area_name=?,phone=?,employee_no=?,is_active=?,admin_scope=?,password_hash=?,
                   is_deleted=0,deleted_at=NULL,auth_version=COALESCE(auth_version,0)+? WHERE id=?""",
                (name.strip(), role.strip(), normalized_area, phone.strip(), employee_no.strip(), int(bool(is_active)),
                 scope, effective_hash, int(auth_changed), current["id"]),
            )
            conn.commit()
            return dict_from_row(conn.execute("SELECT * FROM users WHERE id=?", (current["id"],)).fetchone())
        cur = conn.execute(
            """INSERT INTO users
               (wx_openid, name, role, area_name, phone, employee_no, is_active, is_deleted, deleted_at, admin_scope, password_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)""",
            (
                wx_openid.strip(),
                name.strip(),
                role.strip(),
                normalized_area,
                phone.strip(),
                employee_no.strip(),
                1 if is_active else 0,
                scope,
                effective_hash,
            ),
        )
        conn.commit()
        return dict_from_row(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def get_users() -> list[dict[str, Any]]:
    """查询全部小程序账号，供 PC 后台账号管理使用。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, wx_openid, name, role, admin_scope, area_name, phone, employee_no, is_active
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

def new_import_batch_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _assert_no_open_task_cases(conn: sqlite3.Connection, task_id: int) -> None:
    for row in conn.execute(
        "SELECT id,comparison_json,source_task_ids_json FROM ai_adjustment_tasks WHERE status NOT IN ('done','rejected')"
    ):
        comparison = json.loads(row["comparison_json"] or "{}")
        task_ids = comparison.get("task_ids", []) or json.loads(row["source_task_ids_json"] or "[]")
        if task_id in task_ids:
            raise ValueError(f"任务 #{task_id} 关联未闭环整改单 #{row['id']}，请先处理或审核驳回后再操作")


def _record_manifest_key(record: dict[str, Any]) -> tuple:
    """只有同名且全部清单业务字段未变化，才能承接现场结果。"""
    extra = record.get("device_extra") or {}
    if isinstance(extra, str):
        extra = json.loads(extra)
    extra = {str(k): str(v).strip() for k, v in extra.items() if str(v or "").strip()}
    return (str(record["device_name"]).strip(), str(record.get("expected_model") or "").strip(),
            tuple(sorted(split_codes(record.get("expected_barcode") or ""))),
            json.dumps(extra, ensure_ascii=False, sort_keys=True))


def _inherit_task_capture(conn: sqlite3.Connection, old_id: int, new_id: int) -> dict[str, int]:
    """复制当前有效采集值；新旧任务保留独立记录和明确的继承来源。"""
    counts = dict(inherited_count=0, inherited_rack_count=0, inherited_surplus_count=0, surplus_conflict_count=0)
    now = datetime.now().isoformat()
    old_rows = [dict(r) for r in conn.execute("SELECT * FROM scan_records WHERE task_id=? AND is_void=0 ORDER BY id", (old_id,))]
    new_rows = [dict(r) for r in conn.execute("SELECT * FROM scan_records WHERE task_id=? AND is_void=0 ORDER BY id", (new_id,))]
    old_by_name: dict[str, list[dict]] = {}
    new_by_name: dict[str, list[dict]] = {}
    for rows, lookup in ((old_rows, old_by_name), (new_rows, new_by_name)):
        for row in rows:
            if row["record_type"] != "surplus":
                lookup.setdefault(row["device_name"].strip(), []).append(row)
    fields = (*CORRECTABLE_RECORD_FIELDS, "field_status", "match_status", "scanned_at", "created_by")
    for raw in old_rows:
        effective = add_compare_status(apply_latest_record_correction(conn, dict(raw)))
        if effective["match_status"] == "未扫":
            continue
        name = effective["device_name"].strip()
        if raw["record_type"] == "surplus":
            # 旧盘盈已进入新清单时必须重新核实，不能同时算作两台设备。
            if name in new_by_name:
                counts["surplus_conflict_count"] += 1
                continue
            if conn.execute(
                "SELECT id FROM scan_records WHERE task_id=? AND inherited_from_task_id=? AND inherited_from_record_id=? AND is_void=0",
                (new_id, old_id, raw["id"]),
            ).fetchone():
                continue
            columns = ("task_id", "device_name", "device_extra", "expected_model", "expected_barcode", *fields,
                       "inherited_from_task_id", "inherited_from_record_id", "inherited_at", "inherited_source_label")
            values = (new_id, effective["device_name"], raw["device_extra"], "", "",
                      *(effective.get(f) for f in fields), old_id, raw["id"], now,
                      "管理员修正" if effective.get("has_admin_correction") else raw.get("inherited_source_label") or "现场采集")
            conn.execute(f"INSERT INTO scan_records ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
            counts["inherited_surplus_count"] += 1
            new_rows.append(effective)
            continue
        matches = new_by_name.get(name, [])
        if len(old_by_name.get(name, [])) != 1 or len(matches) != 1:
            continue
        target = matches[0]
        if target["match_status"] != "未扫" or _record_manifest_key(raw) != _record_manifest_key(target):
            continue
        conn.execute(
            f"UPDATE scan_records SET {','.join(f'{f}=?' for f in fields)},inherited_from_task_id=?,inherited_from_record_id=?,inherited_at=?,inherited_source_label=? WHERE id=?",
            (*(effective.get(f) for f in fields), old_id, raw["id"], now,
             "管理员修正" if effective.get("has_admin_correction") else raw.get("inherited_source_label") or "现场采集", target["id"]),
        )
        target["match_status"] = effective["match_status"]
        counts["inherited_count"] += 1
    target_task = conn.execute("SELECT * FROM tasks WHERE id=?", (new_id,)).fetchone()
    for row in conn.execute("SELECT * FROM cabinet_records WHERE task_id=? AND is_void=0 AND cabinet_type IN ('odf','iodf') ORDER BY id", (old_id,)):
        fields = ("cabinet_type", "cabinet_barcode", "no_barcode", "cabinet_name", "location", "source", "notes", "created_by", "scanned_at", "updated_by", "updated_at")
        existing = conn.execute(
            "SELECT * FROM cabinet_records WHERE task_id=? AND is_void=0 AND cabinet_type=? AND cabinet_name=? AND location=? AND cabinet_barcode=? AND no_barcode=?",
            (new_id, row["cabinet_type"], row["cabinet_name"], row["location"], row["cabinet_barcode"], row["no_barcode"]),
        ).fetchone()
        if existing:
            continue
        columns = ("task_id", "room_name", "area_name", *fields, "inherited_from_task_id", "inherited_from_cabinet_id", "inherited_at", "inherited_source_label")
        conn.execute(f"INSERT INTO cabinet_records ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                     (new_id, target_task["room_name"], target_task["area_name"], *(row[f] for f in fields), old_id, row["id"], now,
                      "管理员修正" if row["updated_at"] else row["inherited_source_label"] or "现场登记"))
        counts["inherited_rack_count"] += 1
    conn.execute(
        """INSERT INTO audit_logs(actor_name,actor_role,action,target_type,target_id,detail_json,created_at)
           VALUES('系统','system','task.inherit','task',?,?,?)""",
        (new_id, json.dumps(dict(source_task_id=old_id, **counts), ensure_ascii=False), now),
    )
    return counts


def import_room_tasks(rooms: dict[str, list[dict[str, Any]]]) -> tuple[str, list[dict[str, Any]]]:
    """整份清单在同一事务中导入，所有机房共享本次批次号。"""
    batch_id = new_import_batch_id()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        from .three_way import validate_room_codes
        known_codes = {name: devices[0].get("room_code", "") for name, devices in rooms.items() if devices[0].get("room_code")}
        validate_room_codes(conn, known_codes)
        created = []
        for room_name, devices in rooms.items():
            code = str(devices[0].get("room_code") or "").strip()
            if not code:
                raise ValueError("地点编码不能为空，无法识别同机房最新任务")
            previous = conn.execute(
                """SELECT * FROM tasks WHERE is_deleted=0 AND status!='done'
                   AND (room_code=? OR (room_name=? AND COALESCE(TRIM(room_code),'')=''))
                   ORDER BY id DESC""", (code, room_name),
            ).fetchall()
            if sum(old["status"] == "doing" for old in previous) > 1:
                raise ValueError(f"{room_name}存在多份已领取未完成任务，请先释放多余任务，只保留一份进行中任务后再导入")
            capture_source_id = next((old["id"] for old in previous if old["status"] == "doing"), previous[0]["id"] if previous else None)
            for old in previous:
                _assert_no_open_task_cases(conn, old["id"])
            area_name = normalize_user_area_name(devices[0]["_area_name"])
            task_name = f"{room_name}-盘点"
            cursor = conn.execute(
                """INSERT INTO tasks (task_name, room_name, area_name, created_at, import_batch_id, room_code)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_name, room_name, area_name, datetime.now().isoformat(), batch_id, devices[0].get("room_code", "")),
            )
            task_id = cursor.lastrowid
            conn.executemany(
                """INSERT INTO scan_records (task_id, device_name, device_extra, expected_model, expected_barcode)
                   VALUES (?, ?, ?, ?, ?)""",
                [(task_id, dev["device_name"], json.dumps(dev.get("device_extra", {}), ensure_ascii=False),
                  dev["expected_model"], dev.get("expected_barcode", "")) for dev in devices],
            )
            counts = dict(inherited_count=0, inherited_rack_count=0, inherited_surplus_count=0, surplus_conflict_count=0)
            replaced = []
            waiting = None
            for old in previous:
                conn.execute("UPDATE tasks SET superseded_by_task_id=? WHERE id=?", (task_id, old["id"]))
                if old["status"] == "doing":
                    waiting = old["id"]
                    continue
                if old["id"] == capture_source_id:
                    inherited = _inherit_task_capture(conn, old["id"], task_id)
                    for key, value in inherited.items():
                        counts[key] += value
                conn.execute("UPDATE tasks SET is_deleted=1,deleted_at=?,deleted_reason=? WHERE id=?",
                             (datetime.now().isoformat(), f"同机房新清单替代，最新任务 #{task_id}", old["id"]))
                replaced.append(old["id"])
            created.append({"task_id": task_id, "task_name": task_name, "room_name": room_name,
                            "area_name": area_name, "device_count": len(devices), "import_batch_id": batch_id,
                            "replaced_task_ids": replaced, "waiting_for_task_id": waiting, **counts})
        conn.commit()
        return batch_id, created
    finally:
        conn.close()


def create_task(task_name: str, room_name: str, assigned_to: int | None, area_name: str = USER_AREA_CORE) -> int:
    """创建盘点任务，返回新任务ID"""
    normalized_area = normalize_user_area_name(area_name)
    conn = get_conn()
    try:
        now = datetime.now().isoformat()
        status = "doing" if assigned_to is not None else "pending"
        cur = conn.execute(
            """INSERT INTO tasks
               (task_name, room_name, area_name, assigned_to, claimed_by, status, created_at, assigned_at, claimed_at, import_batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_name,
                room_name,
                normalized_area,
                assigned_to,
                assigned_to,
                status,
                now,
                now if assigned_to is not None else None,
                now if assigned_to is not None else None,
                new_import_batch_id(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_user(user_id: int, data: dict[str, Any], *, password_hash: str | None = None) -> dict[str, Any] | None:
    """修改小程序账号信息。只更新 PC 账号管理允许维护的字段。"""
    allowed_fields = ["wx_openid", "name", "role", "admin_scope", "area_name", "phone", "employee_no", "is_active"]
    update_data = {field: data[field] for field in allowed_fields if field in data}
    if any(field in update_data and update_data[field] is None for field in ("wx_openid", "name", "role", "admin_scope", "area_name", "is_active")):
        raise ValueError("登录编码、姓名、角色、片区和启用状态不能为 null")
    for field in ("phone", "employee_no"):
        if field in update_data and update_data[field] is None:
            update_data[field] = ""
    if not update_data and password_hash is None:
        return get_user_by_id(user_id)

    for text_field in ["wx_openid", "name", "role", "area_name", "phone", "employee_no"]:
        if text_field in update_data and update_data[text_field] is not None:
            update_data[text_field] = str(update_data[text_field]).strip()
    if "area_name" in update_data:
        update_data["area_name"] = normalize_user_area_name(update_data["area_name"])
    if "admin_scope" in update_data and update_data["admin_scope"] not in ADMIN_SCOPE_CHOICES:
        raise ValueError("管理员权限只能是 full 或 collaborator")
    if "is_active" in update_data:
        update_data["is_active"] = 1 if update_data["is_active"] else 0
    if password_hash is not None:
        update_data["password_hash"] = password_hash

    set_sql = ", ".join(f"{field} = ?" for field in update_data)
    params = list(update_data.values()) + [user_id]
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT role, admin_scope, is_active, wx_openid, password_hash FROM users WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
            (user_id,),
        ).fetchone()
        if current is None:
            return None
        _protect_last_admin(conn, user_id, update_data.get("role", current["role"]),
                            bool(update_data.get("is_active", current["is_active"])),
                            update_data.get("admin_scope", current["admin_scope"]), password_hash)
        auth_changed = any(
            field in update_data
            and str(update_data[field] or "") != str(current[field] or "")
            for field in ("role", "admin_scope", "wx_openid", "password_hash")
        )
        if "is_active" in update_data:
            auth_changed = auth_changed or int(update_data["is_active"]) != int(current["is_active"] or 0)
        try:
            cur = conn.execute(
                f"""UPDATE users
                    SET {set_sql},
                        auth_version = COALESCE(auth_version, 0) + ?
                    WHERE id = ? AND COALESCE(is_deleted, 0) = 0""",
                [*params[:-1], 1 if auth_changed else 0, user_id],
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
        conn.execute("BEGIN IMMEDIATE")
        _protect_last_admin(conn, user_id, "worker", False)
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


def _assign_task(conn: sqlite3.Connection, task_id: int, assigned_to: int) -> bool:
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND is_deleted = 0", (task_id,)).fetchone()
    if task is None:
        return False
    _require_task_current(conn, task)
    if task["status"] != "pending" or task["claimed_by"] is not None:
        raise ValueError("只能下发待领取任务；已完成任务保持锁定")
    worker = conn.execute(
        "SELECT * FROM users WHERE id = ? AND role = 'worker' AND is_active = 1 AND is_deleted = 0",
        (assigned_to,),
    ).fetchone()
    if worker is None or not user_can_access_area(worker["area_name"], task["area_name"]):
        raise ValueError("请选择任务所属片区的有效现场人员")
    now = datetime.now().isoformat()
    conn.execute(
        """UPDATE tasks SET assigned_to = ?, claimed_by = ?, assigned_at = ?, claimed_at = ?, status = 'doing'
           WHERE id = ?""", (assigned_to, assigned_to, now, now, task_id),
    )
    return True


def assign_task(task_id: int, assigned_to: int) -> bool:
    """给待领取任务指定同片区现场人员。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        updated = _assign_task(conn, task_id, assigned_to)
        conn.commit()
        return updated
    finally:
        conn.close()


def batch_assign_tasks(assignments: list[dict[str, int]]) -> int:
    """批量下发任务，返回实际更新的任务数量。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        count = 0
        for item in assignments:
            if not _assign_task(conn, item["task_id"], item["assigned_to"]):
                raise ValueError(f"任务 #{item['task_id']} 不存在")
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def delete_task(task_id: int, reason: str) -> bool:
    """作废任务并保留历史数据，默认业务查询不再显示。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND is_deleted = 0", (task_id,)
        ).fetchone()
        if task is None:
            return False
        if task["superseded_by_task_id"] or conn.execute(
            "SELECT id FROM tasks WHERE superseded_by_task_id=? AND is_deleted=0 AND status='doing'", (task_id,)
        ).fetchone():
            raise ValueError("该机房正在更新任务清单，请原领取人先释放旧任务并完成结果沿用后再作废")
        _assert_no_open_task_cases(conn, task_id)
        if conn.execute("""SELECT p.id FROM inventory_pairs p JOIN scan_records r
                           ON r.id IN (p.missing_record_id,p.surplus_record_id)
                           WHERE p.status='active' AND r.task_id=? LIMIT 1""", (task_id,)).fetchone():
            raise ValueError("任务关联拼销记录，请先撤销拼销后再作废任务")
        conn.execute(
            "UPDATE tasks SET is_deleted = 1, deleted_at = ?, deleted_reason = ? WHERE id = ?",
            (datetime.now().isoformat(), reason.strip(), task_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _task_stat_select(where_sql: str) -> str:
    """任务列表通用查询片段，避免多处统计口径不一致。"""
    return f"""
            SELECT
                t.id, t.task_name, t.room_name, t.area_name, t.status, t.created_at,
                t.assigned_to, t.assigned_at, t.claimed_by, t.claimed_at,
                t.completed_at, t.field_completed_at, t.closed_at, t.released_at, t.release_reason,
                t.import_batch_id, t.room_code,
                t.superseded_by_task_id,
                (SELECT MIN(prior.id) FROM tasks prior
                 WHERE prior.superseded_by_task_id=t.id AND prior.is_deleted=0 AND prior.status='doing') AS waiting_for_task_id,
                assigned_user.name AS assigned_name,
                claimed_user.name AS claimed_name,
                (SELECT COUNT(*) FROM cabinet_records c WHERE c.task_id = t.id AND c.is_void=0) AS cabinet_count,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' THEN 1 ELSE 0 END) AS total,
                SUM(CASE WHEN sr.id IS NOT NULL AND COALESCE(sr.record_type, 'normal') != 'surplus' AND sr.match_status != '未扫' THEN 1 ELSE 0 END) AS scanned,
                SUM(CASE WHEN sr.match_status = '不一致' THEN 1 ELSE 0 END) AS mismatch
            FROM tasks t
            LEFT JOIN scan_records sr ON sr.task_id = t.id AND sr.is_void=0
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
        placeholders = ",".join("?" for _ in user_areas)
        where_sql = f"t.area_name IN ({placeholders})"
        params = tuple(user_areas)
        rows = conn.execute(
            _task_stat_select(where_sql) + " ORDER BY CASE t.status WHEN 'pending' THEN 0 WHEN 'doing' THEN 1 ELSE 2 END, t.area_name, t.created_at DESC",
            params,
        ).fetchall()
        return [add_task_field_status(dict_from_row(r)) for r in rows]
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
        return [add_task_field_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


def claim_task(task_id: int, user_id: int) -> tuple[bool, str]:
    """现场人员领取片区任务；用条件更新保证多人同时领取时只有一人成功。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT id, role, area_name, is_active FROM users WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
            (user_id,),
        ).fetchone()
        if user is None:
            return False, "用户不存在"
        if user["role"] != "worker" or not user["is_active"]:
            return False, "当前账号不能领取现场任务"
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND is_deleted=0", (task_id,)).fetchone()
        if task is None:
            return False, "任务不存在或已被最新清单替代，请刷新任务列表"
        try:
            _require_task_current(conn, task)
        except ValueError as exc:
            return False, str(exc)

        now = datetime.now().isoformat()
        user_areas = split_area_names(user["area_name"])
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


def release_task(task_id: int, user_id: int, reason: str = "", *, details: dict | None = None) -> tuple[bool, str]:
    """现场人员释放已领取任务，任务回到片区待领取池。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = datetime.now().isoformat()
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND is_deleted=0", (task_id,)).fetchone()
        if task and task["status"] == "doing" and task["claimed_by"] == user_id and task["superseded_by_task_id"]:
            _assert_no_open_task_cases(conn, task_id)
            replacement_id = task["superseded_by_task_id"]
            replacement = conn.execute("SELECT * FROM tasks WHERE id=? AND is_deleted=0", (replacement_id,)).fetchone()
            if replacement is None or replacement["status"] != "pending":
                raise ValueError("最新任务已变更，请刷新任务列表或联系管理员处理")
            counts = _inherit_task_capture(conn, task_id, replacement_id)
            conn.execute(
                "UPDATE tasks SET is_deleted=1,deleted_at=?,deleted_reason=?,released_at=?,release_reason=? WHERE id=?",
                (now, f"现场释放后由最新任务 #{replacement_id} 替代", now, reason.strip(), task_id),
            )
            if details is not None:
                details.update(replacement_task_id=replacement_id, **counts)
            conn.commit()
            return True, "旧任务已释放，请领取最新任务"
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
            if details is not None:
                details["replacement_task_id"] = None
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
        return add_task_field_status(dict_from_row(row)) if row else None
    finally:
        conn.close()


def get_all_tasks() -> list[dict[str, Any]]:
    """查询所有任务（管理员看板用）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            _task_stat_select("1 = 1") + " ORDER BY t.created_at DESC"
        ).fetchall()
        return [add_task_field_status(dict_from_row(r)) for r in rows]
    finally:
        conn.close()


def require_task_writable(
    conn: sqlite3.Connection, task_id: int, user_id: int | None,
) -> sqlite3.Row:
    """在写事务内重验任务状态和归属，防止作废、释放或完成与提交并发。"""
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND is_deleted = 0", (task_id,)).fetchone()
    if task is None:
        raise ValueError("任务不存在或已作废")
    _require_task_current(conn, task)
    if task["status"] != "doing":
        raise ValueError("任务尚未领取或已完成锁定；有误数据请由管理员修正，严重问题转整改")
    if user_id is not None:
        actor = conn.execute("SELECT * FROM users WHERE id = ? AND is_active = 1 AND is_deleted = 0", (user_id,)).fetchone()
        if actor is None:
            raise ValueError("账号已停用或不存在")
        if actor["role"] != "admin" and (
            task["claimed_by"] != user_id or not user_can_access_area(actor["area_name"], task["area_name"])
        ):
            raise ValueError("只能操作本人所属片区已领取的任务")
    return task


def _require_task_current(conn: sqlite3.Connection, task: sqlite3.Row) -> None:
    if task["superseded_by_task_id"]:
        raise ValueError("该机房有最新任务，请释放当前任务并领取最新任务")
    _require_task_not_waiting(conn, task["id"])


def _require_task_not_waiting(conn: sqlite3.Connection, task_id: int) -> None:
    waiting = conn.execute(
        "SELECT id FROM tasks WHERE superseded_by_task_id=? AND is_deleted=0 AND status='doing' LIMIT 1", (task_id,)
    ).fetchone()
    if waiting:
        raise ValueError(f"请等待原任务 #{waiting['id']} 的领取人释放当前任务后，再领取最新任务")


def complete_inventory_task(task_id: int, user_id: int) -> dict[str, Any]:
    """设备全处理后由领取人确认完成；锁定和审计在同一事务中提交。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT * FROM tasks WHERE id = ? AND is_deleted = 0", (task_id,)).fetchone()
        worker = conn.execute(
            "SELECT * FROM users WHERE id = ? AND role = 'worker' AND is_active = 1 AND is_deleted = 0",
            (user_id,),
        ).fetchone()
        if task is None:
            raise ValueError("任务不存在或已作废")
        if worker is None or task["claimed_by"] != user_id or not user_can_access_area(worker["area_name"], task["area_name"]):
            raise ValueError("只能确认本人所属片区已领取的任务")
        if task["status"] == "done":
            return {"already_completed": True, "field_completed_at": task["field_completed_at"]}
        require_task_writable(conn, task_id, user_id)
        counts = conn.execute(
            """SELECT COUNT(*) AS total, SUM(match_status = '未扫') AS pending
               FROM scan_records WHERE task_id = ? AND is_void=0 AND COALESCE(record_type, 'normal') != 'surplus'""",
            (task_id,),
        ).fetchone()
        if not counts["total"] or counts["pending"]:
            raise ValueError("清单设备尚未全部处理，不能确认完成")
        now = datetime.now().isoformat()
        conn.execute("UPDATE tasks SET status = 'done', completed_at = ?, field_completed_at = ? WHERE id = ?", (now, now, task_id))
        conn.execute(
            """INSERT INTO audit_logs (actor_id, actor_name, actor_role, action, target_type, target_id, detail_json, created_at)
               VALUES (?, ?, 'worker', 'task.complete', 'task', ?, ?, ?)""",
            (user_id, worker["name"], task_id, json.dumps({"import_batch_id": task["import_batch_id"], "device_count": counts["total"]}, ensure_ascii=False), now),
        )
        conn.commit()
        return {"already_completed": False, "field_completed_at": now}
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


def _cabinet_type(value: str) -> str:
    """统一机柜类型，避免前后端传入大小写造成重复类型。"""
    normalized = (value or "").strip().lower()
    if normalized in {"equipment", "设备", "设备机柜", "传输综合柜"}:
        return "equipment"
    if normalized in {"odf"}:
        return "odf"
    if normalized in {"iodf"}:
        return "iodf"
    raise ValueError("机柜类型只能是传输综合柜、ODF 或 IODF")


def _cabinet_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    cabinet = dict_from_row(row)
    if cabinet is None:
        return None
    # 手工记录将机架编号写入兼容字段，自动采集记录仍从位置字段读取。
    cabinet["rack_no"] = (
        _text(cabinet.get("location"))
        if cabinet.get("source") == "device_scan"
        else _text(cabinet.get("cabinet_name")) or _text(cabinet.get("location"))
    )
    cabinet["cabinet_type_label"] = CABINET_TYPE_LABELS.get(cabinet.get("cabinet_type"), cabinet.get("cabinet_type", ""))
    cabinet["source_label"] = CABINET_SOURCE_LABELS.get(cabinet.get("source"), cabinet.get("source", ""))
    cabinet["has_admin_correction"] = bool(cabinet.get("updated_at"))
    cabinet["corrected_at"] = cabinet.get("updated_at") or ""
    if cabinet["has_admin_correction"]:
        cabinet["source_label"] = "管理员补录" if cabinet.get("updated_at") == cabinet.get("scanned_at") else "现场登记（管理员修正）"
    if cabinet.get("inherited_source_label") == "管理员修正":
        cabinet["source_label"] = "管理员修正"
    cabinet["no_barcode"] = bool(cabinet.get("no_barcode"))
    return cabinet


def _insert_cabinet(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    room_name: str,
    area_name: str,
    cabinet_type: str,
    cabinet_barcode: str,
    no_barcode: bool,
    cabinet_name: str,
    location: str,
    source: str,
    notes: str,
    created_by: int | None,
    client_request_id: str = "",
) -> tuple[dict[str, Any], bool]:
    """写入一条机柜发现记录；唯一键冲突时返回已有记录。"""
    normalized_type = _cabinet_type(cabinet_type)
    normalized_barcode = normalize_barcode(cabinet_barcode)
    normalized_name = _text(cabinet_name)
    normalized_location = _text(location)
    request_id = _text(client_request_id)
    if no_barcode:
        normalized_barcode = ""
        if not normalized_name:
            raise ValueError("无条码机柜必须填写机柜名称或编号")
    elif not normalized_barcode:
        raise ValueError("请填写机柜条码或勾选无条码")

    # 设备机柜原始缓存只为兼容历史保留，身份校验使用有效设备反馈。
    if normalized_type == "equipment":
        existing = conn.execute(
            """SELECT c.*, u.name AS created_by_name FROM cabinet_records c
               LEFT JOIN users u ON u.id=c.created_by
               WHERE c.task_id=? AND c.cabinet_type='equipment' AND c.is_void=0
                 AND c.cabinet_barcode=? AND (? != '' OR (c.no_barcode=1 AND c.location=?)) LIMIT 1""",
            (task_id, normalized_barcode, normalized_barcode, normalized_location),
        ).fetchone()
        if existing:
            return _cabinet_from_row(existing) or {}, False

    values = (
        task_id,
        _text(room_name),
        normalize_user_area_name(area_name, strict=False),
        normalized_type,
        normalized_barcode,
        1 if no_barcode else 0,
        normalized_name,
        normalized_location,
        _text(source) or "manual",
        _text(notes),
        created_by,
        datetime.now().isoformat(),
        request_id,
    )
    cursor = conn.execute(
        """INSERT OR IGNORE INTO cabinet_records
           (task_id, room_name, area_name, cabinet_type, cabinet_barcode, no_barcode,
            cabinet_name, location, source, notes, created_by, scanned_at, client_request_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )
    if cursor.rowcount:
        row = conn.execute(
            """SELECT c.*, u.name AS created_by_name
               FROM cabinet_records c
               LEFT JOIN users u ON u.id = c.created_by
               WHERE c.id = ?""",
            (cursor.lastrowid,),
        ).fetchone()
        return _cabinet_from_row(row) or {}, True

    if created_by is not None and request_id:
        row = conn.execute(
            """SELECT c.*, u.name AS created_by_name
               FROM cabinet_records c
               LEFT JOIN users u ON u.id = c.created_by
               WHERE c.created_by = ? AND c.client_request_id = ?""",
            (created_by, request_id),
        ).fetchone()
        if row:
            return _cabinet_from_row(row) or {}, False

    if normalized_barcode:
        row = conn.execute(
            """SELECT c.*, u.name AS created_by_name
               FROM cabinet_records c
               LEFT JOIN users u ON u.id = c.created_by
               WHERE c.task_id = ? AND c.cabinet_barcode = ? AND c.is_void=0 AND c.source='manual'""",
            (task_id, normalized_barcode),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT c.*, u.name AS created_by_name
               FROM cabinet_records c
               LEFT JOIN users u ON u.id = c.created_by
               WHERE c.task_id = ? AND c.cabinet_type = ? AND c.cabinet_name = ?
                 AND c.location = ? AND c.no_barcode = 1 AND c.is_void=0""",
            (task_id, normalized_type, normalized_name, normalized_location),
        ).fetchone()
    if row is None:
        raise ValueError("机柜登记失败，请稍后重试")
    return _cabinet_from_row(row) or {}, False


def _register_scan_cabinets(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    room_name: str,
    area_name: str,
    cabinet_barcodes: str,
    cabinet_no_barcode: bool,
    rack_no: str,
    created_by: int | None,
) -> list[dict[str, Any]]:
    """设备盘点提交时自动登记现场发现的设备机柜。"""
    result = []
    for barcode in sorted(split_codes(cabinet_barcodes)):
        cabinet, _ = _insert_cabinet(
            conn,
            task_id=task_id,
            room_name=room_name,
            area_name=area_name,
            cabinet_type="equipment",
            cabinet_barcode=barcode,
            no_barcode=False,
            cabinet_name="",
            location=rack_no,
            source="device_scan",
            notes="由设备盘点自动登记",
            created_by=created_by,
        )
        result.append(cabinet)
    if cabinet_no_barcode:
        cabinet, _ = _insert_cabinet(
            conn,
            task_id=task_id,
            room_name=room_name,
            area_name=area_name,
            cabinet_type="equipment",
            cabinet_barcode="",
            no_barcode=True,
            cabinet_name=f"传输综合柜-{_text(rack_no)}",
            location=rack_no,
            source="device_scan",
            notes="设备盘点记录无传输综合柜条码",
            created_by=created_by,
        )
        result.append(cabinet)
    return result


class CaptureConflictError(ValueError):
    """同一机架条码的身份冲突，不能当成重复提交吞掉。"""


def _effective_capture_records(conn: sqlite3.Connection, task_id: int) -> list[dict[str, Any]]:
    records = [apply_latest_record_correction(conn, dict(row)) for row in conn.execute(
        "SELECT * FROM scan_records WHERE task_id=? AND is_void=0", (task_id,)
    )]
    return [r for r in records if r.get("scanned_at") and r.get("record_type") != "missing"]


def _check_rack_identity(conn: sqlite3.Connection, task_id: int, cabinet_type: str, barcode: str, rack_no: str, exclude_id: int | None = None) -> None:
    if not barcode:
        return
    for record in _effective_capture_records(conn, task_id):
        if not record.get("cabinet_no_barcode") and barcode in split_codes(record.get("cabinet_barcodes") or ""):
            raise CaptureConflictError(
                f"条码 {barcode} 冲突：已在设备“{record['device_name']}”登记为传输综合柜，柜位 {record.get('rack_no') or '未填'}；本次登记为 {cabinet_type.upper()} 机架 {rack_no}。请核实后更正。"
            )
    old = conn.execute(
        """SELECT * FROM cabinet_records WHERE task_id=? AND cabinet_barcode=?
           AND is_void=0 AND cabinet_type IN ('odf','iodf') AND id!=? LIMIT 1""",
        (task_id, barcode, exclude_id or -1),
    ).fetchone()
    if old and (old["cabinet_type"] != cabinet_type or _text(old["cabinet_name"]) != rack_no):
        raise CaptureConflictError(
            f"条码 {barcode} 冲突：已登记为 {old['cabinet_type'].upper()} 机架 {old['cabinet_name']}；本次登记为 {cabinet_type.upper()} 机架 {rack_no}。请核实后更正。"
        )


def get_collection_warnings(task_id: int, record_id: int | None = None, cabinet_id: int | None = None) -> list[str]:
    """按最新有效反馈检测采集冲突，提醒核实但保留真实现场异常。"""
    conn = get_conn()
    try:
        records = _effective_capture_records(conn, task_id)
        objects = []
        for record in records:
            label = f"设备“{record['device_name']}”（柜位 {record.get('rack_no') or '未填'}）"
            identity = ("record", record["id"])
            if not record.get("asset_no_barcode"):
                objects.extend((code, "device", label, "", identity) for code in split_codes(record.get("asset_barcodes") or ""))
            if not record.get("cabinet_no_barcode"):
                positions = {part.strip() for part in re.split(r"[,，、;；\r\n]+", _text(record.get("rack_no"))) if part.strip()}
                # 多柜位反馈没有条码与位置的一一映射，不能据此判断单柜身份冲突。
                position = next(iter(positions)) if len(positions) == 1 else ""
                objects.extend((code, "equipment", f"{label}的传输综合柜", position, identity) for code in split_codes(record.get("cabinet_barcodes") or ""))
        for rack in conn.execute("SELECT * FROM cabinet_records WHERE task_id=? AND is_void=0 AND cabinet_type IN ('odf','iodf')", (task_id,)):
            if not rack["no_barcode"] and rack["cabinet_barcode"]:
                objects.append((rack["cabinet_barcode"], "rack", f"{rack['cabinet_type'].upper()} 机架“{rack['cabinet_name']}”", "", ("rack", rack["id"])))
        focus = ("record", record_id) if record_id else (("rack", cabinet_id) if cabinet_id else None)
        by_barcode, by_position = {}, {}
        for index, item in enumerate(objects):
            by_barcode.setdefault(item[0], []).append(index)
            if item[1] == "equipment" and item[3]:
                by_position.setdefault(item[3], []).append(index)
        pairs = set()
        for index, item in enumerate(objects):
            if focus and item[4] != focus:
                continue
            candidates = set(by_barcode[item[0]])
            if item[1] == "equipment" and item[3]:
                candidates.update(by_position[item[3]])
            for other in candidates:
                if other != index:
                    pairs.add((min(index, other), max(index, other)))
        warnings = set()
        for left_index, right_index in sorted(pairs):
            left, right = objects[left_index], objects[right_index]
            if left[0] == right[0]:
                if left[1] == right[1] == "equipment" and (not left[3] or not right[3] or left[3] == right[3]):
                    continue
                if left[1] == right[1] == "device" and left[4] == right[4]:
                    continue
                if left[1] == right[1] == "equipment":
                    warnings.add(f"同一机柜条码 {left[0]} 对应不同柜位：{left[2]}；{right[2]}。请核实。")
                elif left[1] != right[1] or left[1] == "device":
                    warnings.add(f"条码 {left[0]} 重复使用：{left[2]}；{right[2]}。请核实。")
            elif left[1] == right[1] == "equipment" and left[3] and left[3] == right[3]:
                warnings.add(f"同一柜位 {left[3]} 对应不同机柜条码：{left[2]} / {left[0]}；{right[2]} / {right[0]}。请核实。")
        return sorted(warnings)
    finally:
        conn.close()


def _capture_audit(conn: sqlite3.Connection, actor_id: int, action: str, target_type: str, target_id: int, detail: dict[str, Any], *, require_admin: bool = True) -> None:
    actor = conn.execute("SELECT name,role FROM users WHERE id=?", (actor_id,)).fetchone()
    if not actor or (require_admin and actor["role"] != "admin"):
        raise ValueError("仅管理员可修正采集数据")
    conn.execute(
        """INSERT INTO audit_logs(actor_id,actor_name,actor_role,action,target_type,target_id,detail_json,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (actor_id, actor["name"], actor["role"], action, target_type, target_id, json.dumps(detail, ensure_ascii=False), datetime.now().isoformat()),
    )


def save_admin_rack(task_id: int, values: dict[str, Any], actor_id: int, rack_id: int | None = None) -> dict[str, Any]:
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND is_deleted=0", (task_id,)).fetchone()
        if not task:
            raise ValueError("任务不存在或已作废")
        _require_task_not_waiting(conn, task_id)
        reason = _text(values.get("correction_reason"))
        if not reason:
            raise ValueError("请填写机架补录或更正原因")
        kind = _cabinet_type(values.get("cabinet_type"))
        if kind not in ("odf", "iodf"):
            raise ValueError("只能补录或更正 ODF / IODF 机架")
        name = _text(values.get("rack_no"))
        no_barcode = bool(values.get("no_barcode"))
        barcode = "" if no_barcode else normalize_barcode(values.get("cabinet_barcode") or "")
        if not name or (not no_barcode and not barcode):
            raise ValueError("请填写机架编号及条码，或勾选无条码")
        before = conn.execute("SELECT * FROM cabinet_records WHERE id=? AND task_id=? AND is_void=0", (rack_id, task_id)).fetchone() if rack_id else None
        if rack_id and (not before or before["cabinet_type"] not in ("odf", "iodf")):
            raise ValueError("机架不存在或已作废")
        _check_rack_identity(conn, task_id, kind, barcode, name, rack_id)
        now = datetime.now().isoformat()
        if rack_id:
            try:
                conn.execute("""UPDATE cabinet_records SET cabinet_type=?,cabinet_barcode=?,no_barcode=?,cabinet_name=?,location='',notes=?,updated_by=?,updated_at=? WHERE id=?""",
                             (kind, barcode, int(no_barcode), name, _text(values.get("notes")), actor_id, now, rack_id))
            except sqlite3.IntegrityError as exc:
                raise CaptureConflictError("本任务已存在相同条码或编号的机架，请修改已有记录") from exc
        else:
            cabinet, created = _insert_cabinet(conn, task_id=task_id, room_name=task["room_name"], area_name=task["area_name"], cabinet_type=kind, cabinet_barcode=barcode, no_barcode=no_barcode, cabinet_name=name, location="", source="manual", notes=_text(values.get("notes")), created_by=actor_id)
            if not created:
                raise CaptureConflictError("本任务已存在相同条码或编号的机架，请修改已有记录")
            rack_id = cabinet["id"]
            conn.execute("UPDATE cabinet_records SET updated_by=?,updated_at=scanned_at WHERE id=?", (actor_id, rack_id))
        after = dict(conn.execute("SELECT * FROM cabinet_records WHERE id=?", (rack_id,)).fetchone())
        _capture_audit(conn, actor_id, "cabinet.correct" if before else "cabinet.admin_create", "cabinet_record", rack_id, {"before": dict(before) if before else None, "after": after, "reason": reason})
        conn.commit()
        return next(c for c in get_task_cabinets(task_id) if c["id"] == rack_id)
    finally:
        conn.close()


def void_capture_record(table: str, record_id: int, actor_id: int, reason: str, task_id: int | None = None) -> bool:
    if table not in ("scan_records", "cabinet_records"):
        raise ValueError("无效采集类型")
    if not reason.strip():
        raise ValueError("请填写作废原因")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None or (task_id is not None and row["task_id"] != task_id):
            raise ValueError("采集记录不存在")
        if conn.execute("SELECT id FROM tasks WHERE id=? AND is_deleted=0", (row["task_id"],)).fetchone() is None:
            raise ValueError("任务已作废")
        _require_task_not_waiting(conn, row["task_id"])
        if table == "scan_records" and row["record_type"] != "surplus":
            raise ValueError("只能作废现场新增的盘盈设备，清单设备请修正现场状态")
        if table == "cabinet_records" and row["cabinet_type"] not in ("odf", "iodf"):
            raise ValueError("只能作废独立登记的 ODF / IODF 机架")
        if row["is_void"]:
            return False
        _capture_audit(conn, actor_id, "surplus.void" if table == "scan_records" else "cabinet.void", "scan_record" if table == "scan_records" else "cabinet_record", record_id, {"before": dict(row), "reason": reason.strip()})
        conn.execute(f"UPDATE {table} SET is_void=1,void_reason=?,voided_by=?,voided_at=? WHERE id=?", (reason.strip(), actor_id, datetime.now().isoformat(), record_id))
        conn.commit()
        return True
    finally:
        conn.close()


def create_cabinet(
    task_id: int,
    cabinet_type: str,
    cabinet_barcode: str,
    no_barcode: bool,
    rack_no: str,
    notes: str,
    created_by: int,
    client_request_id: str = "",
) -> tuple[dict[str, Any], bool] | None:
    """单独盘点 ODF/IODF；任务清单当前只包含设备。"""
    normalized_barcode = normalize_barcode(cabinet_barcode)
    normalized_rack_no = _text(rack_no)
    if not no_barcode and not normalized_barcode:
        raise ValueError("请填写机架条码")
    if not normalized_rack_no:
        raise ValueError("请填写机架编号")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = require_task_writable(conn, task_id, created_by)
        normalized_type = _cabinet_type(cabinet_type)
        if normalized_type == "equipment":
            raise ValueError("传输综合柜请随设备盘点提交，不在机架盘点中单独登记")
        _check_rack_identity(conn, task_id, normalized_type, normalized_barcode if not no_barcode else "", normalized_rack_no)
        cabinet, created = _insert_cabinet(
            conn,
            task_id=task_id,
            room_name=task["room_name"],
            area_name=task["area_name"],
            cabinet_type=normalized_type,
            cabinet_barcode=normalized_barcode if not no_barcode else "",
            no_barcode=no_barcode,
            # 旧数据库字段保留兼容，新记录的 cabinet_name 表示机架编号。
            cabinet_name=normalized_rack_no,
            location="",
            source="manual",
            notes=notes,
            created_by=created_by,
            client_request_id=client_request_id,
        )
        conn.commit()
        return cabinet, created
    finally:
        conn.close()


def delete_task_rack(task_id: int, cabinet_id: int, user_id: int) -> bool:
    """删除进行中任务的误录机架，原值与删除人在同一事务内留痕。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        require_task_writable(conn, task_id, user_id)
        row = conn.execute("SELECT * FROM cabinet_records WHERE id=? AND task_id=? AND is_void=0", (cabinet_id, task_id)).fetchone()
        if row is None:
            return False
        if row["cabinet_type"] not in ("odf", "iodf") or row["source"] != "manual":
            raise ValueError("只能删除本任务手工登记的 ODF / IODF 机架")
        if row["updated_at"]:
            raise ValueError("该机架已由管理员补录或修正，现场不能删除，请联系管理员")
        actor = conn.execute("SELECT name,role FROM users WHERE id=?", (user_id,)).fetchone()
        conn.execute("UPDATE cabinet_records SET is_void=1,void_reason='现场删除误录机架',voided_by=?,voided_at=? WHERE id=? AND task_id=?", (user_id, datetime.now().isoformat(), cabinet_id, task_id))
        conn.execute(
            """INSERT INTO audit_logs (actor_id, actor_name, actor_role, action, target_type, target_id, detail_json, created_at)
               VALUES (?, ?, ?, 'cabinet.delete', 'cabinet_record', ?, ?, ?)""",
            (user_id, actor["name"], actor["role"], cabinet_id, json.dumps(dict(row), ensure_ascii=False), datetime.now().isoformat()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_task_surplus(task_id: int, record_id: int, user_id: int) -> bool:
    """仅删除本人进行中任务的误录盘盈，软作废及审计在同一事务中完成。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        require_task_writable(conn, task_id, user_id)
        row = conn.execute("SELECT * FROM scan_records WHERE id=? AND task_id=?", (record_id, task_id)).fetchone()
        if row is None:
            raise ValueError("盘盈记录不存在或不属于当前任务")
        if row["record_type"] != "surplus":
            raise ValueError("只能删除现场新增的盘盈设备，不能删除清单设备")
        if row["is_void"]:
            return False
        _require_record_uncorrected(conn, dict(row))
        reason = "现场删除误录盘盈"
        _capture_audit(conn, user_id, "surplus.delete", "scan_record", record_id,
                       {"before": dict(row), "reason": reason}, require_admin=False)
        conn.execute("UPDATE scan_records SET is_void=1,void_reason=?,voided_by=?,voided_at=? WHERE id=?",
                     (reason, user_id, datetime.now().isoformat(), record_id))
        conn.commit()
        return True
    finally:
        conn.close()


def get_task_cabinets(task_id: int) -> list[dict[str, Any]]:
    """读取本任务原始登记；业务机柜汇总统一通过 get_task_cabinet_data 获取。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT c.*, u.name AS created_by_name
               FROM cabinet_records c
               LEFT JOIN users u ON u.id = c.created_by
               WHERE c.task_id = ? AND c.is_void=0
               ORDER BY c.id DESC""",
            (task_id,),
        ).fetchall()
        return [_cabinet_from_row(row) for row in rows if row is not None]
    finally:
        conn.close()


def get_prior_room_racks(task_id: int) -> list[dict[str, Any]]:
    """只返回同机房已完成历史批次的 ODF/IODF，供本次现场盘点参考。"""
    conn = get_conn()
    try:
        task = conn.execute("SELECT room_name, room_code FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            return []
        rows = conn.execute(
            """SELECT c.*, u.name AS created_by_name FROM cabinet_records c
               JOIN tasks t ON t.id=c.task_id
               LEFT JOIN users u ON u.id=c.created_by
               WHERE t.id!=? AND t.status='done' AND COALESCE(t.is_deleted,0)=0
                 AND c.cabinet_type IN ('odf','iodf') AND c.is_void=0
                 AND ((? != '' AND t.room_code=?) OR ((? = '' OR t.room_code='') AND t.room_name=?))
               ORDER BY t.completed_at DESC, c.id DESC""",
            (task_id, task["room_code"], task["room_code"], task["room_code"], task["room_name"]),
        ).fetchall()
        latest = {}
        for row in rows:
            rack = _cabinet_from_row(row)
            key = (rack["cabinet_type"], rack["rack_no"])
            latest.setdefault(key, rack)
        return list(latest.values())
    finally:
        conn.close()


def get_task_cabinet_data(task_id: int, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """机柜按最新有效设备结果汇总；ODF/IODF 继续读取本任务独立登记。"""
    cabinets = [c for c in get_task_cabinets(task_id) if c["cabinet_type"] in ("odf", "iodf")]
    records = get_task_items(task_id) if records is None else records
    conn = get_conn()
    try:
        task = conn.execute("SELECT room_name,area_name FROM tasks WHERE id=?", (task_id,)).fetchone()
        actor_ids = {r.get("corrected_by") if r.get("has_admin_correction") else r.get("created_by") for r in records}
        actor_ids.discard(None)
        placeholders = ",".join("?" for _ in actor_ids)
        users = {r["id"]: r["name"] for r in conn.execute(f"SELECT id,name FROM users WHERE id IN ({placeholders})", tuple(actor_ids))} if actor_ids else {}
    finally:
        conn.close()
    grouped = {}
    for record in records:
        if record.get("is_void"):
            continue
        if not record.get("scanned_at") or record.get("record_type") == "missing":
            continue
        no_barcode = bool(record.get("cabinet_no_barcode"))
        barcodes = [""] if no_barcode else sorted(split_codes(record.get("cabinet_barcodes") or ""))
        position = _text(record.get("rack_no"))
        corrected = bool(record.get("has_admin_correction"))
        actor_id = record.get("corrected_by") if corrected else record.get("created_by")
        updated_at = record.get("corrected_at") if corrected else record.get("scanned_at")
        for barcode in barcodes:
            # 缺少机柜位的无条码旧记录不能凭空合并为同一台机柜。
            identity = f"barcode:{barcode}" if barcode else f"position:{position}" if position else f"record:{record['id']}"
            if identity not in grouped:
                grouped[identity] = dict(id=f"equipment:{task_id}:{identity}", task_id=task_id,
                    room_name=task["room_name"] if task else "", area_name=task["area_name"] if task else "",
                    cabinet_type="equipment", cabinet_type_label="传输综合柜", cabinet_barcode=barcode,
                    no_barcode=no_barcode, cabinet_name="", source="device_summary", scanned_at="",
                    _positions=set(), _devices=set(), _actors=set(), _corrections=set())
            cabinet = grouped[identity]
            if position:
                cabinet["_positions"].add(position)
            cabinet["_devices"].add(record["device_name"])
            if users.get(actor_id):
                cabinet["_actors"].add(users[actor_id])
            if corrected:
                cabinet["_corrections"].add("现场整改反馈" if record.get("correction_reason") == "现场整改反馈" else "管理员修正")
            if (updated_at or "") >= cabinet["scanned_at"]:
                cabinet.update(scanned_at=updated_at or "", created_by=actor_id)
    for cabinet in grouped.values():
        positions = sorted(cabinet.pop("_positions"))
        cabinet["rack_no"] = cabinet["location"] = "、".join(positions)
        cabinet["created_by_name"] = "、".join(sorted(cabinet.pop("_actors")))
        corrections = sorted(cabinet.pop("_corrections"))
        cabinet["source_label"] = "设备盘点汇总" + ("（含" + "、".join(corrections) + "）" if corrections else "")
        cabinet["notes"] = "来源设备：" + "、".join(sorted(cabinet.pop("_devices")))
        if len(positions) > 1:
            cabinet["notes"] += "；同一机柜条码对应多个机柜位，待核实"
        elif not positions:
            cabinet["notes"] += "；机柜位缺失，待核实"
        cabinets.append(cabinet)
    return {
        "cabinets": cabinets,
        "summary": {
            "total": len(cabinets),
            "equipment": sum(1 for item in cabinets if item.get("cabinet_type") == "equipment"),
            "odf": sum(1 for item in cabinets if item.get("cabinet_type") == "odf"),
            "iodf": sum(1 for item in cabinets if item.get("cabinet_type") == "iodf"),
        },
    }


def apply_resource_baseline(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    cache: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """按设备记录 ID 读取当前综资值；只更新返回副本，不覆盖导入清单或现场反馈。"""
    record = dict(record)
    task_id = record["task_id"]
    if cache is None:
        cache = {}
    if task_id not in cache:
        baseline = conn.execute(
            """SELECT t.room_code, b.id, b.rows_json FROM tasks t
               LEFT JOIN resource_baselines b ON b.id=(
                   SELECT MAX(id) FROM resource_baselines WHERE task_id=t.id
               ) WHERE t.id=?""", (task_id,),
        ).fetchone()
        rows = json.loads(baseline["rows_json"]) if baseline and baseline["rows_json"] is not None else None
        by_record_id: dict[int, list[dict[str, Any]]] = {}
        for row in rows or []:
            if row.get("record_id") is not None:
                by_record_id.setdefault(row["record_id"], []).append(row)
        cache[task_id] = {
            "id": baseline["id"] if baseline and baseline["id"] is not None else 0,
            "rows": rows,
            "by_record_id": by_record_id,
            "room_code": baseline["room_code"] if baseline else "",
        }
    baseline = cache[task_id]
    record.update(
        resource_baseline_id=baseline["id"],
        resource_baseline_state="original",
        resource_device_name=record.get("device_name") or "",
        resource_location_code=baseline["room_code"] or "",
        resource_editable=True,
        resource_unavailable_reason="",
    )
    if "expected_rack_no" not in record:
        extra = record.get("device_extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (TypeError, ValueError):
                extra = {}
        record["expected_rack_no"] = str(extra.get("设备机架") or "") if isinstance(extra, dict) else ""

    if record.get("record_type") == "surplus":
        record.update(resource_baseline_state="surplus", resource_editable=False,
                      resource_unavailable_reason="现场新增设备尚无可直接编辑的综资记录，请先补充综资清单。")
        return record
    if baseline["rows"] is None:
        return record
    matches = baseline["by_record_id"].get(record["id"], [])
    if len(matches) == 1:
        source = matches[0]
        record.update(
            expected_model=source.get("model") or "",
            expected_barcode=source.get("barcode") or "",
            expected_rack_no=source.get("rack_no") or "",
            resource_baseline_state="matched",
            resource_device_name=source.get("name") or "",
            resource_location_code=source.get("location_code") or "",
        )
    else:
        record.update(
            expected_model="", expected_barcode="", expected_rack_no="",
            resource_baseline_state="missing" if not matches else "ambiguous",
            resource_device_name="", resource_location_code="", resource_editable=False,
            resource_unavailable_reason=("当前综资基线中已无此设备，请先核实或更新综资清单。" if not matches else
                                         "当前综资基线有多条记录关联此设备，请先核实对应关系。"),
        )
    return record


def get_task_items(task_id: int) -> list[dict[str, Any]]:
    """查询某个任务下的全部设备清单"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM scan_records WHERE task_id = ? AND is_void=0 ORDER BY id",
            (task_id,),
        ).fetchall()
        baseline_cache: dict[int, dict[str, Any]] = {}
        return [add_compare_status(apply_resource_baseline(conn, apply_latest_record_correction(conn, dict_from_row(r)), baseline_cache)) for r in rows]
    finally:
        conn.close()


def get_record_by_id(record_id: int) -> dict[str, Any] | None:
    """按记录 ID 查询单条盘点记录。"""
    conn = get_conn()
    try:
        record = dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone())
        return add_compare_status(apply_latest_record_correction(conn, record)) if record else None
    finally:
        conn.close()


CORRECTABLE_RECORD_FIELDS = (
    "actual_model",
    "asset_barcodes",
    "asset_no_barcode",
    "cabinet_barcodes",
    "cabinet_no_barcode",
    "rack_no",
    "notes",
    "record_type",
    "missing_reason",
)


def apply_latest_record_correction(
    conn: sqlite3.Connection,
    record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """保留现场原始值，并叠加最近一次管理员修正作为当前有效值。"""
    if record is None:
        return None
    latest = conn.execute(
        """SELECT c.*, u.name AS corrected_by_name
           FROM scan_record_corrections c
           LEFT JOIN users u ON u.id = c.corrected_by
           WHERE c.record_id = ?
             AND COALESCE(c.source_scanned_at, '') = COALESCE(?, '')
           ORDER BY c.id DESC LIMIT 1""",
        (record["id"], record.get("scanned_at") or ""),
    ).fetchone()
    record["has_admin_correction"] = bool(latest)
    record["correction_count"] = conn.execute(
        """SELECT COUNT(*) FROM scan_record_corrections
           WHERE record_id = ?
             AND COALESCE(source_scanned_at, '') = COALESCE(?, '')""",
        (record["id"], record.get("scanned_at") or ""),
    ).fetchone()[0]
    for field in CORRECTABLE_RECORD_FIELDS:
        record[f"original_{field}"] = record.get(field)
    if latest:
        for field in CORRECTABLE_RECORD_FIELDS:
            record[field] = latest[field]
        record["field_status"] = {
            "normal": "正常",
            "missing": "现场未找到",
            "surplus": "现场新增",
        }.get(record["record_type"], "正常")
        record["latest_correction_id"] = latest["id"]
        record["correction_reason"] = latest["correction_reason"]
        record["correction_note"] = latest["correction_note"]
        record["corrected_by"] = latest["corrected_by"]
        record["corrected_by_name"] = latest["corrected_by_name"] or ""
        record["corrected_at"] = latest["corrected_at"]
        if not record.get("scanned_at") and latest["correction_reason"] == "现场整改反馈":
            record["scanned_at"] = latest["corrected_at"]
            record["match_status"] = "不一致"

    return record


def _require_record_uncorrected(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    if apply_latest_record_correction(conn, dict(record)).get("has_admin_correction"):
        raise ValueError("该记录已由管理员修正，现场不能覆盖或删除，请联系管理员")


def list_record_corrections(record_id: int) -> list[dict[str, Any]]:
    """按时间倒序查询管理员修正历史。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT c.*, u.name AS corrected_by_name
               FROM scan_record_corrections c
               LEFT JOIN users u ON u.id = c.corrected_by
               WHERE c.record_id = ?
               ORDER BY c.id DESC""",
            (record_id,),
        ).fetchall()
        return [dict_from_row(row) for row in rows if row is not None]
    finally:
        conn.close()


def create_record_correction(
    record_id: int,
    values: dict[str, Any],
    correction_reason: str,
    correction_note: str,
    corrected_by: int,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """新增一版管理员修正；现场原始记录保持不变。"""
    conn = connection or get_conn()
    try:
        if connection is None:
            conn.execute("BEGIN IMMEDIATE")
        original = dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?",
            (record_id,),
        ).fetchone())
        if original is None:
            return None
        if original.get("is_void"):
            raise ValueError("该盘盈设备已作废，不能再修改")
        if conn.execute("SELECT id FROM tasks WHERE id = ? AND is_deleted = 0", (original["task_id"],)).fetchone() is None:
            raise ValueError("任务已作废，不能修正业务结果")
        _require_task_not_waiting(conn, original["task_id"])
        before_effective = apply_latest_record_correction(conn, dict(original))
        if before_effective.get("match_status") == "未扫" and connection is None:
            raise ValueError("尚未提交现场反馈的设备不能由管理员代填，请退回现场盘点")
        closed_adjustment = conn.execute(
            """SELECT id FROM ai_adjustment_tasks
               WHERE source_type IN ('rule', 'manual')
                 AND (
                     source_key LIKE ?
                     OR source_key LIKE ?
                     OR source_key LIKE ?
                 )
                 AND status = 'done'
               ORDER BY id DESC LIMIT 1""",
            (f"{record_id}:%", f"location:{record_id}:%", f"location:%:{record_id}"),
        ).fetchone()
        if closed_adjustment:
            raise ValueError(f"该记录关联的整改任务 #{closed_adjustment['id']} 已闭环，不能直接修改")

        reason = correction_reason.strip()
        note = correction_note.strip()
        if not reason:
            raise ValueError("请选择修正原因")
        if reason == "其他" and not note:
            raise ValueError("选择其他原因时必须填写修正说明")

        new_name = values.get("device_name")
        if new_name is not None:
            new_name = _text(new_name)
            if (original.get("record_type") or "normal") != "surplus" and new_name != original["device_name"]:
                raise ValueError("只能更正盘盈设备名称，不能修改综资清单设备名称")
            if not new_name:
                raise ValueError("请填写盘盈设备名称")

        actual_model = _text(values.get("actual_model"))
        asset_no_barcode = bool(values.get("asset_no_barcode"))
        asset_barcodes = "" if asset_no_barcode else ",".join(sorted(split_codes(_text(values.get("asset_barcodes")))))
        cabinet_no_barcode = bool(values.get("cabinet_no_barcode"))
        cabinet_barcodes = "" if cabinet_no_barcode else ",".join(sorted(split_codes(_text(values.get("cabinet_barcodes")))))
        rack_no = _text(values.get("rack_no"))
        notes = _text(values.get("notes"))
        record_type = _text(values.get("record_type")) or (original.get("record_type") or "normal")
        if (original.get("record_type") or "normal") == "surplus" and record_type != "surplus":
            raise ValueError("现场新增设备不能直接改成综资清单设备")
        if (original.get("record_type") or "normal") != "surplus" and record_type not in {"normal", "missing"}:
            raise ValueError("综资清单设备的现场状态只能修正为正常或现场未找到")
        missing_reason = _text(values.get("missing_reason"))
        if record_type in {"normal", "surplus"}:
            missing_reason = ""
            if not actual_model:
                raise ValueError("管理员修正后必须保留现场设备型号")
            if not asset_no_barcode and not asset_barcodes:
                raise ValueError("请填写现场资产条码或勾选无资产条码")
            if not cabinet_no_barcode and not cabinet_barcodes:
                raise ValueError("请填写现场机柜条码或勾选无机柜条码")
            if not rack_no:
                raise ValueError("管理员修正后必须保留现场机架号")
        elif record_type == "missing":
            if not missing_reason:
                raise ValueError("现场未找到时必须填写原因")
            actual_model = asset_barcodes = cabinet_barcodes = rack_no = ""
            asset_no_barcode = cabinet_no_barcode = False

        now = datetime.now().isoformat()
        cursor = conn.execute(
            """INSERT INTO scan_record_corrections
               (record_id, actual_model, asset_barcodes, asset_no_barcode,
                cabinet_barcodes, cabinet_no_barcode, rack_no, notes,
                record_type, missing_reason, correction_reason, correction_note,
                corrected_by, corrected_at, source_scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                actual_model,
                asset_barcodes,
                1 if asset_no_barcode else 0,
                cabinet_barcodes,
                1 if cabinet_no_barcode else 0,
                rack_no,
                notes,
                record_type,
                missing_reason,
                reason,
                note,
                corrected_by,
                now,
                original.get("scanned_at") or "",
            ),
        )
        if new_name is not None and new_name != original["device_name"]:
            conn.execute("UPDATE scan_records SET device_name=?,device_name_corrected_at=? WHERE id=?", (new_name, now, record_id))
            _capture_audit(conn, corrected_by, "surplus.rename", "scan_record", record_id, {"before": {"device_name": original["device_name"]}, "after": {"device_name": new_name}, "reason": reason, "note": note})
            original["device_name"] = new_name
            original["device_name_corrected_at"] = now
        from .three_way import track_sources
        after_values = dict(model=actual_model, barcode=asset_barcodes, no_barcode=asset_no_barcode, record_type=record_type)
        before_values = dict(model=before_effective["actual_model"], barcode=before_effective["asset_barcodes"], no_barcode=bool(before_effective["asset_no_barcode"]), record_type=before_effective["record_type"])
        track_sources(conn, {f"field:{record_id}": before_values}, {f"field:{record_id}": after_values})
        if connection is None:
            conn.commit()
        effective = apply_latest_record_correction(conn, original)
        if effective is None:
            return None
        effective = add_compare_status(effective)
        effective["related_adjustment_ids"] = [
            row["id"] for row in conn.execute(
                """SELECT id FROM ai_adjustment_tasks
                   WHERE source_type IN ('rule', 'manual')
                     AND (
                         source_key LIKE ?
                         OR source_key LIKE ?
                         OR source_key LIKE ?
                     )
                     AND status NOT IN ('done', 'rejected')
                   ORDER BY id""",
                (f"{record_id}:%", f"location:{record_id}:%", f"location:%:{record_id}"),
            ).fetchall()
        ]
        effective["latest_correction_id"] = cursor.lastrowid
        return effective
    finally:
        if connection is None:
            conn.close()


def mark_record_missing(record_id: int, reason: str, notes: str = "", created_by: int | None = None) -> dict[str, Any] | None:
    """把任务清单内设备标记为盘亏：现场应有但未找到。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        record = dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone())
        if record is None:
            return None
        if record.get("is_void"):
            raise ValueError("该记录已作废")
        require_task_writable(conn, record["task_id"], created_by)
        _require_record_uncorrected(conn, record)
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
                asset_barcodes = '', asset_no_barcode = 0,
                cabinet_barcodes = '', cabinet_no_barcode = 0,
                rack_no = '', actual_model = '', created_by = ?,
                match_status = '不一致',
                scanned_at = ?,
                inherited_from_task_id = NULL, inherited_from_record_id = NULL, inherited_at = '', inherited_source_label = ''
            WHERE id = ?
        """, (
            final_reason,
            notes.strip() or final_reason,
            created_by,
            datetime.now().isoformat(),
            record_id,
        ))
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
        conn.execute("BEGIN IMMEDIATE")
        task = require_task_writable(conn, task_id, created_by)
        asset_barcodes = "" if asset_no_barcode else ",".join(sorted(split_codes(asset_barcodes)))
        cabinet_barcodes = "" if cabinet_no_barcode else ",".join(sorted(split_codes(cabinet_barcodes)))
        if not device_name.strip():
            raise ValueError("请填写盘盈设备名称")
        if not actual_model.strip():
            raise ValueError("请填写现场设备型号")
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
                    match_status, scanned_at, created_by, client_request_id
                ) VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, 'surplus', '盘盈', '不一致', ?, ?, ?)
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
                    if existing["is_void"]:
                        raise ValueError("该盘盈记录已作废，请重新登记")
                    return add_compare_status(dict_from_row(existing))
            raise
        _register_scan_cabinets(
            conn,
            task_id=task_id,
            room_name=task["room_name"],
            area_name=task["area_name"],
            cabinet_barcodes=cabinet_barcodes,
            cabinet_no_barcode=cabinet_no_barcode,
            rack_no=rack_no,
            created_by=created_by,
        )
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
    created_by: int | None = None,
    device_name: str | None = None,
) -> dict[str, Any] | None:
    """提交一条盘点记录，返回更新后的记录"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 先查期望型号，用于比对
        record = dict_from_row(conn.execute(
            "SELECT * FROM scan_records WHERE id = ?", (record_id,)
        ).fetchone())
        if record is None:
            return None
        if record.get("is_void"):
            raise ValueError("该记录已作废")

        task = require_task_writable(conn, record["task_id"], created_by)
        _require_record_uncorrected(conn, record)
        new_name = record["device_name"] if device_name is None else device_name.strip()
        if device_name is not None:
            if (record.get("record_type") or "normal") != "surplus" and new_name != record["device_name"]:
                raise ValueError("只能修改盘盈设备名称，不能修改综资清单设备名称")
            if not new_name or len(new_name) > 500:
                raise ValueError("请填写不超过500字的盘盈设备名称")
        asset_barcodes = "" if asset_no_barcode else ",".join(sorted(split_codes(asset_barcodes)))
        cabinet_barcodes = "" if cabinet_no_barcode else ",".join(sorted(split_codes(cabinet_barcodes)))

        if not rack_no.strip():
            raise ValueError("请填写现场机架号")
        if not actual_model.strip():
            raise ValueError("请填写现场设备型号")
        if not asset_no_barcode and not asset_barcodes.strip():
            raise ValueError("请填写现场设备资产条码或勾选无条码")
        if not cabinet_no_barcode and not cabinet_barcodes.strip():
            raise ValueError("请填写机柜资产条码或勾选无条码")

        model_match_status = calc_model_match_status(actual_model, record["expected_model"])
        asset_match_status = calc_asset_match_status(
            asset_barcodes,
            asset_no_barcode,
            record["expected_barcode"],
        )
        record_type = "surplus" if record["record_type"] == "surplus" else "normal"
        match_status = "不一致" if record_type == "surplus" else calc_overall_match_status(asset_match_status, model_match_status)

        conn.execute("""
            UPDATE scan_records SET
                asset_barcodes = ?,
                asset_no_barcode = ?,
                cabinet_barcodes = ?,
                cabinet_no_barcode = ?,
                rack_no = ?,
                actual_model = ?,
                notes = ?,
                record_type = ?,
                field_status = ?,
                created_by = ?,
                missing_reason = '',
                match_status = ?,
                scanned_at = ?,
                inherited_from_task_id = NULL, inherited_from_record_id = NULL, inherited_at = '', inherited_source_label = ''
            WHERE id = ?
        """, (
            asset_barcodes,
            1 if asset_no_barcode else 0,
            cabinet_barcodes,
            1 if cabinet_no_barcode else 0,
            rack_no.strip(),
            actual_model,
            notes,
            record_type,
            "盘盈" if record_type == "surplus" else "正常",
            created_by,
            match_status,
            datetime.now().isoformat(),
            record_id,
        ))
        _register_scan_cabinets(
            conn,
            task_id=record["task_id"],
            room_name=task["room_name"],
            area_name=task["area_name"],
            cabinet_barcodes=cabinet_barcodes,
            cabinet_no_barcode=cabinet_no_barcode,
            rack_no=rack_no,
            created_by=created_by,
        )
        if new_name != record["device_name"]:
            now = datetime.now().isoformat()
            conn.execute("UPDATE scan_records SET device_name=?,device_name_corrected_at=? WHERE id=?", (new_name, now, record_id))
            _capture_audit(conn, created_by, "surplus.rename", "scan_record", record_id,
                           {"before": {"device_name": record["device_name"]}, "after": {"device_name": new_name}, "reason": "现场修正本次录入"}, require_admin=False)
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
        conn.execute("BEGIN")
        task = dict_from_row(conn.execute(
            "SELECT t.*, u.name AS assigned_name FROM tasks t LEFT JOIN users u ON u.id = t.assigned_to WHERE t.id = ?",
            (task_id,),
        ).fetchone())
        if task is None:
            return []

        records = conn.execute(
            "SELECT * FROM scan_records WHERE task_id = ? AND is_void=0 ORDER BY id",
            (task_id,),
        ).fetchall()
        baseline_cache: dict[int, dict[str, Any]] = {}
        effective = [add_compare_status(apply_resource_baseline(conn, apply_latest_record_correction(conn, dict_from_row(r)), baseline_cache)) for r in records]
        from .inventory_pairing import project_records
        return project_records(conn, task_id, effective)
    finally:
        conn.close()
