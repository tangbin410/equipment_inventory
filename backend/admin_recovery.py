"""仅供本机交互终端恢复现有管理员密码；不提供 HTTP 接口。"""

from __future__ import annotations

import getpass
import json
import os
import sqlite3
import sys
import tempfile
import time
import warnings
from datetime import datetime
from pathlib import Path

from . import database
from .auth import hash_password


class RecoveryError(Exception):
    """可以向本机操作人显示、且不含凭据的恢复失败原因。"""


def _connection() -> sqlite3.Connection:
    path = Path(database.DB_PATH)
    if not path.is_file():
        raise RecoveryError("现有数据库不存在，已停止；本工具不会创建数据库或账号。")
    # mode=rw 在文件被并发删除时也不会自动创建空数据库。
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True,
                           timeout=database.DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    return conn


def _target(conn: sqlite3.Connection, login: str, expected_id: int | None = None) -> sqlite3.Row:
    row = conn.execute(
        """SELECT id, wx_openid, role, admin_scope, is_active, is_deleted
           FROM users WHERE wx_openid=?""", (login,),
    ).fetchone()
    if (row is None or row["role"] != "admin" or row["is_active"] != 1
            or row["is_deleted"] != 0 or row["admin_scope"] not in {"full", "collaborator"}
            or (expected_id is not None and row["id"] != expected_id)):
        raise RecoveryError("目标不是现有启用的后台管理员，已停止；不会创建、启用或升级账号。")
    return row


def _backup_database(conn: sqlite3.Connection) -> Path:
    # 跟随当前 DB_PATH，临时数据库测试不会写入真实备份目录。
    directory = Path(database.DB_PATH).parent / "backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix="before_admin_password_recovery_", suffix=".db", dir=directory)
    path = Path(filename)
    try:
        os.fchmod(fd, 0o600)
        os.close(fd)
        fd = -1
        deadline = time.monotonic() + database.DB_TIMEOUT_SECONDS

        def progress(_status: int, _remaining: int, _total: int) -> None:
            if time.monotonic() > deadline:
                raise RecoveryError("数据库备份超时，密码未修改。")

        backup = sqlite3.connect(path)
        try:
            conn.backup(backup, pages=128, progress=progress, sleep=0.05)
        finally:
            backup.close()
        return path
    except BaseException as exc:
        # 只清除此调用创建的未完成备份，绝不轮换或删除其他备份。
        path.unlink(missing_ok=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise RecoveryError("数据库备份失败，密码未修改。") from None
    finally:
        if fd != -1:
            os.close(fd)


def _save_password(login: str, password: str, expected_id: int) -> Path:
    if not 12 <= len(password) <= 128 or not password.strip():
        raise RecoveryError("新密码须为 12—128 位，且不能全部为空格。")
    encoded = hash_password(password)
    conn = _connection()
    try:
        _target(conn, login, expected_id)
        backup = _backup_database(conn)
        conn.execute("BEGIN IMMEDIATE")
        # 输入期间或备份期间账号可能被改名、停用、降级或删除，持写锁后重查。
        target = _target(conn, login, expected_id)
        conn.execute(
            """UPDATE users SET password_hash=?, auth_version=COALESCE(auth_version,0)+1
               WHERE id=?""", (encoded, target["id"]),
        )
        conn.execute(
            """INSERT INTO audit_logs
               (actor_id, actor_name, actor_role, action, target_type, target_id,
                detail_json, request_id, created_at)
               VALUES (NULL, ?, ?, ?, 'user', ?, ?, '', ?)""",
            ("本机密码恢复", "local_recovery", "admin.password.local_recovery", target["id"],
             json.dumps({"source": "local_interactive_recovery", "sessions_revoked": True}, ensure_ascii=False),
             datetime.now().isoformat()),
        )
        conn.commit()
        return backup
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("不接受命令行参数或密码参数，请直接运行本机交互工具。", file=sys.stderr)
        return 2
    if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        print("必须在本机交互终端运行，不接受管道、重定向或无人值守输入。", file=sys.stderr)
        return 2
    stage = "input"
    try:
        print("本机管理员密码恢复：只改密码，不创建账号、不改角色、不修改 .env。")
        login = input("管理员登录编码 [admin_001]（按 Control-C 取消）：").strip() or "admin_001"
        conn = _connection()
        try:
            target_id = _target(conn, login)["id"]
        finally:
            conn.close()
        with warnings.catch_warnings():
            # 禁止 getpass 在无法隐藏输入时回退成可见输入。
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("新密码（12—128 位，留空取消）：")
            if not password:
                print("已取消，密码未修改。")
                return 0
            confirmation = getpass.getpass("再次输入新密码（留空取消）：")
        if not confirmation:
            print("已取消，密码未修改。")
            return 0
        if password != confirmation:
            raise RecoveryError("两次密码不一致，密码未修改。")
        if not 12 <= len(password) <= 128 or not password.strip():
            raise RecoveryError("新密码须为 12—128 位，且不能全部为空格。")
        print(f"即将更新管理员登录编码 {login!r} 的密码，并使此账号旧登录失效。")
        if input("输入 RESET 确认（其他输入取消）：").strip() != "RESET":
            print("已取消，密码未修改。")
            return 0
        stage = "saving"
        backup = _save_password(login, password, target_id)
        stage = "saved"
        print("密码已更新，原登录已失效；无需重启服务，请使用新密码重新登录。")
        print(f"修改前数据库备份：{backup}")
        return 0
    except (KeyboardInterrupt, EOFError, getpass.GetPassWarning):
        if stage != "input":
            print("\n恢复保存阶段已中断，请用新密码尝试登录并核对恢复结果。", file=sys.stderr)
            return 1
        print("\n已取消，密码未修改。")
        return 0
    except RecoveryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # 数据库/操作系统异常不回显，避免第三方异常消息带出输入或内部内容。
        if stage == "saved":
            print("密码已保存，但结果显示失败；请使用新密码重新登录。", file=sys.stderr)
        elif stage == "saving":
            print("恢复保存过程发生错误，请用新密码尝试登录，或联系维护人员核对恢复结果。", file=sys.stderr)
        else:
            print("恢复失败，密码未修改；请检查数据库及备份目录是否可用。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
