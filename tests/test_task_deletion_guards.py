import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database as db


class TaskDeletionGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in (("DB_DIR", root), ("DB_PATH", root / "task-deletion.db"),
                            ("BACKUP_DIR", root / "backups")):
            replacement = patch.object(db, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        db.init_db()

    def task(self, name):
        return db.create_task(name + "盘点", name, None, "南区")

    def case(self, task_id, status, source_type="ai", *, comparison=False):
        with db.get_conn() as conn:
            return conn.execute(
                """INSERT INTO ai_adjustment_tasks
                   (source_type,source_task_ids_json,comparison_json,status,created_at)
                   VALUES(?,?,?,?,?)""",
                (source_type, "[]" if comparison else json.dumps([task_id]),
                 json.dumps({"task_ids": [task_id]}) if comparison else "",
                 status, "2026-11-13T08:00:00"),
            ).lastrowid

    def row(self, table, row_id):
        with db.get_conn() as conn:
            return dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone())

    def assert_blocked_without_changes(self, task_id, case_id):
        task_before = self.row("tasks", task_id)
        case_before = self.row("ai_adjustment_tasks", case_id)
        with self.assertRaisesRegex(ValueError, "未闭环"):
            db.delete_task(task_id, "不能绕过整改单作废")
        self.assertEqual(self.row("tasks", task_id), task_before)
        self.assertEqual(self.row("ai_adjustment_tasks", case_id), case_before)

    def test_open_legacy_and_rule_cases_with_only_source_task_ids_block_deletion(self):
        for source_type in ("ai", "rule"):
            for status in ("pending_review", "dispatched", "pending_verify"):
                with self.subTest(source_type=source_type, status=status):
                    task_id = self.task(source_type + status)
                    case_id = self.case(task_id, status, source_type)
                    self.assert_blocked_without_changes(task_id, case_id)

    def test_comparison_task_link_still_blocks_deletion(self):
        task_id = self.task("三方比对机房")
        case_id = self.case(task_id, "dispatched", "rule", comparison=True)
        self.assert_blocked_without_changes(task_id, case_id)

    def test_closed_rejected_and_other_room_cases_do_not_block_deletion(self):
        task_id = self.task("待作废机房")
        other_task = self.task("其他机房")
        case_ids = [self.case(task_id, status) for status in ("done", "rejected")]
        case_ids.append(self.case(task_id, "done", "rule", comparison=True))
        case_ids.append(self.case(other_task, "pending_verify", "rule"))
        case_ids.append(self.case(other_task, "dispatched", "rule", comparison=True))
        cases_before = [self.row("ai_adjustment_tasks", case_id) for case_id in case_ids]
        other_before = self.row("tasks", other_task)

        self.assertTrue(db.delete_task(task_id, "清单重复"))

        deleted = self.row("tasks", task_id)
        self.assertEqual((deleted["is_deleted"], deleted["deleted_reason"]), (1, "清单重复"))
        self.assertTrue(deleted["deleted_at"])
        self.assertEqual(self.row("tasks", other_task), other_before)
        self.assertEqual([self.row("ai_adjustment_tasks", case_id) for case_id in case_ids], cases_before)


if __name__ == "__main__":
    unittest.main()
