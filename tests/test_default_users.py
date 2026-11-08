import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from backend import database
from backend.main import _ensure_default_user


class DefaultUserInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def test_existing_default_user_profile_is_preserved(self):
        original = database.create_user(
            "worker_001",
            "张三",
            "worker",
            "北区",
            phone="13800138000",
            employee_no="TB001",
        )

        user = _ensure_default_user("worker_001", "张三", "worker", "北区")

        self.assertEqual(user["id"], original["id"])
        self.assertEqual(user["phone"], "13800138000")
        self.assertEqual(user["employee_no"], "TB001")

    def test_missing_default_user_is_created(self):
        user = _ensure_default_user("worker_002", "李四", "worker", "南区")

        self.assertEqual(user["name"], "李四")
        self.assertEqual(user["area_name"], "南区")

    def test_restart_preserves_password_reset_in_account_management(self):
        from backend.auth import hash_password
        from backend.main import on_startup

        user = database.create_user("admin_001", "管理员", "admin", password_hash=hash_password("new-account-password-123"))
        with patch.dict("os.environ", {
            "INVENTORY_SECRET_KEY": "isolated-startup-test-key-with-32-characters",
            "INVENTORY_ADMIN_PASSWORD": "old-bootstrap-password-123",
        }):
            on_startup()
        after = database.get_user_by_id(user["id"])
        self.assertEqual(after["password_hash"], user["password_hash"])
        self.assertEqual(after["auth_version"], user["auth_version"])


if __name__ == "__main__":
    unittest.main()
