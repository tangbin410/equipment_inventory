import tempfile
import unittest
from pathlib import Path

from backend import database


class RecordCorrectionTests(unittest.TestCase):
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
        self.task_id = conn.execute(
            """INSERT INTO tasks (task_name, room_name, status, created_at, field_completed_at)
               VALUES ('任务', '机房', 'done', '2026-09-01T08:00:00', '2026-09-01T10:00:00')"""
        ).lastrowid
        self.record_id = conn.execute(
            """INSERT INTO scan_records
               (task_id, device_name, expected_model, expected_barcode, actual_model,
                asset_barcodes, cabinet_barcodes, rack_no, notes, match_status, scanned_at)
               VALUES (?, 'NE-1', 'MODEL-A', 'A001,A002', 'MODEL-X',
                       'TEST', 'CAB-1', 'R1', '原始备注', '不一致', '2026-09-01T09:00:00')""",
            (self.task_id,),
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def values(self):
        return {
            "actual_model": "MODEL-A",
            "asset_barcodes": "A002,A001",
            "asset_no_barcode": False,
            "cabinet_barcodes": "CAB-1",
            "cabinet_no_barcode": False,
            "rack_no": "R1",
            "notes": "管理员依据照片修正",
            "record_type": "normal",
            "missing_reason": "",
        }

    def test_surplus_name_correction_and_void_preserve_history_and_exit_all_readers(self):
        conn = database.get_conn()
        surplus_id = conn.execute(
            """INSERT INTO scan_records(task_id,device_name,expected_model,record_type,actual_model,
               asset_barcodes,cabinet_barcodes,rack_no,match_status,scanned_at)
               VALUES(?,'误录名称','','surplus','M','SURPLUS','SURPLUS-CAB','S01','不一致','2026-09-01T09:30:00')""",
            (self.task_id,),
        ).lastrowid
        conn.commit()
        conn.close()
        corrected = database.create_record_correction(surplus_id, {**self.values(), "device_name": "正确名称", "record_type": "surplus", "asset_barcodes": "SURPLUS", "cabinet_barcodes": "SURPLUS-CAB", "rack_no": "S01"}, "误录更正", "", self.admin_id)
        self.assertEqual(corrected["device_name"], "正确名称")
        self.assertTrue(corrected["device_name_corrected_at"])
        self.assertTrue(any(c["cabinet_barcode"] == "SURPLUS-CAB" for c in database.get_task_cabinet_data(self.task_id)["cabinets"]))
        self.assertTrue(database.void_capture_record("scan_records", surplus_id, self.admin_id, "重复登记"))
        self.assertFalse(database.void_capture_record("scan_records", surplus_id, self.admin_id, "重复登记"))
        self.assertNotIn(surplus_id, [r["id"] for r in database.get_task_items(self.task_id)])
        self.assertNotIn(surplus_id, [r["id"] for r in database.get_report(self.task_id)])
        self.assertFalse(any(c["cabinet_barcode"] == "SURPLUS-CAB" for c in database.get_task_cabinet_data(self.task_id)["cabinets"]))
        self.assertTrue(database.get_record_by_id(surplus_id)["is_void"])
        actions = [a["action"] for a in database.get_audit_logs()]
        self.assertIn("surplus.rename", actions)
        self.assertIn("surplus.void", actions)
        with self.assertRaisesRegex(ValueError, "已作废"):
            database.create_record_correction(surplus_id, self.values(), "再次更正", "", self.admin_id)
        # 数据库升级重复执行后，作废数据不能重新出现。
        database.init_db()
        self.assertTrue(database.get_record_by_id(surplus_id)["is_void"])
        self.assertTrue(database.get_record_by_id(surplus_id)["device_name_corrected_at"])

    def test_listed_device_cannot_be_renamed_or_voided(self):
        with self.assertRaisesRegex(ValueError, "不能修改综资清单设备名称"):
            database.create_record_correction(self.record_id, {**self.values(), "device_name": "另一个设备"}, "误录更正", "", self.admin_id)
        with self.assertRaisesRegex(ValueError, "只能作废现场新增"):
            database.void_capture_record("scan_records", self.record_id, self.admin_id, "误操作")

    def test_correction_preserves_original_and_changes_effective_values(self):
        corrected = database.create_record_correction(
            self.record_id,
            self.values(),
            "根据现场凭证修正",
            "照片可确认型号和条码",
            self.admin_id,
        )

        self.assertEqual(corrected["match_status"], "一致")
        self.assertEqual(corrected["asset_match_status"], "一致")
        self.assertEqual(corrected["model_match_status"], "一致")
        self.assertEqual(corrected["original_actual_model"], "MODEL-X")
        self.assertEqual(corrected["actual_model"], "MODEL-A")
        self.assertTrue(corrected["has_admin_correction"])

        conn = database.get_conn()
        raw = conn.execute(
            "SELECT actual_model, asset_barcodes, notes FROM scan_records WHERE id = ?",
            (self.record_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(tuple(raw), ("MODEL-X", "TEST", "原始备注"))

    def test_new_field_submission_cannot_override_admin_correction(self):
        # 管理员修正后，进行中任务的现场表单也不能覆盖有效修正。
        conn = database.get_conn()
        conn.execute("UPDATE tasks SET status = 'doing', field_completed_at = NULL WHERE id = ?", (self.task_id,))
        conn.commit()
        conn.close()
        database.create_record_correction(
            self.record_id, self.values(), "条码录入错误", "", self.admin_id
        )
        with self.assertRaisesRegex(ValueError, "管理员修正"):
            database.submit_scan(
                self.record_id, "NEW-CODE", False, "CAB-2", False,
                "R2", "MODEL-NEW", "现场重新提交",
            )

        current = database.get_record_by_id(self.record_id)
        self.assertTrue(current["has_admin_correction"])
        self.assertEqual(current["actual_model"], "MODEL-A")
        self.assertEqual(current["asset_barcodes"], "A001,A002")

    def test_missing_record_can_be_restored_to_normal(self):
        conn = database.get_conn()
        conn.execute(
            """UPDATE scan_records
               SET record_type = 'missing', field_status = '盘亏', missing_reason = '未找到'
               WHERE id = ?""",
            (self.record_id,),
        )
        conn.commit()
        conn.close()

        corrected = database.create_record_correction(
            self.record_id, self.values(), "根据现场凭证修正", "现场照片确认设备存在", self.admin_id
        )
        self.assertEqual(corrected["record_type"], "normal")
        self.assertEqual(corrected["field_status"], "正常")
        self.assertEqual(corrected["missing_reason"], "")

    def test_closed_adjustment_blocks_direct_correction(self):
        conn = database.get_conn()
        conn.execute(
            """INSERT INTO ai_adjustment_tasks
               (source_type, source_key, problem_type, status, created_at)
               VALUES ('rule', ?, '测试问题', 'done', datetime('now'))""",
            (f"{self.record_id}:model",),
        )
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(ValueError, "已闭环"):
            database.create_record_correction(
                self.record_id, self.values(), "型号录入错误", "", self.admin_id
            )


if __name__ == "__main__":
    unittest.main()
