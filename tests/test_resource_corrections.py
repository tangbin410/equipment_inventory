"""综资单设备修正的隔离测试；所有写入均限于临时数据库。"""

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from backend import database as db, main, three_way as tw
from backend.auth import create_access_token, hash_password


class ResourceCorrectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / "resource-tests.db"
        db.BACKUP_DIR = db.DB_DIR / "backups"
        self.env = patch.dict(os.environ, {
            "INVENTORY_SECRET_KEY": "isolated-resource-test-key-1234567890",
            "INVENTORY_ENV": "development", "INVENTORY_DEV_LOGIN": "1",
            "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "1",
            "INVENTORY_ADMIN_ALLOWED_IPS": "127.0.0.1,::1",
            "INVENTORY_TRUSTED_PROXY_IPS": "127.0.0.1,::1",
            "INVENTORY_LOGIN_RATE_LIMIT_ENABLED": "0",
        })
        self.env.start()
        db.init_db()
        password = hash_password("isolated-resource-password-12345")
        self.admin = db.create_user("resource-admin", "管理员", "admin", "南区", admin_scope="full", password_hash=password)
        self.collaborator = db.create_user("resource-collaborator", "协作管理员", "admin", "南区", admin_scope="collaborator", password_hash=password)
        self.worker = db.create_user("resource-worker", "现场人员", "worker", "南区")
        self.task = db.create_task("综资编辑测试", "机房甲", self.worker["id"], "南区")
        self.sql("UPDATE tasks SET import_batch_id='resource-batch',room_code='001' WHERE id=?", (self.task,))
        db.batch_create_records(self.task, [
            dict(device_name="网元甲", expected_model="M1", expected_barcode="A1", device_extra={"设备机架": "R1", "设备类别": "OTN"}),
            dict(device_name="网元乙", expected_model="M2", expected_barcode="B1", device_extra={"设备机架": "R2"}),
        ])
        records = db.get_task_items(self.task)
        self.record, self.other = records[0]["id"], records[1]["id"]
        db.submit_scan(self.record, "A1", False, "C1", False, "R1", "M1", "现场原始备注", created_by=self.worker["id"])
        db.submit_scan(self.other, "B1", False, "C2", False, "R2", "M2", "另一台备注", created_by=self.worker["id"])
        db.complete_inventory_task(self.task, self.worker["id"])
        db.save_asset_info_snapshot(
            file_name="资产.xlsx", sheet_name="资产", barcode_column="资产条码",
            headers=["资产条码", "资源名称", "地点编码", "规格型号"],
            records=[{"barcode": code, "fields": {"资产条码": code, "资源名称": name, "地点编码": "001", "规格型号": model}}
                     for code, name, model in [("A1", "网元甲", "M1"), ("B1", "网元乙", "M2")]],
            imported_by=self.admin["id"], imported_by_name=self.admin["name"],
        )

    def tearDown(self):
        self.env.stop()
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def sql(self, statement, values=()):
        with db.get_conn() as conn:
            return conn.execute(statement, values).lastrowid

    def raw(self, record_id):
        with db.get_conn() as conn:
            return dict(conn.execute("SELECT * FROM scan_records WHERE id=?", (record_id,)).fetchone())

    def baseline(self):
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM resource_baselines WHERE task_id=? ORDER BY id DESC LIMIT 1", (self.task,)).fetchone()
            return (row["id"], json.loads(row["rows_json"])) if row else (0, [])

    def values(self, **changes):
        return {"expected_model": "新综资型号", "expected_barcode": "NEW-A1,NEW-A2", "expected_rack_no": "NEW-R1", **changes}

    def save(self, record_id=None, values=None, fingerprint=None):
        record_id = record_id or self.record
        current = tw.resource_record(record_id)
        return tw.update_resource_record(record_id, values or self.values(), fingerprint or current["fingerprint"], self.admin["id"], note="管理员核实综资")

    def source(self, record_id):
        return next(row for group in tw.read([self.task])["groups"] for row in tw.source_records(group, "综资")
                    if row.get("record_id") == record_id)

    def workbook_rows(self, response):
        rows = list(openpyxl.load_workbook(io.BytesIO(response.body)).active.values)
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def test_edit_reaches_report_summary_collection_and_both_exports_without_changing_field_data(self):
        before, other_before = self.raw(self.record), self.raw(self.other)
        values = self.values()
        self.save(values=values)
        self.assertEqual(self.raw(self.record), before, "原始导入与现场记录保留，读取当前综资基线")
        self.assertEqual(self.raw(self.other), other_before, "另一台设备不能被单行编辑覆盖")
        self.assertEqual(db.get_record_by_id(self.record)["expected_model"], before["expected_model"], "内部原始记录查询保留导入语义")
        for record in [db.get_report(self.task)[0], db.get_task_items(self.task)[0]]:
            for key, value in values.items():
                self.assertEqual(record[key], value)
            self.assertEqual(record["actual_model"], "M1")
            self.assertEqual(record["asset_barcodes"], "A1")
            self.assertEqual(record["rack_no"], "R1")
            self.assertEqual(record["notes"], "现场原始备注")
        summary = next(task for task in main.api_summary(self.admin) if task["id"] == self.task)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["scanned"], 2)
        for key, value in values.items():
            self.assertEqual(summary["records"][0][key], value)
        source = self.source(self.record)
        self.assertEqual((source["model"], source["barcode"], source["rack_no"]), tuple(values.values()))
        inventory = self.workbook_rows(main.api_ai_export_inventory([self.task], self.admin))
        row = next(row for row in inventory if row["设备名称"] == "网元甲")
        self.assertEqual(row["清单设备型号"], values["expected_model"])
        self.assertEqual(row["清单资产条码"], values["expected_barcode"])
        self.assertEqual(row["清单设备机柜位"], values["expected_rack_no"])
        self.assertEqual(row["现场资产条码"], "A1")
        comparison = self.workbook_rows(main.api_export_comparison([self.task], self.admin))
        row = next(row for row in comparison if row["系统"] == "综资" and row["资产条码"] == values["expected_barcode"])
        self.assertEqual(row["规格型号"], values["expected_model"])
        self.assertEqual(row["设备机柜位"], values["expected_rack_no"])
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_adjustment_tasks").fetchone()[0], 0, "直接修正不能额外下发整改任务")
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM comparison_events").fetchone()[0], 0)

    def test_existing_full_baseline_keeps_other_and_new_rows(self):
        tw.replace_baseline(self.task, [
            dict(device_name="网元甲", expected_model="基线型号", expected_barcode="BASE-A", room_code="001", device_extra={"设备机架": "BASE-R"}),
            dict(device_name="网元乙", expected_model="M2", expected_barcode="B1", room_code="001", device_extra={"设备机架": "R2"}),
            dict(device_name="综资新增网元", expected_model="M3", expected_barcode="C3", room_code="001", device_extra={"设备机架": "R3"}),
        ], self.admin["id"])
        current = tw.resource_record(self.record)
        self.assertEqual(current["expected_model"], "基线型号", "表单必须读取上传后的最新基线")
        revision, rows = self.baseline()
        untouched = [row for row in rows if row.get("record_id") != self.record]
        self.save()
        new_revision, rows = self.baseline()
        self.assertGreater(new_revision, revision)
        self.assertEqual([row for row in rows if row.get("record_id") != self.record], untouched)
        self.assertEqual(len(rows), 3)

    def test_stale_form_cannot_overwrite_latest_baseline(self):
        old = tw.resource_record(self.record)
        self.save()
        saved_revision, saved_rows = self.baseline()
        with self.assertRaises(ValueError):
            self.save(values=self.values(expected_model="陈旧覆盖"), fingerprint=old["fingerprint"])
        self.assertEqual(self.baseline(), (saved_revision, saved_rows))
        stale = tw.resource_record(self.record)
        self.save(self.other, self.values(expected_barcode="B2"))
        saved_revision, saved_rows = self.baseline()
        with self.assertRaises(ValueError):
            self.save(fingerprint=stale["fingerprint"])
        self.assertEqual(self.baseline(), (saved_revision, saved_rows))

    def test_blank_and_multiple_barcodes_are_valid_and_model_decisions_expire(self):
        group = next(group for group in tw.read([self.task])["groups"] if group["barcode"] == "A1")
        tw.save_decision([self.task], group["group_key"], group["fingerprint"], "model",
                         dict(field_status="一致", asset_status="一致", note="原型号人工确认"), self.admin["id"])
        self.save(values=self.values(expected_model="M-CHANGED", expected_barcode="A1"))
        self.assertEqual(next(group for group in tw.read([self.task])["groups"] if group["barcode"] == "A1")["field_model_status"], "待判断")
        self.save(values=self.values(expected_model="M1", expected_barcode="A1", expected_rack_no="R1"))
        self.assertEqual(next(group for group in tw.read([self.task])["groups"] if group["barcode"] == "A1")["field_model_status"], "待判断", "改回旧型号仍不能复活旧判断")
        self.save(values=self.values(expected_barcode=""))
        self.assertEqual(tw.resource_record(self.record)["expected_barcode"], "")
        self.save(values=self.values(expected_barcode="A1,A2"))
        self.assertEqual(tw.resource_record(self.record)["expected_barcode"], "A1,A2")

    def test_rack_round_trip_advances_full_source_generation(self):
        self.save(values=self.values(expected_model="M1", expected_barcode="A1", expected_rack_no="R1"))
        with db.get_conn() as conn:
            before = conn.execute("SELECT COALESCE(MAX(id),0) FROM comparison_source_changes WHERE kind='full'").fetchone()[0]
        self.save(values=self.values(expected_model="M1", expected_barcode="A1", expected_rack_no="R9"))
        self.save(values=self.values(expected_model="M1", expected_barcode="A1", expected_rack_no="R1"))
        with db.get_conn() as conn:
            after = conn.execute("SELECT COALESCE(MAX(id),0) FROM comparison_source_changes WHERE kind='full'").fetchone()[0]
        self.assertGreater(after, before, "机柜位改动及恢复应记录数据代次，防止旧人工核对复活")

    def test_unfinished_void_surplus_or_deleted_task_cannot_write(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.task,))
        surplus = db.create_surplus_record(self.task, "盘盈", "M", "S1", False, "SC", False, "SR", "", self.worker["id"])["id"]
        self.sql("UPDATE tasks SET status='done' WHERE id=?", (self.task,))
        cases = [
            ("UPDATE tasks SET status='doing' WHERE id=?", (self.task,), "UPDATE tasks SET status='done' WHERE id=?", (self.task,), self.record),
            ("UPDATE scan_records SET is_void=1 WHERE id=?", (self.record,), "UPDATE scan_records SET is_void=0 WHERE id=?", (self.record,), self.record),
            ("UPDATE tasks SET is_deleted=1 WHERE id=?", (self.task,), "UPDATE tasks SET is_deleted=0 WHERE id=?", (self.task,), self.record),
            ("SELECT 1", (), "SELECT 1", (), surplus),
        ]
        for before, params, after, restore, record_id in cases:
            with self.subTest(record_id=record_id, state=before):
                self.sql(before, params)
                try:
                    with self.assertRaises(ValueError):
                        tw.update_resource_record(record_id, self.values(), "0" * 64, self.admin["id"])
                    self.assertEqual(self.baseline()[0], 0)
                finally:
                    self.sql(after, restore)

    def test_removed_and_ambiguous_baseline_cannot_be_resurrected_by_single_edit(self):
        tw.replace_baseline(self.task, [dict(device_name="网元乙", expected_model="M2", expected_barcode="B1", room_code="001")], self.admin["id"])
        current = tw.resource_record(self.record)
        self.assertFalse(current["editable"])
        with self.assertRaises(ValueError):
            self.save(fingerprint=current["fingerprint"])
        task = db.get_task_by_id(self.task)
        rows = [tw.source_row("综资", "A1", "网元甲", "001", "M1", task, self.record, "R1") for _ in range(2)]
        self.sql("INSERT INTO resource_baselines(task_id,rows_json,actor_id,created_at) VALUES(?,?,?,?)",
                 (self.task, tw.encode(rows), self.admin["id"], "2026-11-07T12:00:00"))
        current = tw.resource_record(self.record)
        self.assertFalse(current["editable"])
        with self.assertRaises(ValueError):
            self.save(fingerprint=current["fingerprint"])

    def test_write_and_source_tracking_roll_back_when_event_fails(self):
        current = tw.resource_record(self.record)
        with db.get_conn() as conn:
            changes_before = [dict(row) for row in conn.execute("SELECT * FROM comparison_source_changes ORDER BY id")]
        with patch.object(tw, "event", side_effect=RuntimeError("isolated failure")):
            with self.assertRaises(RuntimeError):
                self.save(fingerprint=current["fingerprint"])
        self.assertEqual(self.baseline()[0], 0)
        with db.get_conn() as conn:
            self.assertEqual([dict(row) for row in conn.execute("SELECT * FROM comparison_source_changes ORDER BY id")], changes_before)
        self.assertEqual(tw.resource_record(self.record)["expected_model"], "M1")

    def token(self, user=None):
        user = user or self.admin
        return create_access_token(db.get_user_for_auth(user["id"]), "wechat" if user["role"] == "worker" else "password")

    async def request(self, method, path, payload=None, token=None):
        data = json.dumps(payload or {}).encode()
        headers = {"content-type": "application/json", "host": "127.0.0.1"}
        if token:
            headers["authorization"] = "Bearer " + token
        messages = []
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": data, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
                 "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
                 "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8000)}
        await asyncio.wait_for(main.app(scope, receive, send), 5)
        status = next(item["status"] for item in messages if item["type"] == "http.response.start")
        body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return status, json.loads(body)

    async def test_api_authorizes_full_and_collaborator_and_denies_worker_or_anonymous(self):
        path = f"/api/admin/records/{self.record}/resource"
        for user in (self.admin, self.collaborator):
            token = self.token(user)
            status, current = await self.request("GET", path, token=token)
            self.assertEqual(status, 200)
            self.assertTrue(current["editable"])
            status, result = await self.request("POST", path, {**self.values(), "fingerprint": current["fingerprint"]}, token=token)
            self.assertEqual(status, 200, result)
        for token in (None, self.token(self.worker)):
            for method in ("GET", "POST"):
                status, _ = await self.request(method, path, {**self.values(), "fingerprint": "0" * 64}, token=token)
                self.assertIn(status, (401, 403))

    async def test_api_rejects_stale_input_and_oversized_values(self):
        path = f"/api/admin/records/{self.record}/resource"
        token = self.token()
        _, current = await self.request("GET", path, token=token)
        payload = {**self.values(), "fingerprint": current["fingerprint"]}
        self.assertEqual((await self.request("POST", path, payload, token=token))[0], 200)
        self.assertEqual((await self.request("POST", path, {**payload, "expected_model": "旧表单"}, token=token))[0], 409)
        _, current = await self.request("GET", path, token=token)
        for field, value in [("expected_model", "M" * 1001), ("expected_barcode", "A" * 2001),
                             ("expected_rack_no", "R" * 1001), ("note", "N" * 2001), ("fingerprint", "bad")]:
            with self.subTest(field=field):
                status, _ = await self.request("POST", path, {**self.values(), "fingerprint": current["fingerprint"], field: value}, token=token)
                self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
