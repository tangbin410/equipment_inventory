import tempfile
import unittest
from pathlib import Path

from backend import database
from backend.reconciliation import build_reconciliation


class ReconciliationRuleTests(unittest.TestCase):
    def test_barcode_sets_must_match_but_order_does_not_matter(self):
        self.assertEqual(database.calc_asset_match_status("B,A", False, "A,B"), "一致")
        self.assertEqual(database.calc_asset_match_status("A", False, "A,B"), "不一致")

    def test_field_model_and_room_are_authoritative(self):
        tasks = [
            {
                "id": 1, "task_name": "A任务", "room_name": "A机房", "area_name": "南区",
                "status": "done", "field_completed_at": "2026-09-01T10:00:00",
                "records": [{
                    "id": 11, "device_name": "NE-1", "record_type": "missing", "match_status": "不一致",
                    "expected_model": "M1", "actual_model": "", "expected_barcode": "Z001",
                    "asset_barcodes": "", "asset_no_barcode": 0,
                }],
            },
            {
                "id": 2, "task_name": "B任务", "room_name": "B机房", "area_name": "南区",
                "status": "done", "field_completed_at": "2026-09-01T11:00:00",
                "records": [{
                    "id": 22, "device_name": "现场设备", "record_type": "surplus", "match_status": "不一致",
                    "expected_model": "", "actual_model": "M2", "expected_barcode": "",
                    "asset_barcodes": "z001", "asset_no_barcode": 0,
                }],
            },
        ]

        result = build_reconciliation(tasks)

        self.assertEqual([item["issue_type"] for item in result["issues"]], ["综资机房位置待整改"])
        issue = result["issues"][0]
        self.assertEqual(issue["source_value"], "A机房")
        self.assertEqual(issue["field_value"], "B机房")
        self.assertTrue(issue["can_create_adjustment"])

    def test_missing_record_is_not_declared_shortage(self):
        result = build_reconciliation([{
            "id": 1, "task_name": "任务", "room_name": "机房", "area_name": "北区",
            "status": "done", "field_completed_at": "2026-09-01T10:00:00",
            "records": [{
                "id": 1, "device_name": "NE", "record_type": "missing", "match_status": "不一致",
                "expected_model": "M", "expected_barcode": "A", "asset_barcodes": "",
            }],
        }])

        issue = result["issues"][0]
        self.assertEqual(issue["issue_type"], "待全批次完成后判断")
        self.assertFalse(issue["can_create_adjustment"])

    def test_model_and_rack_mismatch_are_reported(self):
        result = build_reconciliation([{
            "id": 1, "task_name": "任务", "room_name": "机房", "area_name": "北区",
            "status": "done", "field_completed_at": "2026-09-01T10:00:00",
            "records": [{
                "id": 1, "device_name": "NE", "record_type": "normal", "match_status": "一致",
                "expected_model": "M1", "actual_model": "M2", "expected_barcode": "A",
                "asset_barcodes": "A", "asset_no_barcode": 0,
                "expected_rack_no": "AC01A", "rack_no": "AC02A",
            }],
        }])

        self.assertEqual(
            [item["issue_type"] for item in result["issues"]],
            ["综资型号待整改", "综资设备机架号待整改"],
        )

    def test_cabinet_barcode_is_not_compared_without_source_baseline(self):
        result = build_reconciliation([{
            "id": 1, "task_name": "任务", "room_name": "机房", "area_name": "北区",
            "status": "done", "field_completed_at": "2026-09-01T10:00:00",
            "records": [{
                "id": 1, "device_name": "NE", "record_type": "normal", "match_status": "一致",
                "expected_model": "M1", "actual_model": "M1", "expected_barcode": "A",
                "asset_barcodes": "A", "asset_no_barcode": 0,
                "expected_rack_no": "AC01A", "rack_no": "AC01A",
                "cabinet_barcodes": "CAB-02", "cabinet_no_barcode": 0,
            }],
        }])

        self.assertEqual(result["summary"]["matched_count"], 1)
        self.assertFalse(result["issues"])


class AdjustmentVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()
        conn = database.get_conn()
        self.admin_id = conn.execute(
            "INSERT INTO users (wx_openid, name, role) VALUES ('admin', '管理员', 'admin')"
        ).lastrowid
        self.worker_id = conn.execute(
            "INSERT INTO users (wx_openid, name, role) VALUES ('worker', '现场人员', 'worker')"
        ).lastrowid
        self.adjustment_id = conn.execute(
            """INSERT INTO ai_adjustment_tasks
               (source_type, source_key, status, assigned_to, created_by, created_at, dispatched_at)
               VALUES ('rule', 'test:1', 'dispatched', ?, ?, datetime('now'), datetime('now'))""",
            (self.worker_id, self.admin_id),
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def test_field_submit_requires_admin_verification(self):
        submitted = database.complete_ai_adjustment_task(self.adjustment_id, self.worker_id, "已修改")
        self.assertEqual(submitted["status"], "pending_verify")

        verified = database.verify_ai_adjustment_task(self.adjustment_id, self.admin_id, "验收通过")
        self.assertEqual(verified["status"], "done")
        self.assertEqual(verified["verification_note"], "验收通过")
        self.assertTrue(verified["verified_at"])


if __name__ == "__main__":
    unittest.main()
