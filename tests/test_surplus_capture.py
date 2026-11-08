import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl
from fastapi import HTTPException
from pydantic import ValidationError

from backend import database, main, three_way
from backend.models import ScanSubmitRequest


class SurplusCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_paths = database.DB_DIR, database.DB_PATH, database.BACKUP_DIR
        database.DB_DIR = Path(self.temp.name)
        database.DB_PATH = database.DB_DIR / "test.db"
        database.BACKUP_DIR = database.DB_DIR / "backups"
        database.init_db()
        self.worker = database.create_user("worker", "现场人员", "worker", "南区")
        self.other = database.create_user("other", "其他人员", "worker", "南区")
        self.admin = database.create_user("admin", "管理员", "admin", "南区")
        self.task_id = database.create_task("任务", "机房", self.worker["id"], "南区")
        database.batch_create_records(self.task_id, [{"device_name": "清单设备", "expected_model": "M1", "expected_barcode": "A1"}])
        self.normal_id = database.get_task_items(self.task_id)[0]["id"]
        self.surplus = database.create_surplus_record(self.task_id, "误录设备", "M2", "A2", False, "C2", False, "R2", "原备注", self.worker["id"])

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp.cleanup()

    def request(self, **values):
        payload = dict(record_id=self.surplus["id"], device_name="正确设备", actual_model="M3",
                       asset_barcodes="A3", cabinet_barcodes="C3", rack_no="R3", notes="更正备注")
        payload.update(values)
        return ScanSubmitRequest(**payload)

    def correct(self, record_id=None):
        database.create_record_correction(record_id or self.surplus["id"],
                                         dict(actual_model="管理员型号", asset_barcodes="ADMIN", cabinet_barcodes="ADMIN-CAB",
                                              rack_no="ADMIN-R", record_type="surplus" if record_id is None else "normal"),
                                         "修正误录", "", self.admin["id"])

    def set_task(self, **values):
        conn = database.get_conn()
        conn.execute("UPDATE tasks SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", (*values.values(), self.task_id))
        conn.commit()
        conn.close()

    def test_edit_overwrites_same_surplus_and_preserves_checklist_counts(self):
        response = main.api_scan(self.request(client_request_id="edit"), self.worker)
        self.assertEqual(response["record_id"], self.surplus["id"])
        self.assertEqual(response["record_type"], "surplus")
        current = database.get_record_by_id(self.surplus["id"])
        self.assertEqual((current["device_name"], current["actual_model"], current["asset_barcodes"], current["rack_no"], current["notes"]),
                         ("正确设备", "M3", "A3", "R3", "更正备注"))
        task = database.get_task_by_id(self.task_id)
        self.assertEqual((task["total"], task["scanned"]), (1, 0))
        self.assertEqual(len(database.get_task_items(self.task_id)), 2)
        cabinets = database.get_task_cabinet_data(self.task_id)["cabinets"]
        self.assertEqual([cabinet["cabinet_barcode"] for cabinet in cabinets], ["C3"])
        self.assertIn("正确设备", cabinets[0]["notes"])
        self.assertEqual(main.api_scan(self.request(client_request_id="edit"), self.worker), response)
        rename = [event for event in database.get_audit_logs() if event["action"] == "surplus.rename"]
        self.assertEqual(len(rename), 1)

    def test_edit_without_name_is_backward_compatible_and_rejects_invalid_names(self):
        main.api_scan(self.request(device_name=None), self.worker)
        self.assertEqual(database.get_record_by_id(self.surplus["id"])["device_name"], "误录设备")
        for name in ("", " "):
            with self.assertRaises(HTTPException):
                main.api_scan(self.request(device_name=name), self.worker)
        with self.assertRaises(ValidationError):
            self.request(device_name="长" * 501)

    def test_normal_cannot_be_renamed_or_deleted(self):
        with self.assertRaises(HTTPException):
            main.api_scan(self.request(record_id=self.normal_id), self.worker)
        with self.assertRaises(HTTPException):
            main.api_delete_task_surplus(self.task_id, self.normal_id, self.worker)
        self.assertEqual(database.get_record_by_id(self.normal_id)["device_name"], "清单设备")

    def test_delete_soft_voids_once_and_exits_readers_comparison_and_export(self):
        response = main.api_delete_task_surplus(self.task_id, self.surplus["id"], self.worker)
        self.assertTrue(response["deleted"])
        self.assertFalse(main.api_delete_task_surplus(self.task_id, self.surplus["id"], self.worker)["deleted"])
        raw = database.get_record_by_id(self.surplus["id"])
        self.assertTrue(raw["is_void"])
        self.assertEqual(raw["voided_by"], self.worker["id"])
        self.assertEqual(raw["device_name"], "误录设备")
        self.assertEqual([r["id"] for r in database.get_task_items(self.task_id)], [self.normal_id])
        self.assertEqual([r["id"] for r in database.get_report(self.task_id)], [self.normal_id])
        self.assertEqual(database.get_task_cabinet_data(self.task_id)["cabinets"], [])
        comparison = three_way.read([self.task_id])
        self.assertNotIn("误录设备", json.dumps(comparison, ensure_ascii=False))
        workbook = openpyxl.load_workbook(io.BytesIO(main.api_export(self.task_id, self.admin).body))
        exported = json.dumps({sheet.title: list(sheet.values) for sheet in workbook}, ensure_ascii=False)
        self.assertNotIn("误录设备", exported)
        self.assertNotIn("C2", exported)
        events = [event for event in database.get_audit_logs() if event["action"] == "surplus.delete"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["detail"]["before"]["device_name"], "误录设备")
        self.assertEqual(database.get_task_by_id(self.task_id)["total"], 1)
        with self.assertRaises(HTTPException):
            main.api_scan(self.request(), self.worker)

    def test_other_user_and_wrong_task_cannot_edit_or_delete(self):
        for operation in (lambda: main.api_scan(self.request(), self.other),
                          lambda: main.api_delete_task_surplus(self.task_id, self.surplus["id"], self.other)):
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 403)
        another_task = database.create_task("另一任务", "另一机房", self.worker["id"], "南区")
        with self.assertRaises(HTTPException):
            main.api_delete_task_surplus(another_task, self.surplus["id"], self.worker)
        self.assertFalse(database.get_record_by_id(self.surplus["id"])["is_void"])

    def test_completed_and_superseded_tasks_block_edit_and_delete_in_transaction(self):
        latest_id = database.create_task("新任务", "另一机房", self.worker["id"], "南区")
        for change in (dict(status="done"), dict(status="doing", superseded_by_task_id=latest_id)):
            self.set_task(**change)
            with self.assertRaises(HTTPException) as caught:
                main.api_scan(self.request(), self.worker)
            self.assertEqual(caught.exception.status_code, 409)
            with self.assertRaises(HTTPException) as caught:
                main.api_delete_task_surplus(self.task_id, self.surplus["id"], self.worker)
            self.assertEqual(caught.exception.status_code, 409)
            # Even if an HTTP precheck passed before completion, database writes recheck under the lock.
            with patch.object(main, "_require_task_write_access"):
                with self.assertRaises(HTTPException):
                    main.api_delete_task_surplus(self.task_id, self.surplus["id"], self.worker)
                with self.assertRaises(HTTPException):
                    main.api_scan(self.request(), self.worker)
        self.assertFalse(database.get_record_by_id(self.surplus["id"])["is_void"])

    def test_admin_correction_locks_surplus_and_normal_against_field_overwrite(self):
        self.correct()
        items = main.api_task_items(self.task_id, self.worker)["items"]
        self.assertTrue(next(item for item in items if item["id"] == self.surplus["id"])["write_locked"])
        with self.assertRaises(HTTPException):
            main.api_scan(self.request(), self.worker)
        with self.assertRaises(HTTPException):
            main.api_delete_task_surplus(self.task_id, self.surplus["id"], self.worker)
        main.api_scan(self.request(record_id=self.normal_id, device_name=None), self.worker)
        self.correct(self.normal_id)
        with self.assertRaises(HTTPException):
            main.api_scan(self.request(record_id=self.normal_id, device_name=None), self.worker)
        with self.assertRaisesRegex(ValueError, "管理员修正"):
            database.mark_record_missing(self.normal_id, "未找到", created_by=self.worker["id"])
        self.assertEqual(database.get_record_by_id(self.surplus["id"])["actual_model"], "管理员型号")

    def test_only_effective_correction_locks_and_public_route_is_allowed(self):
        self.correct()
        conn = database.get_conn()
        conn.execute("UPDATE scan_record_corrections SET source_scanned_at='old-scan' WHERE record_id=?", (self.surplus["id"],))
        conn.commit()
        conn.close()
        self.assertFalse(next(item for item in main.api_task_items(self.task_id, self.worker)["items"] if item["id"] == self.surplus["id"])["write_locked"])
        self.assertTrue(main.api_delete_task_surplus(self.task_id, self.surplus["id"], self.worker)["deleted"])
        self.assertTrue(main._is_field_or_public_api_path("DELETE", f"/api/tasks/{self.task_id}/surplus/{self.surplus['id']}"))
        self.assertFalse(main._is_field_or_public_api_path("DELETE", f"/api/admin/records/{self.surplus['id']}"))


if __name__ == "__main__":
    unittest.main()
