import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import database as db, main
from backend.auth import create_access_token
from backend.permissions import admin_request_allowed


class RoomModelTests(unittest.IsolatedAsyncioTestCase):
    """建模读取只使用临时数据库，不启动服务或读取现场数据。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in (("DB_DIR", root), ("DB_PATH", root / "room-model.db"),
                            ("BACKUP_DIR", root / "backups")):
            replacement = patch.object(db, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        settings = patch.dict(os.environ, {
            "INVENTORY_SECRET_KEY": "room-model-test-secret-not-for-production",
            "INVENTORY_ENV": "development",
            "INVENTORY_ADMIN_HOST": "",
            "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "1",
            "INVENTORY_ADMIN_ALLOWED_IPS": "127.0.0.1,::1",
        })
        settings.start()
        self.addCleanup(settings.stop)
        db.init_db()
        self.admin = db.create_user("model-admin", "管理员", "admin", "南区")
        self.collaborator = db.create_user("model-collaborator", "协作管理员", "admin", "南区", admin_scope="collaborator")
        self.worker = db.create_user("model-worker", "现场人员", "worker", "南区")
        with db.get_conn() as conn:
            self.task_id = conn.execute(
                """INSERT INTO tasks(task_name,room_name,room_code,area_name,status,created_at,claimed_by)
                   VALUES('建模测试','测试机房','MODEL-ROOM','南区','doing','2026-11-12T08:00:00',?)""",
                (self.worker["id"],),
            ).lastrowid
            self.record_id = conn.execute(
                """INSERT INTO scan_records(task_id,device_name,expected_model,expected_barcode)
                   VALUES(?,'测试网元','MODEL-A','ASSET-A')""", (self.task_id,),
            ).lastrowid
        db.submit_scan(self.record_id, "ASSET-A", False, "CAB-OLD", False,
                       "AA01A", "MODEL-A", "", self.worker["id"])

    def complete(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET status='done',field_completed_at='2026-11-12T10:00:00' WHERE id=?", (self.task_id,))

    def test_unfinished_room_is_rejected(self):
        for status in ("pending", "doing"):
            with self.subTest(status=status):
                with db.get_conn() as conn:
                    conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, self.task_id))
                with self.assertRaises(HTTPException) as caught:
                    main.api_room_model(self.task_id, self.admin)
                self.assertEqual(caught.exception.status_code, 409)
                self.assertIn("已完成", caught.exception.detail)

    def test_completed_room_uses_admin_correction_for_cabinet_summary(self):
        self.complete()
        db.create_record_correction(self.record_id, {
            "actual_model": "MODEL-NEW", "asset_barcodes": "ASSET-A",
            "cabinet_barcodes": "CAB-NEW", "rack_no": "BB02B",
        }, "现场照片核实", "", self.admin["id"])
        report = main.api_room_model(self.task_id, self.admin)
        self.assertEqual(report["task"]["id"], self.task_id)
        self.assertEqual(report["records"][0]["actual_model"], "MODEL-NEW")
        self.assertTrue(report["records"][0]["has_admin_correction"])
        self.assertEqual([(cabinet["cabinet_type"], cabinet["cabinet_barcode"], cabinet["rack_no"])
                          for cabinet in report["cabinet_records"]], [("equipment", "CAB-NEW", "BB02B")])

    def test_odf_and_iodf_remain_independent_types(self):
        db.create_cabinet(self.task_id, "odf", "ODF-A", False, "AA02A", "", self.worker["id"])
        db.create_cabinet(self.task_id, "iodf", "IODF-A", False, "AA03A", "", self.worker["id"])
        self.complete()
        report = main.api_room_model(self.task_id, self.admin)
        self.assertEqual({cabinet["cabinet_barcode"]: cabinet["cabinet_type"]
                          for cabinet in report["cabinet_records"]},
                         {"CAB-OLD": "equipment", "ODF-A": "odf", "IODF-A": "iodf"})

    def test_missing_and_void_records_do_not_create_equipment_cabinets(self):
        surplus = db.create_surplus_record(self.task_id, "误录设备", "MODEL-X", "VOID-ASSET",
                                          False, "VOID-CAB", False, "AA02A", "", self.worker["id"])
        self.complete()
        db.create_record_correction(self.record_id, {
            "record_type": "missing", "missing_reason": "现场未找到",
        }, "现场复核", "", self.admin["id"])
        db.void_capture_record("scan_records", surplus["id"], self.admin["id"], "重复登记")
        report = main.api_room_model(self.task_id, self.admin)
        self.assertEqual([record["id"] for record in report["records"]], [self.record_id])
        self.assertEqual(report["records"][0]["record_type"], "missing")
        self.assertEqual(report["cabinet_records"], [])

    async def test_endpoint_is_admin_only_and_collaborator_is_read_only(self):
        self.complete()
        path = f"/api/admin/room-models/{self.task_id}"
        for user in (self.admin, self.collaborator):
            with self.subTest(role=user["wx_openid"]):
                status, body = await self.get(path, user)
                self.assertEqual(status, 200)
                self.assertEqual(body["task"]["id"], self.task_id)
        self.assertEqual((await self.get(path, self.worker))[0], 403)
        self.assertEqual((await self.get(path))[0], 401)
        self.assertTrue(admin_request_allowed(self.collaborator, "GET", path))
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertFalse(admin_request_allowed(self.collaborator, method, path))

    async def get(self, path, user=None):
        headers = []
        if user is not None:
            token = create_access_token(db.get_user_for_auth(user["id"]), "password")
            headers.append((b"authorization", f"Bearer {token}".encode()))
        messages = []
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": headers,
                 "client": ("127.0.0.1", 12345), "server": ("localhost", 8000)}
        await asyncio.wait_for(main.app(scope, receive, send), 5)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return status, json.loads(body)


if __name__ == "__main__":
    unittest.main()
