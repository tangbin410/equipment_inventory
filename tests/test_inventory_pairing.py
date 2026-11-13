"""盘亏盘盈拼销的隔离测试；原始采集、筛选、跨房展示和撤销保持一致。"""

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from backend import ai_agent, database as db, inventory_pairing as pairing, main, three_way
from backend.auth import create_access_token, hash_password
from backend.permissions import admin_request_allowed


class InventoryPairingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / "pairing.db"
        db.BACKUP_DIR = db.DB_DIR / "backups"
        self.env = patch.dict(os.environ, {
            "INVENTORY_SECRET_KEY": "isolated-pairing-test-key-1234567890",
            "INVENTORY_ENV": "development", "INVENTORY_DEV_LOGIN": "1",
            "INVENTORY_REMOTE_ADMIN_ENABLED": "0",
            "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "1",
            "INVENTORY_ADMIN_ALLOWED_IPS": "127.0.0.1,::1",
            "INVENTORY_TRUSTED_PROXY_IPS": "127.0.0.1,::1",
            "INVENTORY_LOGIN_RATE_LIMIT_ENABLED": "0",
        })
        self.env.start()
        db.init_db()
        with db.get_conn() as conn:
            pairing.init_schema(conn)
        secret = hash_password("isolated-pairing-password-123")
        self.admin = db.create_user("pair-admin", "管理员", "admin", "南区", admin_scope="full", password_hash=secret)
        self.collaborator = db.create_user("pair-collaborator", "协作管理员", "admin", "南区", admin_scope="collaborator", password_hash=secret)
        self.worker = db.create_user("pair-worker", "现场人员", "worker", "南区")
        self.source_task, self.missing = self.make_task("机房甲", "001", "batch-a", missing=True)
        self.target_task, self.normal = self.make_task("机房乙", "002", "batch-b", finish=False)
        self.surplus = self.add_surplus(self.target_task)
        self.finish(self.target_task)

    def tearDown(self):
        self.env.stop()
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def sql(self, statement, values=()):
        with db.get_conn() as conn:
            return conn.execute(statement, values).lastrowid

    def make_task(self, room, code, batch, *, missing=False, finish=True):
        task = db.create_task(room + "盘点", room, self.worker["id"], "南区")
        self.sql("UPDATE tasks SET room_code=?,import_batch_id=? WHERE id=?", (code, batch, task))
        db.batch_create_records(task, [dict(device_name="网元甲" if missing else "网元乙", expected_barcode="ASSET-A" if missing else "ASSET-B",
                                           expected_model="MODEL-A" if missing else "MODEL-B", device_extra={"设备类别": "OTN" if missing else "PTN", "设备机架": "R1" if missing else "R2"})])
        record = db.get_task_items(task)[0]["id"]
        if missing:
            db.mark_record_missing(record, "现场未找到", created_by=self.worker["id"])
        else:
            db.submit_scan(record, "ASSET-B", False, "CAB-B", False, "R2", "MODEL-B", "", created_by=self.worker["id"])
        if finish:
            self.finish(task)
        return task, record

    def finish(self, task):
        db.complete_inventory_task(task, self.worker["id"])

    def add_surplus(self, task, *, name="现场发现甲", barcode="ASSET-A", cabinet="CAB-A", category="OTN", model="MODEL-A"):
        record = db.create_surplus_record(task, name, model, barcode, not bool(barcode), cabinet, False, "R9", "现场原始备注", self.worker["id"])
        self.sql("UPDATE scan_records SET device_extra=? WHERE id=?", (json.dumps({"设备类别": category}, ensure_ascii=False), record["id"]))
        return record["id"]

    def enrich(self, record_id):
        with db.get_conn() as conn:
            return pairing.enrich_record(conn, record_id)

    def pair(self, **changes):
        missing, surplus = self.enrich(self.missing), self.enrich(self.surplus)
        args = dict(missing_record_id=self.missing, surplus_record_id=self.surplus,
                    missing_fingerprint=missing["fingerprint"], surplus_fingerprint=surplus["fingerprint"],
                    note="已查网管和设备照片，确认同一实物", actor_id=self.admin["id"])
        args.update(changes)
        return pairing.create_pair(**args)

    def projected(self, task):
        with db.get_conn() as conn:
            return pairing.project_records(conn, task, db.get_task_items(task))

    def raw(self, record):
        with db.get_conn() as conn:
            return dict(conn.execute("SELECT * FROM scan_records WHERE id=?", (record,)).fetchone())

    def active(self):
        with db.get_conn() as conn:
            return pairing.get_active_pairs(conn)

    def assets(self):
        db.save_asset_info_snapshot(file_name="资产.xlsx", sheet_name="资产", barcode_column="资产条码",
            headers=["资产条码", "资源名称", "地点编码", "规格型号"],
            records=[dict(barcode=barcode, fields=dict(zip(["资产条码", "资源名称", "地点编码", "规格型号"], values)))
                     for barcode, values in [("ASSET-A", ["ASSET-A", "网元甲", "001", "MODEL-A"]), ("ASSET-B", ["ASSET-B", "网元乙", "002", "MODEL-B"])]],
            imported_by=self.admin["id"], imported_by_name="管理员")

    def test_bidirectional_candidates_are_global_and_not_limited_to_a_batch(self):
        forward = pairing.candidates(self.missing)
        reverse = pairing.candidates(self.surplus)
        self.assertEqual([row["id"] for row in forward["candidates"]], [self.surplus])
        self.assertEqual([row["id"] for row in reverse["candidates"]], [self.missing])
        self.assertEqual(forward["source"]["device_category"], "OTN")
        self.assertEqual(forward["candidates"][0]["room_name"], "机房乙")
        self.assertEqual(self.active(), [], "搜索候选不能自动确认或写入拼销")

    def test_candidate_filters_use_name_device_barcode_model_category_and_room(self):
        for keyword in ("发现甲", "ASSET-A", "MODEL-A"):
            self.assertEqual([r["id"] for r in pairing.candidates(self.missing, keyword=keyword)["candidates"]], [self.surplus])
        self.assertEqual(len(pairing.candidates(self.missing, room_name="机房乙", device_category="OTN")["candidates"]), 1)
        for options in (dict(keyword="不存在"), dict(room_name="机房甲"), dict(device_category="PTN"), dict(same_room=True)):
            self.assertEqual(pairing.candidates(self.missing, **options)["candidates"], [])

    def test_candidate_prefilter_uses_effective_correction_type_in_both_directions(self):
        db.create_record_correction(self.normal, dict(record_type="missing", missing_reason="管理员已核实未找到"),
                                    "核实盘点结论", "", self.admin["id"])
        self.assertEqual({row["id"] for row in pairing.candidates(self.surplus)["candidates"]}, {self.missing, self.normal})
        db.create_record_correction(self.missing, dict(record_type="normal", actual_model="MODEL-A", asset_barcodes="ASSET-A",
            cabinet_barcodes="CAB-A", rack_no="R1"), "核实后已找到设备", "", self.admin["id"])
        self.assertEqual([row["id"] for row in pairing.candidates(self.surplus)["candidates"]], [self.normal],
                         "不能因原记录仍是missing而返回已经修正为正常的设备")
        self.assertEqual([row["id"] for row in pairing.candidates(self.normal)["candidates"]], [self.surplus])

    def test_equipment_barcode_is_preferred_but_cabinet_barcode_is_not_an_equipment_match(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.target_task,))
        other = self.add_surplus(self.target_task, name="柜条码碰巧相同", barcode="OTHER", cabinet="ASSET-A", model="OTHER-M")
        self.finish(self.target_task)
        result = pairing.candidates(self.missing)["candidates"]
        self.assertEqual(result[0]["id"], self.surplus)
        reasons = {row["id"]: row.get("match_reason", "") for row in result}
        self.assertNotEqual(reasons[self.surplus], reasons[other])
        self.assertEqual([row["id"] for row in pairing.candidates(self.missing, keyword="OTHER")["candidates"]], [other])

    def test_barcode_placeholders_are_not_recommendation_evidence(self):
        for placeholder in ("缺失", "无条码", "未填写", "-"):
            self.sql("UPDATE scan_records SET expected_barcode=? WHERE id=?", (placeholder, self.missing))
            self.sql("UPDATE scan_records SET asset_barcodes=?,asset_no_barcode=0 WHERE id=?", (placeholder, self.surplus))
            self.assertEqual(pairing.candidates(self.missing, recommended_only=True)["candidates"], [],
                             "两个空码提示相同不代表设备条码一致")
            result = pairing.candidates(self.missing, room_name="机房乙")["candidates"]
            self.assertEqual(result[0]["match_reason"], "人工核对候选")

    def test_only_latest_completed_non_void_records_can_be_selected(self):
        newer, new_missing = self.make_task("机房甲", "001", "newer", missing=True)
        self.sql("UPDATE tasks SET field_completed_at='2099-01-01T00:00:00',completed_at='2099-01-01T00:00:00' WHERE id=?", (newer,))
        self.assertEqual([r["id"] for r in pairing.candidates(self.surplus)["candidates"]], [new_missing])
        with self.assertRaises(ValueError):
            self.pair()
        self.sql("UPDATE scan_records SET is_void=1 WHERE id=?", (new_missing,))
        self.assertEqual(pairing.candidates(self.surplus)["candidates"], [])

    def test_same_room_pair_is_one_effective_record_and_raw_history_stays_unchanged(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.source_task,))
        self.surplus = self.add_surplus(self.source_task)
        self.finish(self.source_task)
        originals = [self.raw(self.missing), self.raw(self.surplus)]
        saved = self.pair()
        result = self.projected(self.source_task)
        self.assertEqual(len(result), 1)
        merged = result[0]
        self.assertEqual((merged["id"], merged["device_name"], merged["record_type"]), (self.surplus, "网元甲", "normal"))
        self.assertEqual((merged["asset_barcodes"], merged["rack_no"], merged["expected_rack_no"], merged["device_category"]), ("ASSET-A", "R9", "R1", "OTN"))
        self.assertEqual(merged["pairing"]["role"], "merged")
        self.assertFalse(saved["cross_room"])
        self.assertEqual([self.raw(self.missing), self.raw(self.surplus)], originals)
        self.assertEqual([r["record_type"] for r in db.get_task_items(self.source_task)], ["missing", "surplus"], "小程序历史仍使用两条原始采集")
        worker_report = main.api_report(self.source_task, self.worker)
        self.assertEqual([r["record_type"] for r in worker_report["records"]], ["missing", "surplus"])
        self.assertTrue(all(not r.get("pairing") for r in worker_report["records"]))
        self.assertEqual(len(main.api_report(self.source_task, self.admin)["records"]), 1)
        self.assertEqual(pairing.candidates(self.missing)["candidates"], [], "已拼销记录不能重复进入候选")

    def test_cross_room_pair_belongs_to_found_room_and_source_keeps_outbound_notice(self):
        original_missing, original_surplus = self.raw(self.missing), self.raw(self.surplus)
        saved = self.pair()
        origin, destination = self.projected(self.source_task), self.projected(self.target_task)
        self.assertEqual(len(origin), 1)
        self.assertEqual(origin[0]["pairing"]["role"], "outbound")
        self.assertEqual(origin[0]["pairing"]["target_room_name"], "机房乙")
        merged = next(r for r in destination if r["id"] == self.surplus)
        self.assertEqual((merged["task_id"], merged["record_type"], merged["device_name"]), (self.target_task, "normal", "网元甲"))
        self.assertEqual(merged["pairing"]["source_room_name"], "机房甲")
        self.assertTrue(saved["cross_room"])
        self.assertIn("调拨", saved["status"])
        self.assertEqual(self.raw(self.missing), original_missing)
        self.assertEqual(self.raw(self.surplus), original_surplus)

    def test_double_pairing_stale_fingerprints_and_invalid_types_are_rejected(self):
        with self.assertRaises(ValueError):
            self.pair(missing_fingerprint="0" * 64)
        with self.assertRaises(ValueError):
            self.pair(surplus_record_id=self.normal, surplus_fingerprint=self.enrich(self.normal)["fingerprint"])
        with self.assertRaises(ValueError):
            self.pair(note="  ")
        saved = self.pair()
        self.assertEqual(self.pair()["id"], saved["id"], "重复提交同一配对幂等，不新增拼销")
        _, another_missing = self.make_task("机房丙", "003", "batch-c", missing=True)
        with self.assertRaises(ValueError):
            self.pair(missing_record_id=another_missing, missing_fingerprint=self.enrich(another_missing)["fingerprint"])
        self.assertEqual(len(self.active()), 1)

    def test_source_change_restores_original_rows_with_stale_notice_until_cancelled(self):
        saved = self.pair()
        self.sql("UPDATE scan_records SET asset_barcodes='CHANGED' WHERE id=?", (self.surplus,))
        origin, target = self.projected(self.source_task), self.projected(self.target_task)
        self.assertEqual(origin[0]["record_type"], "missing")
        self.assertEqual(next(r for r in target if r["id"] == self.surplus)["record_type"], "surplus")
        self.assertEqual(origin[0]["pairing"]["role"], "stale")
        self.assertFalse(self.active()[0]["valid"])
        pairing.cancel_pair(saved["id"], "资料变化，重新核实", self.admin["id"])
        self.assertEqual(self.active(), [])
        self.assertNotIn("pairing", self.projected(self.source_task)[0])

    def test_cancel_restores_records_and_preserves_audit_and_allows_repair(self):
        audit_before = len(db.get_audit_logs(200))
        saved = self.pair(actor_id=self.collaborator["id"])
        result = pairing.cancel_pair(saved["id"], "确认对象选错，撤销核实", self.collaborator["id"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.active(), [])
        self.assertEqual(self.projected(self.source_task)[0]["record_type"], "missing")
        self.assertEqual(next(r for r in self.projected(self.target_task) if r["id"] == self.surplus)["record_type"], "surplus")
        self.assertFalse(pairing.cancel_pair(saved["id"], "重复撤销", self.admin["id"])["cancelled"])
        self.assertGreaterEqual(len(db.get_audit_logs(200)), audit_before + 2)
        self.assertNotEqual(self.pair()["id"], saved["id"])

    def test_incomplete_deleted_superseded_task_and_worker_cannot_pair(self):
        for column, value, restore in [("status", "doing", "done"), ("is_deleted", 1, 0), ("superseded_by_task_id", self.target_task, None)]:
            self.sql(f"UPDATE tasks SET {column}=? WHERE id=?", (value, self.source_task))
            try:
                with self.assertRaises(ValueError):
                    self.pair()
            finally:
                self.sql(f"UPDATE tasks SET {column}=? WHERE id=?", (restore, self.source_task))
        with self.assertRaises(ValueError):
            self.pair(actor_id=self.worker["id"])
        self.assertEqual(self.active(), [])

    def test_effective_administrator_correction_and_latest_baseline_are_used(self):
        three_way.replace_baseline(self.source_task, [dict(device_name="网元甲", expected_model="BASE-M", expected_barcode="BASE-A", room_code="001", device_extra={"设备机架": "BASE-R", "设备类别": "OTN"})], self.admin["id"])
        db.create_record_correction(self.surplus, dict(actual_model="CORRECTED-M", asset_barcodes="BASE-A", cabinet_barcodes="CORRECTED-C", rack_no="CORRECTED-R", record_type="surplus"), "核实实物后更正", "", self.admin["id"])
        expected, actual = self.enrich(self.missing), self.enrich(self.surplus)
        self.assertEqual((expected["expected_model"], expected["expected_barcode"], expected["expected_rack_no"]), ("BASE-M", "BASE-A", "BASE-R"))
        self.assertEqual((actual["actual_model"], actual["asset_barcodes"]), ("CORRECTED-M", "BASE-A"))
        self.assertEqual([r["id"] for r in pairing.candidates(self.surplus, recommended_only=True)["candidates"]], [self.missing],
                         "优先推荐应读取管理员修正的实扫条码和最新综资基线")
        self.pair()
        merged = next(r for r in self.projected(self.target_task) if r["id"] == self.surplus)
        self.assertEqual((merged["expected_barcode"], merged["actual_model"], merged["rack_no"]), ("BASE-A", "CORRECTED-M", "CORRECTED-R"))

    def test_open_adjustment_blocks_pairing_for_the_same_resource(self):
        for source_key in (f"{self.missing}:asset", f"location:{self.missing}:{self.surplus}"):
            case_id = self.sql("INSERT INTO ai_adjustment_tasks(source_type,source_key,status,created_at) VALUES('rule',?,'pending_review','2026-11-10T08:00:00')", (source_key,))
            with self.assertRaisesRegex(ValueError, "未闭环整改单"):
                self.pair()
            self.sql("DELETE FROM ai_adjustment_tasks WHERE id=?", (case_id,))
        result = three_way.read([self.source_task])
        group = next(g for g in result["groups"] if any(r.get("record_id") == self.missing for r in three_way.source_records(g, "综资")))
        meta = {"group_key": group["group_key"], "task_ids": [self.source_task], "snapshot": group}
        self.sql("INSERT INTO ai_adjustment_tasks(source_task_ids_json,status,created_at,comparison_json) VALUES(?,'pending_review','2026-11-10T08:00:00',?)", (json.dumps([self.source_task]), json.dumps(meta)))
        with self.assertRaises(ValueError):
            self.pair()

    def test_report_summary_export_and_three_way_use_one_same_room_projection(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.source_task,))
        self.surplus = self.add_surplus(self.source_task)
        self.finish(self.source_task)
        self.assets()
        self.pair()
        task = next(t for t in main.api_summary(self.admin) if t["id"] == self.source_task)
        self.assertEqual((task["total"], task["scanned"], len(task["records"])), (1, 1, 1))
        self.assertEqual(task["records"][0]["device_category"], "OTN")
        self.assertEqual(len(db.get_report(self.source_task)), 1)
        workbook = openpyxl.load_workbook(io.BytesIO(main.api_ai_export_inventory([self.source_task], self.admin).body))
        rows = list(workbook.active.values)
        exported = [dict(zip(rows[0], row)) for row in rows[1:]]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["设备类别"], "OTN")
        self.assertTrue(exported[0]["拼销状态"])
        comparison = three_way.read_completed([self.source_task])
        matched = [g for g in comparison["groups"] if g["device_name"] == "网元甲"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(len(three_way.source_records(matched[0], "现场")), 1)
        self.assertEqual(len(comparison["groups"]), 1, "不能另留原盘盈名称生成重复比对组")

    def test_cancel_then_pair_again_does_not_resurrect_previous_manual_judgment(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.source_task,))
        self.surplus = self.add_surplus(self.source_task)
        self.finish(self.source_task)
        self.assets()
        saved = self.pair()

        def group():
            return next(g for g in three_way.read_completed([self.source_task])["groups"] if g["device_name"] == "网元甲")

        before = group()
        three_way.save_decision([self.source_task], before["group_key"], before["fingerprint"], "model",
            dict(field_status="一致", asset_status="一致", note="已核实拼销双方设备型号"), self.admin["id"])
        judged = group()
        self.assertEqual((judged["field_model_status"], judged["asset_model_status"]), ("一致", "一致"))
        pairing.cancel_pair(saved["id"], "更换确认依据后重新核实", self.admin["id"])
        self.assertEqual(group()["field_model_status"], "待判断")
        self.assertNotEqual(self.pair()["id"], saved["id"])
        rebuilt = group()
        self.assertNotEqual(rebuilt["fingerprint"], judged["fingerprint"])
        self.assertEqual(rebuilt["field_model_status"], "待判断", "重新拼销必须重新判断，不能复用上一笔确认")

    def test_cross_room_summary_moves_count_and_three_way_cannot_close_pending_transfer(self):
        self.assets()
        self.pair()
        tasks = {t["id"]: t for t in main.api_summary(self.admin)}
        self.assertEqual(tasks[self.source_task]["total"], 0)
        self.assertEqual(tasks[self.target_task]["total"], 2)
        result = three_way.read_completed([self.source_task, self.target_task])
        groups = [g for g in result["groups"] if g["device_name"] == "网元甲"]
        self.assertTrue(groups)
        self.assertTrue(all(not g["consistent"] for g in groups))
        self.assertIn("调拨", json.dumps(result, ensure_ascii=False))

    def test_cross_room_scope_keeps_assets_local_and_deduplicates_outbound_notice(self):
        self.assets()
        saved = self.pair()
        for batch in ("batch-b", "batch-a"):
            with self.subTest(target_batch=batch):
                self.sql("UPDATE tasks SET import_batch_id=? WHERE id=?", (batch, self.target_task))
                source = three_way.read_completed([self.source_task])
                self.assertEqual(source["groups"], [], "迁出提示不能又生成一组资产表独有资源")
                self.assertEqual([p["id"] for p in source["inventory_pairings"]], [saved["id"]])
                self.assertEqual([a["barcode"] for a in source["scoped_assets"]], ["ASSET-A"])
                target = three_way.read_completed([self.target_task])
                self.assertEqual([a["barcode"] for a in target["scoped_assets"]], ["ASSET-B"])
                merged = next(g for g in target["groups"] if g["device_name"] == "网元甲")
                self.assertEqual(three_way.source_records(merged, "资产"), [], "只选目标机房时不能跨地点编码拉入资产行")
                self.assertEqual([r["location_code"] for r in three_way.source_records(merged, "综资")], ["001"])
                self.assertEqual([r["location_code"] for r in three_way.source_records(merged, "现场")], ["002"])
                combined = three_way.read_completed([self.source_task, self.target_task])
                self.assertEqual(len(combined["inventory_pairings"]), 1, "跨批汇总同一拼销提示只显示一次")
                self.assertEqual(len(combined["groups"]), 2)
                self.assertEqual(combined["summary"]["device_count"], 2)
                self.assertEqual({a["barcode"] for a in combined["scoped_assets"]}, {"ASSET-A", "ASSET-B"})

    def test_comparison_export_keeps_pairing_transfer_and_ai_masks_pairing_identity(self):
        self.assets()
        saved = self.pair(note="网管核实：机房甲设备已移到机房乙，联系人及内部序列号仅管理员留档")
        for scope in ([self.source_task], [self.target_task], [self.source_task, self.target_task]):
            with self.subTest(task_ids=scope):
                workbook = openpyxl.load_workbook(io.BytesIO(main.api_export_comparison(scope, self.admin).body))
                self.assertIn("拼销记录", workbook.sheetnames)
                rows = list(workbook["拼销记录"].values)
                self.assertEqual(len(rows), 2, "仅选迁出机房也应导出一条拼销提示")
                row = dict(zip(rows[0], rows[1]))
                self.assertEqual((row["设备名称"], row["原机房"], row["实际找到机房"]), ("网元甲", "机房甲", "机房乙"))
                self.assertEqual(row["确认依据"], saved["note"])
                self.assertIn("调拨", row["状态"])
        originals = ai_agent.build_inventory_rows(main.api_summary(self.admin))
        masked, mappings = ai_agent.desensitize_rows(originals)
        room_codes = {row["机房名称"]: masked[index]["机房名称"] for index, row in enumerate(originals)}
        paired_rows = [row for row in masked if row.get("拼销来源机房")]
        self.assertTrue(paired_rows)
        for row in paired_rows:
            self.assertEqual((row["拼销来源机房"], row["拼销去向机房"]), (room_codes["机房甲"], room_codes["机房乙"]))
            self.assertNotIn("拼销依据", row)
        encoded = json.dumps(masked, ensure_ascii=False)
        self.assertNotIn(saved["note"], encoded)
        self.assertNotIn("机房甲", encoded)
        self.assertNotIn("机房乙", encoded)
        self.assertEqual(mappings["拼销来源机房"][room_codes["机房甲"]], "机房甲")
        self.assertTrue(any(row.get("拼销依据") == saved["note"] for row in originals), "AI脱敏不能修改管理员原始留档数据")

    def test_paired_group_cannot_create_case_even_with_manual_confirmation(self):
        self.assets()
        self.pair()
        group = next(g for g in three_way.read_completed([self.target_task])["groups"] if g["device_name"] == "网元甲")
        for confirmation in (None, dict(problem_type="人工确认问题", basis="不应绕过拼销约束", suggestion="转整改")):
            with self.assertRaisesRegex(ValueError, "拼销"):
                three_way.create_case([self.target_task], group["group_key"], group["fingerprint"], self.admin["id"], confirmation)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_adjustment_tasks").fetchone()[0], 0)

    def test_baseline_change_then_restore_never_reactivates_pair_but_unrelated_edit_is_safe(self):
        three_way.replace_baseline(self.source_task, [dict(device_name="网元甲", expected_model="MODEL-A", expected_barcode="ASSET-A", room_code="001", device_extra={"设备机架": "R1"})], self.admin["id"])
        self.pair()
        original = self.enrich(self.missing)

        def edit(record_id, barcode):
            current = three_way.resource_record(record_id)
            values = {key: current[key] for key in ("expected_model", "expected_barcode", "expected_rack_no")}
            values["expected_barcode"] = barcode
            three_way.update_resource_record(record_id, values, current["fingerprint"], self.admin["id"], "管理员核实综资")

        edit(self.normal, "UNRELATED-B")
        self.assertTrue(self.active()[0]["valid"], "同机房无关设备的综资变化不能影响拼销")
        edit(self.missing, "TEMP-A")
        self.assertFalse(self.active()[0]["valid"])
        edit(self.missing, "ASSET-A")
        restored = self.enrich(self.missing)
        self.assertEqual(restored["expected_barcode"], original["expected_barcode"])
        self.assertNotEqual(restored["fingerprint"], original["fingerprint"])
        self.assertFalse(self.active()[0]["valid"], "改回旧值也需要管理员撤销并重新核实拼销")
        self.assertEqual(self.projected(self.source_task)[0]["record_type"], "missing")
        target = next(r for r in self.projected(self.target_task) if r["id"] == self.surplus)
        self.assertEqual((target["record_type"], target["pairing"]["role"]), ("surplus", "stale"))

    def token(self, user):
        return create_access_token(db.get_user_for_auth(user["id"]), "wechat" if user["role"] == "worker" else "password")

    async def request(self, method, path, payload=None, user=None):
        body = json.dumps(payload or {}).encode()
        headers = {"content-type": "application/json", "host": "127.0.0.1"}
        if user:
            headers["authorization"] = "Bearer " + self.token(user)
        url, _, query = path.partition("?")
        messages = []
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": method,
                 "scheme": "http", "path": url, "raw_path": url.encode(), "query_string": query.encode(), "root_path": "",
                 "headers": [(key.encode(), value.encode()) for key, value in headers.items()], "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8000)}
        await asyncio.wait_for(main.app(scope, receive, send), 5)
        status = next(item["status"] for item in messages if item["type"] == "http.response.start")
        content = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return status, json.loads(content)

    async def test_candidate_api_pages_keep_priority_filters_and_all_records_accessible(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.target_task,))
        for index in range(46):
            self.add_surplus(self.target_task, name=f"候选设备{index:02d}", barcode=f"EXTRA-{index:03d}", model="FILTER-M")
        self.finish(self.target_task)
        complete = pairing.candidates(self.missing)["candidates"]
        self.assertEqual(len(complete), 47)
        path = f"/api/admin/inventory-pairs/candidates?record_id={self.missing}&recommended_only=false&device_category=OTN&page_size=20"
        pages = []
        for page in (1, 2, 3):
            status, data = await self.request("GET", path + f"&page={page}", user=self.admin)
            self.assertEqual(status, 200, data)
            self.assertEqual((data["total"], data["page"], data["page_size"]), (47, page, 20))
            self.assertEqual(len(data["candidates"]), 20 if page < 3 else 7)
            pages.extend(data["candidates"])
        self.assertEqual([row["id"] for row in pages], [row["id"] for row in complete], "翻页不能漏掉候选或改变条码优先顺序")
        status, last = await self.request("GET", path + "&page=999", user=self.admin)
        self.assertEqual((status, last["page"], len(last["candidates"])), (200, 3, 7))
        status, filtered = await self.request("GET", path + "&keyword=EXTRA&page=2", user=self.admin)
        self.assertEqual((status, filtered["total"], filtered["page"]), (200, 46, 2))
        expected = pairing.candidates(self.missing, keyword="EXTRA")["candidates"]
        self.assertEqual([row["id"] for row in filtered["candidates"]], [row["id"] for row in expected[20:40]])
        status, empty = await self.request("GET", path + "&same_room=true&page=3", user=self.admin)
        self.assertEqual((status, empty["total"], empty["page"], empty["candidates"]), (200, 0, 1, []))
        source, chosen = self.enrich(self.missing), pages[25]
        status, saved = await self.request("POST", "/api/admin/inventory-pairs", dict(
            missing_record_id=source["id"], surplus_record_id=chosen["id"], missing_fingerprint=source["fingerprint"],
            surplus_fingerprint=chosen["fingerprint"], note="已核实第二页候选的网管身份和实物"), self.admin)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["pairing"]["surplus_record_id"], chosen["id"])

    async def test_candidate_pagination_limits_and_paired_record_detail(self):
        path = f"/api/admin/inventory-pairs/candidates?record_id={self.missing}"
        for query in ("page=0", "page=-1", "page_size=0", "page_size=101", "page=abc"):
            status, _ = await self.request("GET", path + "&" + query, user=self.admin)
            self.assertEqual(status, 422)
        saved = self.pair()
        status, result = await self.request("GET", path, user=self.collaborator)
        self.assertEqual((status, result["total"], result["page"], result["candidates"]), (200, 0, 1, []))
        self.assertEqual(result["pairing"]["id"], saved["id"], "分页不能破坏查看拼销或撤销所需的详情")

    async def test_default_recommendations_are_capped_and_unrelated_records_are_not_enriched(self):
        self.sql("UPDATE tasks SET status='doing' WHERE id=?", (self.target_task,))
        for index in range(22):
            self.add_surplus(self.target_task, name=f"推荐候选{index:02d}", barcode="ASSET-A")
        for index in range(20):
            self.add_surplus(self.target_task, name=f"其他设备{index:02d}", barcode=f"OTHER-{index}")
        self.finish(self.target_task)
        with patch.object(pairing, "_record", wraps=pairing._record) as read_record:
            status, result = await self.request("GET", f"/api/admin/inventory-pairs/candidates?record_id={self.missing}&page_size=100", user=self.admin)
        self.assertEqual((status, result["recommended_only"], result["total"], len(result["candidates"])), (200, True, 15, 15))
        self.assertTrue(all(row["match_reason"] == "设备资产条码一致" for row in result["candidates"]))
        self.assertLessEqual(read_record.call_count, 16, "只为来源和推荐的15条读取完整修正、历史和指纹，不整理所有候选")
        status, _ = await self.request("GET", f"/api/admin/inventory-pairs/candidates?record_id={self.missing}&recommended_only=false", user=self.admin)
        self.assertEqual(status, 400, "管理员未填查询条件时不能加载全部盘亏盘盈")

    async def test_no_recommendation_waits_for_search_and_name_match_is_recommended(self):
        self.sql("UPDATE scan_records SET expected_barcode='UNRELATED' WHERE id=?", (self.missing,))
        path = f"/api/admin/inventory-pairs/candidates?record_id={self.missing}"
        status, result = await self.request("GET", path, user=self.admin)
        self.assertEqual((status, result["total"], result["candidates"]), (200, 0, []), "仅型号相同不能推荐")
        status, found = await self.request("GET", path + "&recommended_only=false&keyword=ASSET-A", user=self.admin)
        self.assertEqual((status, found["recommended_only"], found["total"]), (200, False, 1))
        self.assertEqual(found["candidates"][0]["id"], self.surplus)
        self.sql("UPDATE scan_records SET device_name='网元甲' WHERE id=?", (self.surplus,))
        status, named = await self.request("GET", path, user=self.admin)
        self.assertEqual((status, named["total"]), (200, 1))
        self.assertIn("名称一致", named["candidates"][0]["match_reason"])

    async def test_api_full_collaborator_authorization_and_field_domain_boundary(self):
        path = "/api/admin/inventory-pairs/candidates?record_id=" + str(self.missing)
        for user in (self.admin, self.collaborator):
            status, data = await self.request("GET", path, user=user)
            self.assertEqual(status, 200, data)
            self.assertEqual(data["source"]["id"], self.missing)
        for user in (None, self.worker):
            status, _ = await self.request("GET", path, user=user)
            self.assertIn(status, (401, 403))
        missing, surplus = self.enrich(self.missing), self.enrich(self.surplus)
        payload = dict(missing_record_id=self.missing, surplus_record_id=self.surplus, missing_fingerprint=missing["fingerprint"], surplus_fingerprint=surplus["fingerprint"], note="已核实设备序列号")
        for user in (None, self.worker):
            status, _ = await self.request("POST", "/api/admin/inventory-pairs", payload, user)
            self.assertIn(status, (401, 403))
        self.assertEqual(self.active(), [])
        status, data = await self.request("POST", "/api/admin/inventory-pairs", payload, self.collaborator)
        self.assertEqual(status, 200, data)
        pair_id = data["pairing"]["id"]
        for user in (None, self.worker):
            status, _ = await self.request("POST", f"/api/admin/inventory-pairs/{pair_id}/cancel", {"note": "未授权撤销"}, user)
            self.assertIn(status, (401, 403))
        self.assertEqual(len(self.active()), 1)
        status, data = await self.request("POST", f"/api/admin/inventory-pairs/{pair_id}/cancel", {"note": "撤销重新核实"}, self.collaborator)
        self.assertEqual(status, 200, data)
        self.assertTrue(admin_request_allowed(self.collaborator, "POST", "/api/admin/inventory-pairs"))
        self.assertFalse(main._is_field_or_public_api_path("POST", "/api/admin/inventory-pairs"))
        self.assertFalse(main._is_field_or_public_api_path("GET", "/api/admin/inventory-pairs/candidates"))


if __name__ == "__main__":
    unittest.main()
