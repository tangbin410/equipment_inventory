import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import database
from backend.main import api_ai_analyze
from backend.models import AIAnalysisRequest


class AIAnalysisVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()
        conn = database.get_conn()
        self.task_id = conn.execute(
            """INSERT INTO tasks
               (task_name, room_name, status, created_at, completed_at, field_completed_at)
               VALUES ('测试任务', '测试机房', 'done', '2026-08-01T08:00:00',
                       '2026-08-01T10:00:00', '2026-08-01T10:00:00')"""
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def save_success(self, completed_at):
        return database.save_ai_analysis_result(
            task_ids=[self.task_id],
            analysis_scope="测试机房",
            model_provider="test",
            model_name="test-model",
            prompt_version="test",
            input_summary="",
            desensitized_payload="{}",
            context_meta="{}",
            analysis_result="[]",
            status="success",
            error_message="",
            created_by=None,
            task_completion_versions=[(self.task_id, completed_at)],
        )

    def test_same_completion_time_cannot_generate_twice(self):
        completed_at = "2026-08-01T10:00:00"
        first_id = self.save_success(completed_at)

        with self.assertRaisesRegex(ValueError, "已经生成过"):
            self.save_success(completed_at)

        versions = database.list_analyzed_ai_task_versions([(self.task_id, completed_at)])
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["analysis_id"], first_id)

    def test_new_completion_time_can_generate_again(self):
        first_completed_at = "2026-08-01T10:00:00"
        second_completed_at = "2026-08-02T10:00:00"
        self.save_success(first_completed_at)
        second_id = self.save_success(second_completed_at)

        versions = database.list_analyzed_ai_task_versions([
            (self.task_id, first_completed_at),
            (self.task_id, second_completed_at),
        ])
        self.assertEqual(len(versions), 2)
        self.assertIn(second_id, {item["analysis_id"] for item in versions})

    def test_unfinished_task_is_rejected_before_model_call(self):
        task = {
            "id": self.task_id,
            "room_name": "测试机房",
            "status": "doing",
            "field_completed_at": None,
        }
        with patch("backend.main._ai_status_payload", return_value={"enabled": True, "available": True}), \
             patch("backend.main._get_task_or_404", return_value=task), \
             patch("backend.main.get_report", return_value=[]), \
             patch("backend.main.analyze_inventory") as analyze:
            with self.assertRaises(HTTPException) as caught:
                api_ai_analyze(AIAnalysisRequest(task_ids=[self.task_id]), {"id": 1})

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("只能分析现场已完成", caught.exception.detail)
        analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
