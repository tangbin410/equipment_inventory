import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl

from backend import database as db, main
from backend.auth import create_access_token, decode_access_token, hash_password
from backend.permissions import admin_request_allowed


class PermissionAllowlistTests(unittest.TestCase):
    def test_unknown_scopes_and_future_endpoints_are_not_implicitly_authorized(self):
        for scope in (None, "", "future-admin"):
            for method, path in (("GET", "/api/tasks"), ("POST", "/api/reconciliation/decisions")):
                with self.subTest(scope=scope, method=method, path=path):
                    self.assertFalse(admin_request_allowed({"admin_scope": scope}, method, path))
        collaborator = {"admin_scope": "collaborator"}
        for method, path in (("GET", "/api/future-sensitive-data"), ("POST", "/api/future-write"),
                             ("GET", "/api/tasks/1/future-data"), ("GET", "/api/reconciliation/future-data"),
                             ("POST", "/api/reconciliation/decisions/extra"), ("PUT", "/api/admin/records/1/corrections")):
            with self.subTest(method=method, path=path):
                self.assertFalse(admin_request_allowed(collaborator, method, path))
        self.assertTrue(admin_request_allowed(collaborator, "GET", "/api/tasks"))
        self.assertTrue(admin_request_allowed(collaborator, "POST", "/api/admin/records/1/corrections"))


