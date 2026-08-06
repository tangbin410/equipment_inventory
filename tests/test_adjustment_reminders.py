import tempfile
import unittest
from pathlib import Path

from backend import database


class AdjustmentReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()

        conn = database.get_conn()
        admin_id = conn.execute(
            "INSERT INTO users (wx_openid, name, role) VALUES ('admin-test', '管理员', 'admin')"
        ).lastrowid
        analysis_id = conn.execute(
            """INSERT INTO ai_analysis_results
               (task_ids_json, status, created_by, created_at)
               VALUES ('[]', 'success', ?, datetime('now'))""",
            (admin_id,),
        ).lastrowid
        self.adjustment_id = conn.execute(
            """INSERT INTO ai_adjustment_tasks
               (analysis_id, status, created_by, created_at, dispatched_at)
               VALUES (?, 'dispatched', ?, datetime('now'), datetime('now'))""",
            (analysis_id, admin_id),
        ).lastrowid
        conn.commit()
        conn.close()
        self.admin_id = admin_id

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def test_reminder_increments_without_changing_status(self):
        first = database.remind_ai_adjustment_task(self.adjustment_id, self.admin_id, "请跟进")
        second = database.remind_ai_adjustment_task(self.adjustment_id, self.admin_id, "再次跟进")

        self.assertEqual(first["status"], "dispatched")
        self.assertEqual(first["reminder_count"], 1)
        self.assertEqual(second["status"], "dispatched")
        self.assertEqual(second["reminder_count"], 2)
        self.assertEqual(second["reminder_note"], "再次跟进")
        self.assertEqual(second["reminded_by"], self.admin_id)
        self.assertTrue(second["reminded_at"])

    def test_only_dispatched_adjustment_can_be_reminded(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE ai_adjustment_tasks SET status = 'done' WHERE id = ?",
            (self.adjustment_id,),
        )
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(ValueError, "只有已下发"):
            database.remind_ai_adjustment_task(self.adjustment_id, self.admin_id)


if __name__ == "__main__":
    unittest.main()
