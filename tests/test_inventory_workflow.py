import asyncio
import io
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl
from fastapi import HTTPException

from backend import database, main
from backend.ai_agent import build_inventory_rows, desensitize_rows
from backend.models import AdminRecordCorrectionRequest, ManualRecordAdjustmentRequest, ScanSubmitRequest
from backend.reconciliation import build_reconciliation
from local_agent.rules import run_rules


class InventoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_paths = database.DB_DIR, database.DB_PATH, database.BACKUP_DIR
        database.DB_DIR = Path(self.temp.name)
        database.DB_PATH = database.DB_DIR / "test.db"
        database.BACKUP_DIR = database.DB_DIR / "backups"
        database.init_db()
        self.worker = database.create_user("test-worker", "现场人员", "worker", "南区")
        self.admin = database.create_user("test-admin", "管理员", "admin", "南区")
        self.task_id = database.create_task("测试任务", "测试机房", self.worker["id"], "南区")
        database.batch_create_records(self.task_id, [{"device_name": "设备", "expected_model": "M1", "expected_barcode": "A1"}])
        self.record_id = database.get_task_items(self.task_id)[0]["id"]

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp.cleanup()

    def scan(self, record_id=None, **values):
        payload = dict(record_id=record_id or self.record_id, asset_barcodes="A1", asset_no_barcode=False,
                       cabinet_barcodes="C1", cabinet_no_barcode=False, rack_no="R1", actual_model="M1",
                       notes="", created_by=self.worker["id"])
        payload.update(values)
        return database.submit_scan(**payload)

    def finish(self):
        return database.complete_inventory_task(self.task_id, self.worker["id"])

    def test_last_scan_stays_editable_until_manual_confirmation(self):
        self.scan()
        self.assertEqual(database.get_task_by_id(self.task_id)["status"], "doing")
        database.create_cabinet(self.task_id, "odf", "ODF1", False, "R2", "", self.worker["id"])
        self.finish()
        task = database.get_task_by_id(self.task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["completed_at"], task["field_completed_at"])
        self.assertTrue(task["field_completed_at"])

    def test_unscanned_and_empty_tasks_cannot_complete(self):
        with self.assertRaisesRegex(ValueError, "尚未全部处理"):
            self.finish()
        empty_id = database.create_task("空任务", "空机房", self.worker["id"], "南区")
        with self.assertRaisesRegex(ValueError, "尚未全部处理"):
            database.complete_inventory_task(empty_id, self.worker["id"])

    def test_completed_task_rejects_all_field_writes(self):
        self.scan()
        self.finish()
        writes = [self.scan,
                  lambda: database.mark_record_missing(self.record_id, "未找到", created_by=self.worker["id"]),
                  lambda: database.create_cabinet(self.task_id, "odf", "NEW", False, "R2", "", self.worker["id"]),
                  lambda: database.create_surplus_record(self.task_id, "新设备", "M2", "A2", False, "C2", False, "R2", "", self.worker["id"])]
        for write in writes:
            with self.subTest(write=write), self.assertRaisesRegex(ValueError, "锁定"):
                write()
        self.assertEqual(database.get_record_by_id(self.record_id)["record_type"], "normal")
        self.assertEqual(database.get_task_by_id(self.task_id)["total"], 1)

    def test_my_tasks_excludes_other_users_completed_rooms(self):
        self.scan()
        self.finish()
        own_active = database.create_task("本人进行中", "另一机房", self.worker["id"], "南区")
        other = database.create_user("other-worker", "同片区人员", "worker", "南区")
        other_task = database.create_task("他人已完成", "他人机房", other["id"], "南区")
        database.batch_create_records(other_task, [{"device_name": "其他设备", "expected_model": "M1", "expected_barcode": "A1"}])
        other_record = database.get_task_items(other_task)[0]["id"]
        self.scan(other_record, created_by=other["id"])
        database.complete_inventory_task(other_task, other["id"])

        own_tasks = main.api_my_tasks(self.worker)
        self.assertEqual({task["id"] for task in own_tasks}, {self.task_id, own_active})
        self.assertEqual({task["id"] for task in own_tasks if task["status"] == "done"}, {self.task_id})
        self.assertEqual([task["id"] for task in main.api_my_tasks(other)], [other_task])

    def test_completion_is_idempotent_and_only_logs_once_under_concurrency(self):
        self.scan()
        barrier = Barrier(2)
        def complete():
            barrier.wait()
            return self.finish()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: complete(), range(2)))
        self.assertEqual(sorted(result["already_completed"] for result in results), [False, True])
        logs = [log for log in database.get_audit_logs() if log["action"] == "task.complete"]
        self.assertEqual(len(logs), 1)

    def test_completion_rejects_other_workers(self):
        self.scan()
        other = database.create_user("other", "其他人员", "worker", "南区")
        with self.assertRaisesRegex(ValueError, "本人"):
            database.complete_inventory_task(self.task_id, other["id"])

    def test_successful_scan_retry_still_works_after_completion(self):
        request = ScanSubmitRequest(record_id=self.record_id, client_request_id="scan-retry", asset_barcodes="A1",
                                    cabinet_barcodes="C1", rack_no="R1", actual_model="M1")
        response = main.api_scan(request, self.worker)
        self.finish()
        self.assertEqual(main.api_scan(request, self.worker), response)
        with self.assertRaises(HTTPException) as caught:
            main.api_scan(request.model_copy(update={"client_request_id": "new-write"}), self.worker)
        self.assertEqual(caught.exception.status_code, 409)

    def test_surplus_resubmit_keeps_origin_and_denominator(self):
        surplus = database.create_surplus_record(self.task_id, "新增设备", "M2", "A2", False, "C2", False, "R2", "", self.worker["id"])
        result = self.scan(surplus["id"], actual_model="M3", asset_barcodes="A3")
        self.assertEqual(result["record_type"], "surplus")
        self.assertEqual(result["asset_match_status"], "盘盈")
        self.assertEqual(database.get_task_by_id(self.task_id)["total"], 1)
        self.assertEqual(database.get_task_by_id(self.task_id)["scanned"], 0)

    def test_no_barcode_clears_conflicting_codes_and_does_not_register_them(self):
        result = self.scan(asset_no_barcode=True, cabinet_no_barcode=True, asset_barcodes="OLD", cabinet_barcodes="OLD-CAB")
        self.assertEqual(result["asset_barcodes"], "")
        self.assertEqual(result["cabinet_barcodes"], "")
        cabinets = database.get_task_cabinets(self.task_id)
        self.assertEqual(len(cabinets), 1)
        self.assertTrue(cabinets[0]["no_barcode"])

    def test_scan_and_cabinet_registration_roll_back_together(self):
        with patch("backend.database._register_scan_cabinets", side_effect=ValueError("登记失败")):
            with self.assertRaisesRegex(ValueError, "登记失败"):
                self.scan()
        self.assertEqual(database.get_record_by_id(self.record_id)["match_status"], "未扫")
        self.assertEqual(database.get_task_cabinets(self.task_id), [])

    def test_missing_clears_stale_scan_fields(self):
        self.scan()
        result = database.mark_record_missing(self.record_id, "未找到", created_by=self.worker["id"])
        for field in ("asset_barcodes", "cabinet_barcodes", "actual_model", "rack_no"):
            self.assertEqual(result[field], "")
        self.assertEqual(result["created_by"], self.worker["id"])

    def test_admin_correction_after_completion_preserves_raw_result(self):
        self.scan(actual_model="WRONG")
        self.finish()
        payload = AdminRecordCorrectionRequest(actual_model="M1", asset_barcodes="A1", cabinet_barcodes="C1", rack_no="R1", correction_reason="型号录入错误")
        response = main.api_create_record_correction(self.record_id, payload, self.admin)
        self.assertEqual(response["record"]["actual_model"], "M1")
        self.assertEqual(response["record"]["original_actual_model"], "WRONG")
        self.assertEqual(database.get_task_by_id(self.task_id)["status"], "done")

    def test_free_text_and_not_applicable_models_can_be_saved_then_corrected(self):
        for model in ("现场自定义型号-Z9/2026", "不涉及"):
            with self.subTest(model=model):
                self.scan(actual_model=model)
                self.assertEqual(database.get_task_items(self.task_id)[0]["actual_model"], model)
                self.assertEqual(database.get_report(self.task_id)[0]["actual_model"], model)
        self.finish()
        payload = AdminRecordCorrectionRequest(actual_model="管理员核实型号", asset_barcodes="A1",
            cabinet_barcodes="C1", rack_no="R1", correction_reason="根据现场凭证修正")
        response = main.api_create_record_correction(self.record_id, payload, self.admin)
        self.assertEqual(response["record"]["actual_model"], "管理员核实型号")
        self.assertEqual(database.get_task_items(self.task_id)[0]["actual_model"], "管理员核实型号")
        self.assertEqual(database.get_task_by_id(self.task_id)["status"], "done")

    def test_serious_problem_can_enter_manual_adjustment_without_ai(self):
        database.mark_record_missing(self.record_id, "疑似资产丢失", created_by=self.worker["id"])
        self.finish()
        from backend import three_way
        three_way.set_room_code(self.task_id, "001", self.admin["id"])
        database.save_asset_info_snapshot(file_name="资产.xlsx", sheet_name="资产", barcode_column="资产条码",
            headers=["资产条码", "资源名称", "地点编码", "规格型号"],
            records=[{"barcode":"A1", "fields":{"资产条码":"A1", "资源名称":"设备", "地点编码":"001", "规格型号":"M1"}}],
            imported_by=self.admin["id"], imported_by_name="管理员")
        payload = ManualRecordAdjustmentRequest(problem_type="资产去向待调查", basis="管理员确认需调查", suggestion="核查调拨和拆除手续")
        result = main.api_manual_record_adjustment(self.record_id, payload, self.admin)
        self.assertEqual(result["item"]["source_type"], "manual")
        self.assertEqual(result["item"]["status"], "pending_review")
        self.assertIsNotNone(result["item"]["comparison"])
        with self.assertRaises(HTTPException) as caught:
            main.api_manual_record_adjustment(self.record_id, payload, self.admin)
        self.assertEqual(caught.exception.status_code, 409)
        item_id = result["item"]["id"]
        three_way.case_action(item_id,"approve",self.admin["id"],dict(note="需现场核实",needs_field=True,assigned_to=self.worker["id"]))
        database.complete_ai_adjustment_task(item_id, self.worker["id"], "已完成调查")
        with self.assertRaisesRegex(ValueError, "仍有问题"):
            database.verify_ai_adjustment_task(item_id, self.admin["id"], "不能只凭调查说明闭环")

    def test_admin_correction_does_not_accept_incomplete_surplus(self):
        surplus = database.create_surplus_record(self.task_id, "新增设备", "M2", "A2", False, "C2", False, "R2", "", self.worker["id"])
        with self.assertRaisesRegex(ValueError, "型号"):
            database.create_record_correction(surplus["id"], {"record_type": "surplus"}, "修正", "", self.admin["id"])

    def test_released_or_deleted_task_cannot_be_modified(self):
        database.release_task(self.task_id, self.worker["id"])
        with self.assertRaisesRegex(ValueError, "尚未领取"):
            self.scan()
        database.claim_task(self.task_id, self.worker["id"])
        database.delete_task(self.task_id, "无效清单")
        with self.assertRaisesRegex(ValueError, "作废"):
            self.scan()

    def test_assignment_cannot_bypass_area_or_completed_lock(self):
        pending_id = database.create_task("待领取", "机房2", None, "南区")
        northern = database.create_user("north", "北区人员", "worker", "北区")
        with self.assertRaisesRegex(ValueError, "片区"):
            database.assign_task(pending_id, northern["id"])
        self.scan()
        self.finish()
        with self.assertRaisesRegex(ValueError, "锁定"):
            database.assign_task(self.task_id, self.worker["id"])

    def test_import_is_atomic_and_each_upload_has_its_own_batch(self):
        rooms = {name: [{"_area_name": "南区", "room_code": name, "device_name": name, "expected_model": "M", "expected_barcode": name}]
                 for name in ("A机房", "B机房")}
        batch, created = database.import_room_tasks(rooms)
        self.assertEqual({item["import_batch_id"] for item in created}, {batch})
        second_batch, _ = database.import_room_tasks(rooms)
        self.assertNotEqual(batch, second_batch)
        before = len(database.get_all_tasks())
        rooms["B机房"][0]["expected_model"] = None
        with self.assertRaises(sqlite3.IntegrityError):
            database.import_room_tasks(rooms)
        self.assertEqual(len(database.get_all_tasks()), before)

    def test_selecting_part_of_batch_does_not_claim_batch_completion(self):
        rooms = {name: [{"_area_name": "南区", "room_code": name, "device_name": name, "expected_model": "M", "expected_barcode": name}]
                 for name in ("A机房", "B机房")}
        _, created = database.import_room_tasks(rooms)
        conn = database.get_conn()
        conn.execute("UPDATE tasks SET status = 'done', field_completed_at = '2026-10-15T10:00:00' WHERE import_batch_id = ?", (created[0]["import_batch_id"],))
        conn.commit()
        conn.close()
        result = main.api_reconciliation([created[0]["task_id"]], self.admin)
        self.assertFalse(result["summary"]["batch_complete"])

    def test_last_password_admin_is_protected_through_all_account_paths(self):
        database.set_password_hash(self.admin["id"], "test-hash")
        writes = [lambda: database.delete_user(self.admin["id"]),
                  lambda: database.update_user(self.admin["id"], {"is_active": False}),
                  lambda: database.update_user(self.admin["id"], {"role": "worker"}),
                  lambda: database.create_user("test-admin", "管理员", "worker", "南区")]
        for write in writes:
            with self.subTest(write=write), self.assertRaisesRegex(ValueError, "最后一个"):
                write()
        other = database.create_user("admin-2", "备用管理员", "admin", "南区")
        with self.assertRaisesRegex(ValueError, "最后一个"):
            database.delete_user(self.admin["id"])
        database.set_password_hash(other["id"], "other-hash")
        self.assertTrue(database.delete_user(self.admin["id"]))

    def test_null_role_cannot_corrupt_user_account(self):
        with self.assertRaisesRegex(ValueError, "null"):
            database.update_user(self.worker["id"], {"role": None})
        self.assertEqual(database.get_user_by_id(self.worker["id"])["role"], "worker")

    def test_concurrent_rule_creation_generates_only_one_adjustment(self):
        issue = {"issue_key": "unique-key", "task_id": self.task_id, "area_name": "南区"}
        barrier = Barrier(2)
        def create():
            barrier.wait()
            try:
                return database.create_rule_adjustment(issue, self.admin["id"])
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: create(), range(2)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(len(database.list_ai_adjustment_tasks()), 1)

    def test_dispatch_and_reject_do_not_overwrite_each_other(self):
        item = database.create_rule_adjustment({"issue_key": "review-race", "task_id": self.task_id, "area_name": "南区"}, self.admin["id"])
        barrier = Barrier(2)
        def review(dispatch):
            barrier.wait()
            try:
                if dispatch:
                    return database.dispatch_ai_adjustment_task(item["id"], self.worker["id"], self.admin["id"])
                return database.reject_ai_adjustment_task(item["id"], self.admin["id"])
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(review, [True, False]))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(database.list_ai_adjustment_tasks()[0]["status"], winners[0]["status"])


