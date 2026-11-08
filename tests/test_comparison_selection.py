"""完成机房选择与跨批次聚合的隔离回归测试。"""

import io
import json
import tempfile
import unittest
from pathlib import Path

import openpyxl
from fastapi import HTTPException

from backend import database as db, main, three_way as tw


class ComparisonSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / "selection.db"
        db.BACKUP_DIR = db.DB_DIR / "backups"
        db.init_db()
        self.worker = db.create_user("selection-worker", "现场人员", "worker", "南区")["id"]
        self.admin = db.create_user("selection-admin", "管理员", "admin", "南区")["id"]
        self.first, self.first_record = self.make_task("机房甲", "001", "batch-a", "2026-11-04T10:00:00", rack_barcode="R1")
        self.second, self.second_record = self.make_task("机房乙", "002", "batch-b", "2026-11-05T10:00:00", rack_barcode="R2")
        # 综资及现场故意使用相同网元名和条码；不同批次仍是独立盘点。
        self.assets([
            ("A1", "同名网元", "001", "M1"),
            ("B1", "同名网元", "002", "M1"),
            ("R1", "ODF-R1", "001", "光纤配线架"),
            ("R2", "ODF-R2", "002", "光纤配线架"),
            ("U1", "", "001", "备用型号"),
            ("U2", "", "002", "备用型号"),
            ("OUT", "其他机房设备", "999", "M9"),
        ])

    def tearDown(self):
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def sql(self, statement, args=()):
        with db.get_conn() as conn:
            conn.execute(statement, args)

    def make_task(self, room, code, batch, completed_at, *, done=True, rack_barcode=None):
        task = db.create_task(room + "盘点", room, self.worker, "南区")
        self.sql("UPDATE tasks SET import_batch_id=?,room_code=? WHERE id=?", (batch, code, task))
        db.batch_create_records(task, [dict(device_name="同名网元", expected_model="M1", expected_barcode="A1", device_extra={"设备机架": "C01"})])
        record = db.get_task_items(task)[0]["id"]
        db.submit_scan(record, "A1", False, "CAB-" + code, False, "C01", "M1", "现场结果", created_by=self.worker)
        if rack_barcode:
            db.create_cabinet(task, "odf", rack_barcode, False, "ODF-" + rack_barcode, "", self.worker)
        if done:
            db.complete_inventory_task(task, self.worker)
            self.sql("UPDATE tasks SET field_completed_at=?,completed_at=? WHERE id=?", (completed_at, completed_at, task))
        return task, record

    def assets(self, rows):
        return db.save_asset_info_snapshot(
            file_name="选择范围资产.xlsx", sheet_name="资产", barcode_column="资产条码",
            headers=["资产条码", "资源名称", "地点编码", "规格型号"],
            records=[dict(barcode=barcode, fields=dict(zip(["资产条码", "资源名称", "地点编码", "规格型号"], [barcode, name, code, model])))
                     for barcode, name, code, model in rows],
            imported_by=self.admin, imported_by_name="管理员",
        )

    def group_for(self, result, task_id):
        return next(group for group in result["groups"] if task_id in group["task_ids"])

    def assert_api_rejected(self, ids):
        for endpoint in (main.api_reconciliation, main.api_export_comparison):
            with self.subTest(endpoint=endpoint.__name__, ids=ids):
                with self.assertRaises(HTTPException) as caught:
                    endpoint(ids, {})
                self.assertEqual(caught.exception.status_code, 400)

    def test_default_selects_each_rooms_latest_completion_date_before_id(self):
        newer_id_older_completion, _ = self.make_task("机房甲", "001", "batch-history", "2026-11-03T10:00:00")
        self.assertGreater(newer_id_older_completion, self.first)
        for result in (tw.read([]), tw.read_completed([])):
            self.assertEqual({task["id"] for task in result["tasks"]}, {self.first, self.second})
            self.assertEqual(result["summary"]["task_count"], 2)
        latest, _ = self.make_task("机房甲", "001", "batch-latest", "2026-11-06T10:00:00")
        self.assertEqual({task["id"] for task in tw.read_completed([])["tasks"]}, {latest, self.second})

    def test_new_unfinished_task_does_not_hide_last_completed_room(self):
        unfinished, _ = self.make_task("机房甲", "001", "batch-in-progress", "", done=False)
        self.assertEqual(db.get_task_by_id(unfinished)["status"], "doing")
        result = tw.read_completed([])
        self.assertEqual({task["id"] for task in result["tasks"]}, {self.first, self.second})
        self.assertNotIn(unfinished, {task["id"] for task in result["tasks"]})
        self.assertEqual(len(tw.read([unfinished])["tasks"]), 1, "内部检查保留读取未完成任务的能力")

    def test_legacy_completion_without_dates_uses_created_at_and_id_tie_break(self):
        legacy, _ = self.make_task("机房甲", "001", "legacy", "")
        self.sql("UPDATE tasks SET field_completed_at=NULL,completed_at=NULL,created_at='2026-11-06T10:00:00' WHERE id=?", (legacy,))
        result = tw.read_completed([])
        self.assertIn(legacy, {task["id"] for task in result["tasks"]})
        tied, _ = self.make_task("机房甲", "001", "legacy-tied", "2026-11-06T10:00:00")
        self.assertIn(tied, {task["id"] for task in tw.read_completed([])["tasks"]})

    def test_cross_batch_same_names_and_barcodes_stay_in_separate_groups(self):
        result = tw.read_completed([self.first, self.second])
        self.assertEqual(result["summary"]["task_count"], 2)
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(len({group["group_key"] for group in result["groups"]}), 2)
        for task_id, code in ((self.first, "001"), (self.second, "002")):
            group = self.group_for(result, task_id)
            self.assertEqual(group["scope_task_ids"], [task_id], "编辑和建单保持本批次范围")
            self.assertEqual(group["task_ids"], [task_id])
            for system in ("综资", "现场", "资产"):
                sources = tw.source_records(group, system)
                self.assertEqual(len(sources), 1, "同名同条码不能跨批次串入另一机房")
                self.assertEqual(sources[0]["location_code"], code)
                if system != "资产":
                    self.assertEqual(sources[0]["task_id"], task_id)
            isolated = self.group_for(tw.read_completed([task_id]), task_id)
            self.assertEqual(group["group_key"], isolated["group_key"])
            self.assertEqual(group["fingerprint"], isolated["fingerprint"])

    def test_cross_batch_aggregate_asset_rack_and_unlinked_counts_are_exact(self):
        result = tw.read_completed([self.first, self.second])
        self.assertEqual(result["asset_counts"], dict(total=6, named=4, resource_names=4, device=2, cabinet=0, rack=2, unlinked=2))
        self.assertEqual({row["barcode"] for row in result["scoped_assets"]}, {"A1", "B1", "R1", "R2", "U1", "U2"})
        self.assertEqual({row["barcode"] for row in result["unlinked_assets"]}, {"U1", "U2"})
        self.assertEqual({row["task_id"] for row in result["rack_comparison"]}, {self.first, self.second})
        self.assertEqual(len(result["rack_comparison"]), 2)
        self.assertEqual(result["summary"]["device_count"], len(result["groups"]))
        self.assertEqual(result["summary"]["matched_count"] + result["summary"]["issue_count"], len(result["groups"]))

    def test_existing_and_new_manual_judgments_keep_batch_scope_and_case_creation_works(self):
        first = self.group_for(tw.read([self.first]), self.first)
        tw.save_decision([self.first], first["group_key"], first["fingerprint"], "model",
                         dict(field_status="一致", asset_status="一致", note="原批次已有判断"), self.admin)
        combined = tw.read_completed([self.first, self.second])
        first = self.group_for(combined, self.first)
        second = self.group_for(combined, self.second)
        self.assertTrue(first["consistent"], "跨批次一起查看不能让已确认的独立判断失效")
        self.assertEqual(second["field_model_status"], "待判断")
        tw.save_decision(second["scope_task_ids"], second["group_key"], second["fingerprint"], "model",
                         dict(field_status="不一致", asset_status="不一致", note="只判断第二批次"), self.admin)
        combined = tw.read_completed([self.first, self.second])
        self.assertTrue(self.group_for(combined, self.first)["consistent"])
        second = self.group_for(combined, self.second)
        self.assertEqual(second["field_model_status"], "不一致")
        case_id = tw.create_case(second["scope_task_ids"], second["group_key"], second["fingerprint"], self.admin)
        self.assertGreater(case_id, 0)
        with db.get_conn() as conn:
            row = conn.execute("SELECT source_task_ids_json,comparison_json FROM ai_adjustment_tasks WHERE id=?", (case_id,)).fetchone()
        self.assertEqual(json.loads(row["source_task_ids_json"]), [self.second])
        self.assertEqual(json.loads(row["comparison_json"])["task_ids"], [self.second])

    def test_export_contains_both_rooms_with_their_own_source_data(self):
        response = main.api_export_comparison([self.first, self.second], {})
        workbook = openpyxl.load_workbook(io.BytesIO(response.body))
        rows = list(workbook["三系统比对"].values)
        headers = rows[0]
        sources = [dict(zip(headers, row)) for row in rows[1:] if row[1] == "综资"]
        self.assertEqual({row["机房"] for row in sources}, {"机房甲", "机房乙"})
        self.assertEqual({row["地点编码"] for row in sources}, {"001", "002"})
        self.assertEqual(len(sources), 2)

    def test_scanned_but_not_confirmed_task_is_rejected_by_formal_api(self):
        unfinished, _ = self.make_task("机房丙", "003", "batch-doing", "", done=False)
        self.assertTrue(db.get_task_items(unfinished)[0]["scanned_at"])
        self.assertEqual(db.get_task_by_id(unfinished)["status"], "doing")
        self.assert_api_rejected([unfinished])
        self.assert_api_rejected([self.first, unfinished])

    def test_same_room_history_unknown_or_void_selection_is_rejected(self):
        history, _ = self.make_task("机房甲", "001", "batch-history", "2026-11-03T10:00:00")
        self.assert_api_rejected([self.first, history])
        self.assert_api_rejected([self.first, 999999])
        self.sql("UPDATE tasks SET is_deleted=1 WHERE id=?", (self.second,))
        self.assert_api_rejected([self.first, self.second])
        self.assertEqual({task["id"] for task in tw.read_completed([])["tasks"]}, {self.first})

    def test_same_effective_location_code_cannot_duplicate_assets_between_rooms(self):
        self.sql("UPDATE tasks SET room_code='001' WHERE id=?", (self.second,))
        self.assert_api_rejected([self.first, self.second])
        with self.assertRaises(ValueError):
            tw.read_completed([self.first, self.second])

    def test_latest_baseline_location_is_used_for_duplicate_room_validation(self):
        tw.replace_baseline(self.second, [dict(device_name="同名网元", expected_model="M1", expected_barcode="A1", room_code="001")], self.admin)
        self.assertEqual(db.get_task_by_id(self.second)["room_code"], "002", "基线地点修正不能改写历史任务地点")
        self.assert_api_rejected([self.first, self.second])


if __name__ == "__main__":
    unittest.main()