class AdminPermissionTests(unittest.IsolatedAsyncioTestCase):
    """通过真实 ASGI 依赖链验证权限；业务数据仅在临时数据库中创建。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / "permissions.db"
        db.BACKUP_DIR = db.DB_DIR / "backups"
        self.settings = patch.dict(os.environ, {
            "INVENTORY_SECRET_KEY": "permission-test-key-not-for-production-12345",
            "INVENTORY_ENV": "development", "INVENTORY_DEV_LOGIN": "1",
            "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "1",
            "INVENTORY_ADMIN_ALLOWED_IPS": "127.0.0.1,::1",
            "INVENTORY_TRUSTED_PROXY_IPS": "127.0.0.1,::1",
            "INVENTORY_LOGIN_RATE_LIMIT_ENABLED": "0",
        })
        self.settings.start()
        db.init_db()
        password_hash = hash_password("permission-test-password-12345")
        self.full = db.create_user("admin-test-full", "主管理员", "admin", "南区", admin_scope="full", password_hash=password_hash)
        self.collaborator = db.create_user("admin-test-collaborator", "协作管理员", "admin", "南区", admin_scope="collaborator", password_hash=password_hash)
        self.worker = db.create_user("wx-field-test", "现场人员", "worker", "南区")
        self.dev_worker = db.create_user("worker_001", "本机调测人员", "worker", "南区")
        self.task = db.create_task("权限测试任务", "权限测试机房", self.worker["id"], "南区")
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET room_code='001',import_batch_id='test-batch' WHERE id=?", (self.task,))
        db.batch_create_records(self.task, [{"device_name": "网元甲", "expected_barcode": "A1", "expected_model": "M1", "device_extra": {"设备机架": "R1"}}])
        self.record = db.get_task_items(self.task)[0]["id"]
        db.submit_scan(self.record, "A1", False, "C1", False, "R1", "M1", "", created_by=self.worker["id"])
        self.surplus = db.create_surplus_record(self.task, "误录盘盈", "M2", "A2", False, "C2", False, "R2", "", self.worker["id"])["id"]
        db.complete_inventory_task(self.task, self.worker["id"])
        db.save_asset_info_snapshot(
            file_name="权限测试资产.xlsx", sheet_name="资产", barcode_column="资产条码",
            headers=["资产条码", "资源名称", "地点编码", "规格型号"],
            records=[{"barcode": "A1", "fields": {"资产条码": "A1", "资源名称": "网元甲", "地点编码": "001", "规格型号": "M1"}}],
            imported_by=self.full["id"], imported_by_name=self.full["name"],
        )

    def tearDown(self):
        self.settings.stop()
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def token(self, user=None, method="password"):
        return create_access_token(db.get_user_for_auth((user or self.collaborator)["id"]), method)

    async def request(self, method, url, payload=None, *, token=None, host="127.0.0.1", headers=None, file_bytes=None):
        path, _, query = url.partition("?")
        data = json.dumps(payload or {}).encode()
        request_headers = {"content-type": "application/json", **(headers or {})}
        if file_bytes is not None:
            boundary = "permission-test-boundary"
            data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="baseline.xlsx"\r\n'
                    'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n').encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
            request_headers["content-type"] = f"multipart/form-data; boundary={boundary}"
        if token:
            request_headers["authorization"] = "Bearer " + token
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
                 "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                 "root_path": "", "headers": [(key.encode(), value.encode()) for key, value in request_headers.items()],
                 "client": (host, 12345), "server": ("localhost", 8000)}
        await asyncio.wait_for(main.app(scope, receive, send), 5)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        try:
            body = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            pass
        return status, body

    async def test_collaborator_can_read_comparison_and_save_manual_decision(self):
        token = self.token()
        for path in ("/api/tasks", "/api/summary", f"/api/report/{self.task}", "/api/asset-info/status",
                     "/api/qr-assets", "/api/audit_logs", "/api/ai/adjustments", "/api/ai/results",
                     f"/api/reconciliation/export?task_ids={self.task}"):
            with self.subTest(path=path):
                self.assertEqual((await self.request("GET", path, token=token))[0], 200)
        status, comparison = await self.request("GET", f"/api/reconciliation?task_ids={self.task}", token=token)
        self.assertEqual(status, 200)
        group = next(group for group in comparison["groups"] if group["barcode"] == "A1")
        status, _ = await self.request("POST", "/api/reconciliation/decisions", {
            "task_ids": [self.task], "group_key": group["group_key"], "fingerprint": group["fingerprint"], "kind": "model",
            "decision": {"field_status": "一致", "asset_status": "一致", "note": "已人工核实"},
        }, token=token)
        self.assertEqual(status, 200)

    async def test_task_list_export_is_scoped_ordered_and_available_only_to_admins(self):
        task_ids = [db.create_task(f"测试任务{index}", f"测试机房{index}", self.worker["id"], "南区") for index in range(24)]
        selected = [task_ids[-1], self.task, *task_ids[:-1]]
        status, data = await self.request("POST", "/api/tasks/export", {"task_ids": selected + [selected[0]]}, token=self.token())
        self.assertEqual(status, 200)
        sheet = openpyxl.load_workbook(io.BytesIO(data)).active
        rows = list(sheet.values)
        exported = [dict(zip(rows[0], row)) for row in rows[1:]]
        self.assertEqual(len(exported), 25, "导出全部查询结果，不局限于当前页的20个任务；重复ID只输出一次")
        self.assertEqual([row["序号"] for row in exported], list(range(1,26)))
        self.assertEqual((exported[0]["机房"], exported[1]["机房"]), ("测试机房23", "权限测试机房"))
        self.assertEqual((exported[1]["现场状态"], exported[1]["完成率"]), ("现场完成", "100%"))
        self.assertEqual(sheet.freeze_panes, "B2")
        self.assertEqual((await self.request("POST", "/api/tasks/export", {"task_ids": [self.task]}, token=self.token(self.full)))[0], 200)
        for token in (None, self.token(self.worker)):
            status, _ = await self.request("POST", "/api/tasks/export", {"task_ids": [self.task]}, token=token)
            self.assertIn(status, (401,403))
        for selected_ids, expected_status in (([],422), ([-1],400), ([99999999],409)):
            status, _ = await self.request("POST", "/api/tasks/export", {"task_ids": selected_ids}, token=self.token())
            self.assertEqual(status, expected_status)
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET room_name='=SUM(1,2)' WHERE id=?", (task_ids[0],))
        status, data = await self.request("POST", "/api/tasks/export", {"task_ids": [task_ids[0]]}, token=self.token())
        self.assertEqual(status, 200)
        cell = openpyxl.load_workbook(io.BytesIO(data)).active['B2']
        self.assertEqual((cell.value, cell.data_type), ('=SUM(1,2)', 's'), "用户填写值不能成为Excel公式")

    async def test_collaborator_can_correct_equipment_racks_and_void_only_surplus(self):
        token = self.token()
        status, _ = await self.request("POST", f"/api/admin/records/{self.record}/corrections", {
            "actual_model": "管理员更正型号", "asset_barcodes": "A1", "cabinet_barcodes": "C1", "rack_no": "R1", "correction_reason": "核实型号",
        }, token=token)
        self.assertEqual(status, 200)
        self.assertEqual(db.get_record_by_id(self.record)["actual_model"], "管理员更正型号")
        rack = {"cabinet_type": "odf", "cabinet_barcode": "ODF1", "rack_no": "ODF-R1", "correction_reason": "核实补录"}
        status, result = await self.request("POST", f"/api/admin/tasks/{self.task}/racks", rack, token=token)
        self.assertEqual(status, 200)
        rack_id = result["cabinet"]["id"]
        status, _ = await self.request("PUT", f"/api/admin/tasks/{self.task}/racks/{rack_id}", {**rack, "rack_no": "ODF-R2"}, token=token)
        self.assertEqual(status, 200)
        self.assertEqual((await self.request("DELETE", f"/api/admin/tasks/{self.task}/racks/{rack_id}", {"reason": "误录"}, token=token))[0], 200)
        self.assertEqual((await self.request("DELETE", f"/api/admin/records/{self.surplus}", {"reason": "误录"}, token=token))[0], 200)
        status, _ = await self.request("DELETE", f"/api/admin/records/{self.record}", {"reason": "不可删除清单设备"}, token=token)
        self.assertEqual(status, 400, "允许作废入口仍必须执行清单设备不可作废的业务校验")

    async def test_collaborator_can_fix_location_and_update_existing_resource_baseline(self):
        token = self.token()
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET room_code='' WHERE id=?", (self.task,))
        self.assertEqual((await self.request("POST", f"/api/reconciliation/tasks/{self.task}/location", {"location_code": "002"}, token=token))[0], 200)
        workbook = openpyxl.Workbook()
        workbook.active.append(main.TASK_TEMPLATE_HEADERS)
        workbook.active.append(["权限测试机房", "南区", "OTN", "网元甲", "M1", "A1", "R1", "在网", "002"])
        stream = io.BytesIO()
        workbook.save(stream)
        status, _ = await self.request("POST", f"/api/reconciliation/tasks/{self.task}/resources", token=token, file_bytes=stream.getvalue())
        self.assertEqual(status, 200)

    async def test_collaborator_denies_account_task_config_import_dispatch_and_all_other_writes(self):
        token = self.token()
        forbidden = [
            ("GET", "/api/users"), ("GET", f"/api/users/{self.worker['id']}"), ("GET", "/api/workers"), ("GET", "/api/ai/status"),
            ("POST", "/api/users"), ("PATCH", f"/api/users/{self.worker['id']}"), ("DELETE", f"/api/users/{self.worker['id']}"),
            ("POST", f"/api/users/{self.full['id']}/password"),
            ("POST", "/api/upload_rooms"), ("POST", "/api/upload_rooms/preview"), ("POST", "/api/asset-info/import"),
            ("POST", "/api/asset-info/display-fields"), ("DELETE", f"/api/tasks/{self.task}"),
            ("POST", f"/api/tasks/{self.task}/assign"), ("POST", "/api/tasks/batch_assign"),
            ("PATCH", "/api/ai/status"), ("POST", "/api/ai/check"), ("POST", "/api/ai/analyze"), ("POST", "/api/ai/chat"),
            ("POST", "/api/reconciliation/adjustments"), ("POST", "/api/reconciliation/cases/1/approve"),
            ("POST", "/api/ai/adjustments/1/dispatch"), ("POST", "/api/ai/adjustments/1/verify"),
            ("POST", "/api/qr-assets"), ("PATCH", "/api/qr-assets/1"), ("POST", "/api/qr-assets/import"), ("DELETE", "/api/qr-assets/1"),
            ("POST", "/api/scan"), ("POST", "/api/cabinets"), ("POST", "/api/surplus"), ("POST", "/api/me/profile"),
            ("POST", f"/api/tasks/{self.task}/claim"), ("POST", f"/api/tasks/{self.task}/release"), ("POST", f"/api/tasks/{self.task}/complete"),
        ]
        for method, path in forbidden:
            with self.subTest(method=method, path=path):
                status, _ = await self.request(method, path, {}, token=token)
                self.assertEqual(status, 403, "协作管理员不能通过其他入口绕过修改范围")
        self.assertIsNotNone(db.get_task_by_id(self.task))
        self.assertIsNotNone(db.get_user_for_auth(self.worker["id"]))
        self.assertEqual((await self.request("GET", "/api/users", token=self.token(self.full)))[0], 200)

    async def test_changing_scope_or_role_invalidates_existing_session(self):
        token = self.token()
        claim_only_full = create_access_token({**db.get_user_for_auth(self.collaborator["id"]), "admin_scope": "full"}, "password")
        self.assertEqual((await self.request("GET", "/api/users", token=claim_only_full))[0], 403, "权限每次读取数据库，不相信token中的旧scope")
        db.update_user(self.collaborator["id"], {"admin_scope": "full"})
        self.assertEqual((await self.request("GET", "/api/tasks", token=token))[0], 401)
        full_token = self.token()
        self.assertEqual((await self.request("GET", "/api/users", token=full_token))[0], 200)
        db.update_user(self.collaborator["id"], {"admin_scope": "collaborator"})
        self.assertEqual((await self.request("GET", "/api/users", token=full_token))[0], 401)
        token = self.token()
        self.assertEqual((await self.request("GET", "/api/users", token=token))[0], 403)
        db.update_user(self.collaborator["id"], {"role": "worker"})
        self.assertEqual((await self.request("GET", "/api/tasks/my", token=token))[0], 401)

    async def test_admin_session_on_field_endpoint_still_requires_admin_source(self):
        for user in (self.full, self.collaborator):
            token = self.token(user)
            with self.subTest(scope=user["admin_scope"]):
                self.assertEqual((await self.request("GET", f"/api/report/{self.task}", token=token))[0], 200)
                for kwargs in ({"host": "203.0.113.9"}, {"headers": {"cf-connecting-ip": "203.0.113.9"}}):
                    self.assertEqual((await self.request("GET", f"/api/report/{self.task}", token=token, **kwargs))[0], 404)

    async def test_dev_and_legacy_test_sessions_cannot_survive_mode_or_source_change(self):
        for token in (self.token(self.dev_worker, "dev"), self.token(self.dev_worker, None), self.token(self.worker, "dev")):
            with self.subTest(method=decode_access_token(token).get("method")):
                self.assertEqual((await self.request("GET", "/api/tasks/my", token=token))[0], 200)
                with patch.dict(os.environ, {"INVENTORY_DEV_LOGIN": "0"}):
                    self.assertEqual((await self.request("GET", "/api/tasks/my", token=token))[0], 401)
                with patch.dict(os.environ, {"INVENTORY_ENV": "production"}):
                    self.assertEqual((await self.request("GET", "/api/tasks/my", token=token))[0], 401)
                self.assertEqual((await self.request("GET", "/api/tasks/my", token=token, host="203.0.113.9"))[0], 403)
                self.assertEqual((await self.request("GET", "/api/tasks/my", token=token, headers={"cf-connecting-ip": "203.0.113.9"}))[0], 403)
        with patch.dict(os.environ, {"INVENTORY_DEV_LOGIN": "0"}):
            self.assertEqual((await self.request("GET", "/api/tasks/my", token=self.token(self.worker, "wechat"), host="203.0.113.9"))[0], 200)

    async def test_actual_login_routes_issue_session_method_and_admin_scope(self):
        status, result = await self.request("POST", "/api/admin/login", {"username": self.collaborator["wx_openid"], "password": "permission-test-password-12345"})
        self.assertEqual(status, 200)
        self.assertEqual(result["user"]["admin_scope"], "collaborator")
        self.assertEqual(decode_access_token(result["access_token"])["method"], "password")
        status, result = await self.request("POST", "/api/login", {"code": "worker_001"})
        self.assertEqual(status, 200)
        self.assertEqual(decode_access_token(result["access_token"])["method"], "dev")
        response = SimpleNamespace(read=lambda: json.dumps({"openid": self.worker["wx_openid"]}).encode())
        with patch.dict(os.environ, {"INVENTORY_DEV_LOGIN": "0", "WECHAT_APPID": "test-appid", "WECHAT_SECRET": "test-secret"}), patch("backend.main.urlopen", return_value=response):
            status, result = await self.request("POST", "/api/login", {"code": "mock-wechat-code"}, host="203.0.113.9")
        self.assertEqual(status, 200)
        self.assertEqual(decode_access_token(result["access_token"])["method"], "wechat")

    async def test_full_admin_creates_independent_credentials_and_promotes_worker_without_duplicate_overwrite(self):
        full_token = self.token(self.full)
        password = "new-collaborator-password-12345"
        replacement_password = "duplicate-must-not-apply-67890"
        payload = {"wx_openid": "new-collaborator", "name": "新增协作管理员", "role": "admin",
                   "admin_scope": "collaborator", "area_name": "南区", "password": password}
        status, created = await self.request("POST", "/api/users", payload, token=full_token)
        self.assertEqual(status, 200)
        stored = db.get_user_for_auth(created["id"])
        self.assertTrue(stored["password_hash"])
        status, login = await self.request("POST", "/api/admin/login", {"username": payload["wx_openid"], "password": password})
        self.assertEqual(status, 200)
        self.assertEqual(login["user"]["admin_scope"], "collaborator")
        status, duplicate = await self.request("POST", "/api/users", {**payload, "password": replacement_password, "admin_scope": "full"}, token=full_token)
        self.assertEqual(status, 409)
        after_duplicate = db.get_user_for_auth(created["id"])
        self.assertEqual(after_duplicate["password_hash"], stored["password_hash"])
        self.assertEqual(after_duplicate["admin_scope"], "collaborator")
        self.assertEqual(after_duplicate["auth_version"], stored["auth_version"])
        self.assertEqual((await self.request("POST", "/api/admin/login", {"username": payload["wx_openid"], "password": password}))[0], 200)
        self.assertEqual((await self.request("POST", "/api/admin/login", {"username": payload["wx_openid"], "password": replacement_password}))[0], 401)

        worker_token = self.token(self.worker, "wechat")
        promotion = {"role": "admin", "admin_scope": "collaborator"}
        self.assertEqual((await self.request("PATCH", f"/api/users/{self.worker['id']}", promotion, token=full_token))[0], 400)
        self.assertEqual(db.get_user_for_auth(self.worker["id"])["role"], "worker")
        promotion_password = "promoted-admin-password-12345"
        status, promoted = await self.request("PATCH", f"/api/users/{self.worker['id']}", {**promotion, "password": promotion_password}, token=full_token)
        self.assertEqual(status, 200)
        self.assertEqual((await self.request("GET", "/api/tasks/my", token=worker_token))[0], 401)
        status, promoted_login = await self.request("POST", "/api/admin/login", {"username": self.worker["wx_openid"], "password": promotion_password})
        self.assertEqual(status, 200)
        exported = json.dumps([created, login, duplicate, promoted, promoted_login, db.get_audit_logs()])
        for secret in (password, replacement_password, promotion_password, stored["password_hash"], "password_hash"):
            self.assertNotIn(secret, exported, "账号响应和审计不得包含密码或哈希")

    async def test_password_reset_requires_full_admin_and_revokes_old_session(self):
        collaborator_token = self.token()
        old_hash = db.get_user_for_auth(self.collaborator["id"])["password_hash"]
        new_password = "reset-collaborator-password-67890"
        for target in (self.collaborator, self.full):
            with self.subTest(target=target["id"]):
                status, _ = await self.request("POST", f"/api/users/{target['id']}/password", {"password": new_password}, token=collaborator_token)
                self.assertEqual(status, 403)
        self.assertEqual(db.get_user_for_auth(self.collaborator["id"])["password_hash"], old_hash)
        status, result = await self.request("POST", f"/api/users/{self.collaborator['id']}/password", {"password": new_password}, token=self.token(self.full))
        self.assertEqual(status, 200)
        self.assertEqual((await self.request("GET", "/api/tasks", token=collaborator_token))[0], 401)
        self.assertEqual((await self.request("POST", "/api/admin/login", {"username": self.collaborator["wx_openid"], "password": "permission-test-password-12345"}))[0], 401)
        status, login = await self.request("POST", "/api/admin/login", {"username": self.collaborator["wx_openid"], "password": new_password})
        self.assertEqual(status, 200)
        self.assertEqual((await self.request("GET", "/api/tasks", token=login["access_token"]))[0], 200)
        exported = json.dumps([result, login, db.get_audit_logs()])
        for secret in (new_password, old_hash, db.get_user_for_auth(self.collaborator["id"])["password_hash"], "password_hash"):
            self.assertNotIn(secret, exported)

    async def test_last_full_admin_cannot_remove_own_login_capability_through_api(self):
        token = self.token(self.full)
        for method, payload in (("PATCH", {"admin_scope": "collaborator"}), ("PATCH", {"is_active": False}), ("DELETE", {})):
            with self.subTest(method=method, payload=payload):
                status, _ = await self.request(method, f"/api/users/{self.full['id']}", payload, token=token)
                self.assertEqual(status, 400)
                user = db.get_user_for_auth(self.full["id"])
                self.assertEqual(user["admin_scope"], "full")
                self.assertEqual(user["is_active"], 1)
                self.assertEqual((await self.request("GET", "/api/users", token=token))[0], 200)

    async def test_password_validation_errors_do_not_echo_supplied_password(self):
        token = self.token(self.full)
        weak_password = "short-P9"
        requests = [
            ("POST", "/api/users", {"wx_openid": "weak-password-user", "name": "测试", "role": "admin", "password": weak_password}),
            ("PATCH", f"/api/users/{self.collaborator['id']}", {"password": weak_password}),
            ("POST", f"/api/users/{self.collaborator['id']}/password", {"password": weak_password}),
        ]
        for method, path, payload in requests:
            with self.subTest(method=method, path=path):
                status, error = await self.request(method, path, payload, token=token)
                self.assertEqual(status, 422)
                self.assertNotIn(weak_password, json.dumps(error))
                self.assertTrue(any("password" in entry["loc"] for entry in error["detail"]), "保留错误字段位置方便用户修正")
                for entry in error["detail"]:
                    self.assertNotIn("input", entry)
                    self.assertNotIn("ctx", entry)
        self.assertIsNone(db.get_user_by_openid("weak-password-user"))


if __name__ == "__main__":
    unittest.main()