class ImportAndAccessTests(unittest.TestCase):
    def sheet(self, values):
        sheet = openpyxl.Workbook().active
        sheet.append(main.TASK_TEMPLATE_HEADERS)
        for row in values:
            sheet.append(row)
        return sheet

    def test_nonempty_rows_with_missing_identity_are_not_silently_dropped(self):
        for missing_index, expected in [(0, "机房名称"), (8, "地点编码"), (3, "网元名称")]:
            row = ["机房", "南区", "PTN", "设备", "M1", "A1", "R1", "在网", "001"]
            row[missing_index] = ""
            with self.subTest(field=expected), self.assertRaisesRegex(HTTPException, expected):
                main._parse_excel_devices(self.sheet([row]))

    def test_normalized_and_overlapping_barcode_sets_are_detected_in_preview(self):
        rooms = main._parse_excel_devices(self.sheet([
            ["机房", "南区", "PTN", "设备1", "M", "a1，A2", "R1", "在网", "001"],
            ["机房", "南区", "PTN", "设备2", "M", "a2", "R2", "在网", "001"],
        ]))
        preview = main._preview_rooms(rooms)
        self.assertFalse(preview["valid"])
        self.assertEqual(preview["duplicate_barcodes"], ["A2"])

    def test_duplicated_headers_are_rejected(self):
        sheet = self.sheet([])
        sheet.cell(1, 10, "资产标签号")
        with self.assertRaisesRegex(HTTPException, "重复表头"):
            main._parse_excel_devices(sheet)

    def test_masked_ai_codes_follow_same_comparison_rules(self):
        rows, _ = desensitize_rows([{"清单资产条码": "a2，a1", "现场资产条码": "A1,A2"}])
        self.assertEqual(rows[0]["清单资产条码"], rows[0]["现场资产条码"])

    def test_export_does_not_call_unscanned_device_normal(self):
        rows = build_inventory_rows([{"records": [{"match_status": "未扫", "record_type": "normal"}]}])
        self.assertEqual(rows[0]["现场状态"], "未盘点")

    def test_admin_allowlist_checks_all_entries_and_denies_empty_list(self):
        request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.8"), headers={}, url=SimpleNamespace(hostname="127.0.0.1"))
        settings = {"INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "1", "INVENTORY_ADMIN_ALLOWED_IPS": "127.0.0.1,203.0.113.0/24"}
        with patch.dict(os.environ, settings):
            main._check_admin_source_allowed(request)
            with patch.dict(os.environ, {"INVENTORY_ADMIN_ALLOWED_IPS": ""}):
                with self.assertRaises(HTTPException):
                    main._check_admin_source_allowed(request)

    def test_dev_login_rejects_remote_sources_and_production(self):
        with patch.dict(os.environ, {"INVENTORY_ENV": "dev"}):
            with self.assertRaises(HTTPException):
                main._check_dev_login_source(SimpleNamespace(client=SimpleNamespace(host="203.0.113.8"), headers={}))
        with patch.dict(os.environ, {"INVENTORY_ENV": "production"}):
            with self.assertRaises(HTTPException):
                main._check_dev_login_source(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}))

    def test_excel_export_preserves_formula_like_notes_as_text(self):
        workbook = openpyxl.Workbook()
        workbook.active.append(["备注", "条码"])
        workbook.active.append(['=HYPERLINK("https://example.invalid", "说明")', "00123"])
        response = main._excel_response(workbook, "test.xlsx")
        exported = openpyxl.load_workbook(io.BytesIO(response.body))
        self.assertEqual(exported.active["A2"].data_type, "s")
        self.assertEqual(exported.active["B2"].value, "00123")

    def test_historical_batches_do_not_generate_duplicate_or_location_findings(self):
        tasks = [{"id": n, "import_batch_id": f"batch-{n}", "room_name": f"机房{n}", "status": "done",
                  "field_completed_at": "2026-10-15T10:00:00", "records": [{"id": n, "device_name": f"设备{n}",
                  "record_type": "normal", "match_status": "一致", "expected_barcode": "A", "asset_barcodes": "A",
                  "expected_model": "M", "actual_model": "M"}]} for n in (1, 2)]
        result = build_reconciliation(tasks)
        self.assertEqual(result["issues"], [])
        rows = [{"导入批次": f"batch-{n}", "机房名称": f"机房{n}", "设备名称": f"设备{n}",
                 "现场资产条码": "A", "盘点时间": "已盘", "现场设备型号": "M", "现场机柜条码": "C"} for n in (1, 2)]
        self.assertFalse(any(item["问题类型"] == "同一资产条码重复" for item in run_rules(rows)))


class PublicFieldApiTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, method, path):
        sent = []
        first = True
        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"{}", "more_body": False}
            await asyncio.Event().wait()
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
                 "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": [(b"content-type", b"application/json")],
                 "client": ("203.0.113.8", 12345), "server": ("localhost", 8000)}
        with patch.dict(os.environ, {"INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "1", "INVENTORY_ADMIN_ALLOWED_IPS": "127.0.0.1,::1"}):
            await asyncio.wait_for(main.app(scope, receive, send), timeout=5)
        return next(message["status"] for message in sent if message["type"] == "http.response.start")

    async def test_field_routes_reach_authentication_from_public_network(self):
        for method, path in [("GET", "/api/me/profile"), ("POST", "/api/cabinets"), ("POST", "/api/tasks/1/complete")]:
            with self.subTest(path=path):
                self.assertEqual(await self.request(method, path), 401)

    async def test_admin_routes_stay_hidden_from_public_network(self):
        self.assertEqual(await self.request("GET", "/api/users"), 404)


if __name__ == "__main__":
    unittest.main()
