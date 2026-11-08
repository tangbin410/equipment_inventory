import builtins
import getpass
import io
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend import admin_recovery as recovery
from backend import database
from backend.auth import create_access_token, decode_access_token, hash_password, verify_password


class Terminal(io.StringIO):
    def isatty(self):
        return True


class AdminRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = "temporary-old-password-123"
        cls.new_password = "temporary-new-password-456"
        cls.old_hash = hash_password(cls.old_password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recovery project ")
        self.path = Path(self.temp.name) / "existing.db"
        self.patch = patch.object(database, "DB_PATH", self.path)
        self.patch.start()
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY, wx_openid TEXT UNIQUE, name TEXT,
                    role TEXT, admin_scope TEXT, is_active INTEGER, is_deleted INTEGER,
                    password_hash TEXT, auth_version INTEGER);
                CREATE TABLE audit_logs(id INTEGER PRIMARY KEY, actor_id INTEGER, actor_name TEXT,
                    actor_role TEXT, action TEXT, target_type TEXT, target_id INTEGER,
                    detail_json TEXT, request_id TEXT, created_at TEXT);
                CREATE TABLE business_records(id INTEGER PRIMARY KEY, content TEXT);
                INSERT INTO business_records VALUES(1, 'unchanged inventory');
            """)
            for values in ((1, "admin_001", "A", "admin", "full", 1, 0),
                           (2, "admin_b", "B", "admin", "collaborator", 1, 0),
                           (3, "worker", "W", "worker", "full", 1, 0),
                           (4, "disabled", "D", "admin", "full", 0, 0),
                           (5, "deleted", "X", "admin", "full", 1, 1)):
                conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?)", (*values, self.old_hash, 7))

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def rows(self, table):
        with sqlite3.connect(self.path) as conn:
            return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def backups(self):
        return list((self.path.parent / "backups").glob("*.db"))

    def run_interactive(self, inputs, passwords):
        out, err = Terminal(), Terminal()
        with patch("sys.stdin", Terminal()), patch("sys.stdout", out), patch("sys.stderr", err), \
             patch("builtins.input", side_effect=inputs), patch("getpass.getpass", side_effect=passwords):
            result = recovery.main([])
        return result, out.getvalue() + err.getvalue()

    def test_changes_only_target_password_version_and_audit(self):
        before = self.rows("users")
        business = self.rows("business_records")
        backup = recovery._save_password("admin_001", self.new_password, 1)
        after = self.rows("users")
        self.assertEqual(before[1:], after[1:])
        self.assertEqual(before[0][:-2], after[0][:-2])
        self.assertTrue(verify_password(self.new_password, after[0][-2]))
        self.assertFalse(verify_password(self.old_password, after[0][-2]))
        self.assertEqual(after[0][-1], before[0][-1] + 1)
        self.assertEqual(self.rows("business_records"), business)
        audit = self.rows("audit_logs")[0]
        self.assertIsNone(audit[1])
        self.assertEqual(audit[3:7], ("local_recovery", "admin.password.local_recovery", "user", 1))
        self.assertEqual(json.loads(audit[7])["source"], "local_interactive_recovery")
        self.assertNotIn(self.new_password, repr(audit))
        self.assertNotIn(after[0][-2], repr(audit))
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("SELECT * FROM users ORDER BY id").fetchall(), before)
            self.assertEqual(conn.execute("SELECT * FROM business_records ORDER BY id").fetchall(), business)

    def test_collaborator_is_existing_admin_without_role_change(self):
        before = self.rows("users")
        recovery._save_password("admin_b", self.new_password, 2)
        after = self.rows("users")
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1][:-2], before[1][:-2])
        self.assertTrue(verify_password(self.new_password, after[1][-2]))

    def test_old_token_authorization_version_is_revoked(self):
        with patch.dict(os.environ, {"INVENTORY_SECRET_KEY": "temporary-secret-at-least-thirty-two-characters"}):
            old_token = create_access_token({"id": 1, "role": "admin", "auth_version": 7}, "password")
            recovery._save_password("admin_001", self.new_password, 1)
            # current_user rejects tokens whose ver differs from the latest database auth_version.
            self.assertNotEqual(decode_access_token(old_token)["ver"], self.rows("users")[0][-1])

    def test_ineligible_or_replaced_accounts_are_rejected_before_backup(self):
        before = self.rows("users")
        for login, expected_id in (("worker", 3), ("disabled", 4), ("deleted", 5),
                                   ("missing", 999), ("admin_001", 2)):
            with self.subTest(login=login), self.assertRaises(recovery.RecoveryError):
                recovery._save_password(login, self.new_password, expected_id)
        self.assertEqual(self.rows("users"), before)
        self.assertFalse(self.backups())

    def test_password_validation_does_not_modify_or_backup(self):
        before = self.rows("users")
        for password in ("short", " " * 12, "x" * 129):
            with self.subTest(length=len(password)), self.assertRaises(recovery.RecoveryError):
                recovery._save_password("admin_001", password, 1)
        self.assertEqual(self.rows("users"), before)
        self.assertFalse(self.backups())

    def test_missing_database_is_never_created(self):
        missing = self.path.parent / "not-created" / "missing.db"
        with patch.object(database, "DB_PATH", missing), self.assertRaises(recovery.RecoveryError):
            recovery._save_password("admin_001", self.new_password, 1)
        self.assertFalse(missing.parent.exists())

    def test_backup_failure_refuses_password_update(self):
        before = self.rows("users")
        with patch.object(recovery, "_backup_database", side_effect=OSError("test backup failure")):
            with self.assertRaises(OSError):
                recovery._save_password("admin_001", self.new_password, 1)
        self.assertEqual(self.rows("users"), before)
        self.assertFalse(self.rows("audit_logs"))

    def test_backup_failure_does_not_delete_other_backups(self):
        folder = self.path.parent / "backups"
        folder.mkdir()
        preserved = folder / "existing-backup.db"
        preserved.write_bytes(b"preserve existing backup")
        before = self.rows("users")
        with patch.object(recovery.time, "monotonic", side_effect=[0, 999999999]):
            with self.assertRaisesRegex(recovery.RecoveryError, "备份失败"):
                recovery._save_password("admin_001", self.new_password, 1)
        self.assertEqual(preserved.read_bytes(), b"preserve existing backup")
        self.assertEqual(self.backups(), [preserved])
        self.assertEqual(self.rows("users"), before)

    def test_audit_failure_rolls_back_password_and_version(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TRIGGER audit_reject BEFORE INSERT ON audit_logs
                            BEGIN SELECT RAISE(ABORT, 'isolated audit failure'); END""")
        before = self.rows("users")
        with self.assertRaises(sqlite3.IntegrityError):
            recovery._save_password("admin_001", self.new_password, 1)
        self.assertEqual(self.rows("users"), before)
        self.assertFalse(self.rows("audit_logs"))
        self.assertEqual(len(self.backups()), 1)

    def test_account_state_is_rechecked_after_backup(self):
        original = recovery._backup_database
        before = self.rows("users")[0]

        def disable_after_backup(conn):
            backup = original(conn)
            with sqlite3.connect(self.path) as other:
                other.execute("UPDATE users SET is_active=0 WHERE id=1")
            return backup

        with patch.object(recovery, "_backup_database", side_effect=disable_after_backup):
            with self.assertRaises(recovery.RecoveryError):
                recovery._save_password("admin_001", self.new_password, 1)
        after = self.rows("users")[0]
        self.assertEqual(after[-2:], before[-2:])
        self.assertEqual(after[5], 0)
        self.assertFalse(self.rows("audit_logs"))

    def test_non_terminal_rejects_without_prompt_or_changes(self):
        before = self.rows("users")
        with patch("sys.stdin", io.StringIO()), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
             patch("builtins.input") as prompt:
            self.assertEqual(recovery.main([]), 2)
            prompt.assert_not_called()
        self.assertEqual(self.rows("users"), before)
        self.assertFalse(self.backups())

    def test_arguments_are_rejected_without_echoing_password(self):
        err = io.StringIO()
        with redirect_stderr(err), patch("builtins.input") as prompt:
            self.assertEqual(recovery.main(["--password", self.new_password]), 2)
        self.assertNotIn(self.new_password, err.getvalue())
        prompt.assert_not_called()
        self.assertFalse(self.backups())

    def test_cancel_mismatch_and_invalid_password_leave_database_unchanged(self):
        before = self.rows("users")
        cases = [([""], [""]), ([""], [self.new_password, ""]),
                 ([""], [self.new_password, "different-password-123"]),
                 ([""], ["short", "short"]), (["", "cancel"], [self.new_password] * 2)]
        for inputs, passwords in cases:
            with self.subTest(inputs=inputs):
                result, output = self.run_interactive(inputs, passwords)
                self.assertIn(result, (0, 1))
                self.assertNotIn(self.new_password, output)
                self.assertEqual(self.rows("users"), before)
                self.assertFalse(self.backups())

    def test_interactive_success_prints_no_password_or_hash(self):
        result, output = self.run_interactive(["", "RESET"], [self.new_password] * 2)
        self.assertEqual(result, 0)
        self.assertIn("无需重启", output)
        self.assertNotIn(self.new_password, output)
        self.assertNotIn(self.rows("users")[0][-2], output)

    def test_keyboard_interrupt_and_eof_cancel_without_backup(self):
        before = self.rows("users")
        for error in (KeyboardInterrupt(), EOFError()):
            out, err = Terminal(), Terminal()
            with patch("sys.stdin", Terminal()), patch("sys.stdout", out), patch("sys.stderr", err), \
                 patch("builtins.input", side_effect=error):
                self.assertEqual(recovery.main([]), 0)
            self.assertEqual(self.rows("users"), before)
            self.assertFalse(self.backups())

    def test_hidden_input_failure_does_not_fall_back_to_echoed_input(self):
        before = self.rows("users")
        result, output = self.run_interactive([""], [getpass.GetPassWarning("cannot hide password")])
        self.assertEqual(result, 0)
        self.assertIn("已取消", output)
        self.assertEqual(self.rows("users"), before)
        self.assertFalse(self.backups())

    def test_password_can_be_twelve_or_128_characters(self):
        for password in ("x" * 12, "y" * 128):
            with self.subTest(length=len(password)):
                recovery._save_password("admin_b", password, 2)
                self.assertTrue(verify_password(password, self.rows("users")[1][-2]))

    def test_internal_exception_message_cannot_leak_password(self):
        with patch.object(recovery, "_save_password", side_effect=RuntimeError(self.new_password)):
            result, output = self.run_interactive(["", "RESET"], [self.new_password] * 2)
        self.assertEqual(result, 1)
        self.assertNotIn(self.new_password, output)

    def test_post_save_output_failure_does_not_claim_password_was_unchanged(self):
        real_print = builtins.print

        def fail_success_output(*args, **kwargs):
            if args and str(args[0]).startswith("密码已更新"):
                raise OSError("isolated terminal failure")
            real_print(*args, **kwargs)

        with patch("builtins.print", side_effect=fail_success_output):
            result, output = self.run_interactive(["", "RESET"], [self.new_password] * 2)
        self.assertEqual(result, 1)
        self.assertIn("密码已保存", output)
        self.assertNotIn("密码未修改", output)
        self.assertTrue(verify_password(self.new_password, self.rows("users")[0][-2]))

    def test_save_stage_interrupt_reports_result_needs_verification(self):
        with patch.object(recovery, "_save_password", side_effect=KeyboardInterrupt()):
            result, output = self.run_interactive(["", "RESET"], [self.new_password] * 2)
        self.assertEqual(result, 1)
        self.assertIn("核对恢复结果", output)
        self.assertNotIn("密码未修改", output)
        self.assertFalse(self.backups())

    def test_shell_refuses_piped_input_without_accessing_database(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts/mac/reset_admin_password_mac.command"
        before = self.rows("users")
        result = subprocess.run(["/bin/bash", str(script)], input="admin_001\n" + self.new_password,
                                text=True, capture_output=True, cwd=self.path.parent, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(self.new_password, result.stdout + result.stderr)
        self.assertEqual(self.rows("users"), before)


if __name__ == "__main__":
    unittest.main()
