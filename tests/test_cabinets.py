import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
import openpyxl
from backend import database, main
from backend.models import AdminRackCorrectionRequest, CabinetCreateRequest, CaptureVoidRequest


class CabinetRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()

        conn = database.get_conn()
        self.worker_id = conn.execute(
            "INSERT INTO users (wx_openid, name, role, area_name) VALUES ('worker', '现场人员', 'worker', '南区')"
        ).lastrowid
        self.task_id = conn.execute(
            """INSERT INTO tasks (task_name, room_name, area_name, status, created_at, claimed_by)
               VALUES ('任务', '机房A', '南区', 'doing', '2026-10-12T08:00:00', ?)""",
            (self.worker_id,),
        ).lastrowid
        self.record_id = conn.execute(
            """INSERT INTO scan_records (task_id, device_name, expected_model, expected_barcode)
               VALUES (?, '设备1', 'MODEL-A', 'ASSET-1')""",
            (self.task_id,),
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def test_device_submission_registers_equipment_cabinet_once(self):
        database.submit_scan(
            self.record_id,
            "ASSET-1",
            False,
            "cab-001",
            False,
            "R01",
            "MODEL-A",
            "",
            self.worker_id,
        )
        database.submit_scan(
            self.record_id,
            "ASSET-1",
            False,
            "CAB-001",
            False,
            "R01",
            "MODEL-A",
            "",
            self.worker_id,
        )

        cabinets = database.get_task_cabinets(self.task_id)
        self.assertEqual(len(cabinets), 1)
        self.assertEqual(cabinets[0]["cabinet_barcode"], "CAB-001")
        self.assertEqual(cabinets[0]["cabinet_type"], "equipment")

    def test_manual_cabinet_deduplicates_barcode_and_request(self):
        first, created = database.create_cabinet(
            self.task_id, "odf", "ODF-01", False, "A01", "", self.worker_id, "request-1"
        )
        same_request, request_created = database.create_cabinet(
            self.task_id, "odf", "ODF-01", False, "A01", "", self.worker_id, "request-1"
        )
        same_barcode, barcode_created = database.create_cabinet(
            self.task_id, "odf", "odf-01", False, "A01", "", self.worker_id, "request-2"
        )

        self.assertTrue(created)
        self.assertFalse(request_created)
        self.assertFalse(barcode_created)
        self.assertEqual(first["id"], same_request["id"])
        self.assertEqual(first["id"], same_barcode["id"])
        self.assertEqual(len(database.get_task_cabinets(self.task_id)), 1)

    def test_rack_identity_conflicts_are_not_silently_deduplicated(self):
        database.create_cabinet(self.task_id, "odf", "R1", False, "A01", "", self.worker_id)
        for kind, number in [("odf", "A02"), ("iodf", "A01")]:
            with self.subTest(kind=kind, number=number):
                with self.assertRaisesRegex(database.CaptureConflictError, "已登记为 ODF 机架 A01"):
                    database.create_cabinet(self.task_id, kind, "R1", False, number, "", self.worker_id)
        with self.assertRaises(HTTPException) as caught:
            main.api_create_cabinet(CabinetCreateRequest(task_id=self.task_id, cabinet_type="iodf", cabinet_barcode="R1", rack_no="A02"), database.get_user_by_id(self.worker_id))
        self.assertEqual(caught.exception.status_code, 409)

    def test_collection_warnings_use_effective_records_and_allow_shared_cabinet(self):
        database.submit_scan(self.record_id, "ASSET-1", False, "CAB1", False, "A01", "M", "", self.worker_id)
        conn = database.get_conn()
        second = conn.execute("INSERT INTO scan_records(task_id,device_name,expected_model) VALUES(?,'设备2','M')", (self.task_id,)).lastrowid
        conn.commit()
        conn.close()
        database.submit_scan(second, "ASSET-2", False, "CAB1", False, "A01", "M", "", self.worker_id)
        self.assertEqual(database.get_collection_warnings(self.task_id, record_id=second), [])
        database.submit_scan(second, "ASSET-2", False, "CAB1", False, "A02", "M", "", self.worker_id)
        self.assertIn("同一机柜条码 CAB1 对应不同柜位", database.get_collection_warnings(self.task_id, record_id=second)[0])
        database.submit_scan(second, "ASSET-2", False, "CAB2", False, "A01", "M", "", self.worker_id)
        self.assertIn("同一柜位 A01 对应不同机柜条码", database.get_collection_warnings(self.task_id, record_id=second)[0])
        admin = database.create_user("capture-admin", "管理员", "admin")["id"]
        database.create_record_correction(second, dict(actual_model="M", asset_barcodes="ASSET-2", cabinet_barcodes="CAB1", rack_no="A01"), "现场录入错误", "", admin)
        self.assertEqual(database.get_collection_warnings(self.task_id, record_id=second), [])
        # 旧 cabinet_records 仍有 CAB2 缓存，但有效设备记录已经更正，不应阻止机架使用。
        cabinet, created = database.create_cabinet(self.task_id, "odf", "CAB2", False, "ODF-A01", "", self.worker_id)
        self.assertTrue(created)
        self.assertEqual(cabinet["cabinet_barcode"], "CAB2")
        with self.assertRaisesRegex(database.CaptureConflictError, "传输综合柜"):
            database.create_cabinet(self.task_id, "odf", "CAB1", False, "ODF-A02", "", self.worker_id)

    def test_cross_type_barcode_warning_returns_after_successful_device_scan(self):
        database.create_cabinet(self.task_id, "iodf", "RACK1", False, "A01", "", self.worker_id)
        result = main.api_scan(main.ScanSubmitRequest(record_id=self.record_id, actual_model="M", asset_barcodes="RACK1", cabinet_barcodes="RACK1", rack_no="A02"), database.get_user_by_id(self.worker_id))
        self.assertTrue(result["success"])
        self.assertTrue(any("IODF" in message and "设备1" in message for message in result["warnings"]))
        self.assertTrue(any("传输综合柜" in message for message in result["warnings"]))

    def test_multicabinet_feedback_does_not_guess_barcode_position_pairs(self):
        with database.get_conn() as conn:
            second = conn.execute(
                "INSERT INTO scan_records(task_id,device_name,expected_model) VALUES(?,'单柜设备','M')",
                (self.task_id,),
            ).lastrowid
        database.submit_scan(second, "ASSET-2", False, "CAB1", False, "AA01A", "M", "", self.worker_id)
        for positions in ("AA01A、AA02A", "AA01A,AA02A", "AA01A；AA02A\nAA03A"):
            with self.subTest(positions=positions):
                database.submit_scan(self.record_id, "ASSET-1", False, "CAB1,CAB2", False,
                                     positions, "M", "", self.worker_id)
                self.assertEqual(database.get_collection_warnings(self.task_id, record_id=self.record_id), [])
                self.assertEqual(database.get_collection_warnings(self.task_id, record_id=second), [])
        database.submit_scan(self.record_id, "ASSET-1", False, "CAB1,CAB2", False,
                             "AA01A", "M", "", self.worker_id)
        self.assertTrue(any("同一柜位 AA01A 对应不同机柜条码" in warning
                            for warning in database.get_collection_warnings(self.task_id, record_id=self.record_id)))

    def test_multicabinet_feedback_keeps_device_and_cross_type_barcode_conflicts(self):
        rack, _ = database.create_cabinet(self.task_id, "iodf", "RACK1", False, "ODF-A01", "", self.worker_id)
        database.submit_scan(self.record_id, "DUPLICATE", False, "RACK1,CAB2", False,
                             "AA01A、AA02A", "M", "", self.worker_id)
        with database.get_conn() as conn:
            second = conn.execute(
                "INSERT INTO scan_records(task_id,device_name,expected_model) VALUES(?,'设备2','M')",
                (self.task_id,),
            ).lastrowid
        database.submit_scan(second, "DUPLICATE", False, "CAB2", False, "AA02A", "M", "", self.worker_id)
        warnings = database.get_collection_warnings(self.task_id, record_id=self.record_id)
        self.assertTrue(any("条码 DUPLICATE 重复使用" in warning for warning in warnings))
        self.assertTrue(any("IODF" in warning and "RACK1" in warning for warning in warnings))
        self.assertFalse(any("同一柜位" in warning or "同一机柜条码" in warning for warning in warnings))
        self.assertTrue(any("传输综合柜" in warning
                            for warning in database.get_collection_warnings(self.task_id, cabinet_id=rack["id"])))

    def test_admin_rack_changes_after_completion_are_effective_and_audited(self):
        admin = database.create_user("rack-admin", "管理员", "admin")["id"]
        rack, _ = database.create_cabinet(self.task_id, "odf", "OLD", False, "A01", "", self.worker_id)
        conn = database.get_conn()
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (self.task_id,))
        conn.commit()
        conn.close()
        values = dict(cabinet_type="iodf", cabinet_barcode="NEW", no_barcode=False, rack_no="A02", notes="修正", correction_reason="误录更正")
        updated = database.save_admin_rack(self.task_id, values, admin, rack["id"])
        self.assertEqual((updated["cabinet_type"], updated["cabinet_barcode"], updated["rack_no"]), ("iodf", "NEW", "A02"))
        self.assertTrue(updated["has_admin_correction"])
        extra = database.save_admin_rack(self.task_id, {**values, "cabinet_barcode": "EXTRA", "rack_no": "A03"}, admin)
        self.assertEqual(database.get_task_cabinet_data(self.task_id)["summary"]["iodf"], 2)
        self.assertTrue(database.void_capture_record("cabinet_records", rack["id"], admin, "误录作废", self.task_id))
        self.assertEqual([r["id"] for r in database.get_task_cabinets(self.task_id)], [extra["id"]])
        with self.assertRaisesRegex(ValueError, "完成"):
            database.delete_task_rack(self.task_id, extra["id"], self.worker_id)
        actions = [a["action"] for a in database.get_audit_logs()]
        self.assertIn("cabinet.correct", actions)
        self.assertIn("cabinet.admin_create", actions)
        self.assertIn("cabinet.void", actions)
        conn = database.get_conn()
        self.assertEqual(conn.execute("SELECT cabinet_barcode,is_void FROM cabinet_records WHERE id=?", (rack["id"],)).fetchone()["is_void"], 1)
        conn.close()

    def test_admin_rack_api_updates_completed_view_and_excludes_void_history_reference(self):
        admin = database.create_user("rack-api-admin", "管理员", "admin")
        conn = database.get_conn()
        conn.execute("UPDATE tasks SET status='done',room_code='ROOM-A' WHERE id=?", (self.task_id,))
        conn.commit()
        conn.close()
        payload = AdminRackCorrectionRequest(cabinet_type="odf", cabinet_barcode="RACK-A", rack_no="A01", correction_reason="补录漏盘")
        created = main.api_admin_create_rack(self.task_id, payload, admin)
        rack_id = created["cabinet"]["id"]
        payload.rack_no = "A02"
        payload.correction_reason = "编号录错"
        main.api_admin_update_rack(self.task_id, rack_id, payload, admin)
        worker = database.get_user_by_id(self.worker_id)
        current = main.api_task_items(self.task_id, worker)
        self.assertEqual(current["cabinet_records"][0]["rack_no"], "A02")
        self.assertTrue(current["items"][0]["write_locked"])
        main.api_admin_void_rack(self.task_id, rack_id, CaptureVoidRequest(reason="重复补录"), admin)
        self.assertEqual(main.api_report(self.task_id, worker)["cabinet_records"], [])
        conn = database.get_conn()
        next_task = conn.execute("""INSERT INTO tasks(task_name,room_name,room_code,area_name,status,created_at)
                                   VALUES('下次','机房A','ROOM-A','南区','pending','2026-10-28T00:00:00')""").lastrowid
        conn.commit()
        conn.close()
        self.assertEqual(database.get_prior_room_racks(next_task), [])
        self.assertTrue(main._is_field_or_public_api_path("DELETE", f"/api/tasks/{self.task_id}/cabinets/{rack_id}"))
        self.assertFalse(main._is_field_or_public_api_path("DELETE", f"/api/admin/tasks/{self.task_id}/racks/{rack_id}"))

    def test_equipment_cabinet_cannot_be_registered_as_separate_rack(self):
        with self.assertRaisesRegex(ValueError, "传输综合柜请随设备盘点提交"):
            database.create_cabinet(
                self.task_id, "equipment", "TC-01", False, "A01", "", self.worker_id, "request-equipment"
            )

    def test_cabinet_allows_no_barcode_with_rack_number(self):
        cabinet, created = database.create_cabinet(
            self.task_id, "iodf", "", True, "A01", "", self.worker_id, "request-no-barcode"
        )

        self.assertTrue(created)
        self.assertTrue(cabinet["no_barcode"])
        self.assertEqual(cabinet["rack_no"], "A01")

    def test_cabinet_requires_barcode_or_no_barcode_and_rack_number(self):
        for barcode, rack_no, expected_message in [
            ("", "A01", "请填写机架条码"),
            ("IODF-01", "", "请填写机架编号"),
        ]:
            with self.subTest(barcode=barcode, rack_no=rack_no):
                with self.assertRaisesRegex(ValueError, expected_message):
                    database.create_cabinet(
                        self.task_id, "iodf", barcode, False, rack_no, "", self.worker_id,
                        f"request-{barcode}-{rack_no}",
                    )

    def test_same_room_across_reimported_tasks_has_independent_cabinet_results(self):
        conn = database.get_conn()
        second_task_id = conn.execute(
            """INSERT INTO tasks (task_name, room_name, area_name, status, created_at, claimed_by)
               VALUES ('重新导入', '机房A', '南区', 'doing', '2026-10-12T09:00:00', ?)""",
            (self.worker_id,),
        ).lastrowid
        conn.commit()
        conn.close()

        first, first_created = database.create_cabinet(
            self.task_id, "odf", "ODF-REIMPORT", False, "A01", "", self.worker_id, "request-5"
        )
        same, second_created = database.create_cabinet(
            second_task_id, "odf", "odf-reimport", False, "A02", "", self.worker_id, "request-6"
        )

        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertNotEqual(first["id"], same["id"])
        self.assertEqual(len(database.get_task_cabinets(self.task_id)), 1)
        self.assertEqual(len(database.get_task_cabinets(second_task_id)), 1)
        self.assertEqual(database.get_task_cabinets(second_task_id)[0]["rack_no"], "A02")

    def test_prior_completed_room_racks_are_reference_only(self):
        database.create_cabinet(self.task_id, "odf", "ODF-01", False, "A01", "", self.worker_id)
        conn = database.get_conn()
        conn.execute("UPDATE tasks SET status='done', completed_at='2026-10-12T10:00:00', room_code='LOC-A' WHERE id=?", (self.task_id,))
        next_task = conn.execute(
            """INSERT INTO tasks (task_name, room_name, room_code, area_name, status, created_at, claimed_by)
               VALUES ('下次盘点', '机房A', 'LOC-A', '南区', 'doing', '2026-10-13T08:00:00', ?)""",
            (self.worker_id,),
        ).lastrowid
        conn.commit()
        conn.close()

        reference = database.get_prior_room_racks(next_task)
        self.assertEqual([(r["cabinet_type"], r["rack_no"]) for r in reference], [("odf", "A01")])
        self.assertEqual(database.get_task_cabinets(next_task), [])

    def test_delete_rack_updates_counts_and_preserves_audit_and_device(self):
        rack,_=database.create_cabinet(self.task_id,"odf","R1",False,"A01","误录",self.worker_id,"create-rack")
        database.create_cabinet(self.task_id,"iodf","R2",False,"A02","",self.worker_id)
        self.assertTrue(database.delete_task_rack(self.task_id,rack["id"],self.worker_id))
        self.assertFalse(database.delete_task_rack(self.task_id,rack["id"],self.worker_id))
        self.assertEqual(database.get_task_cabinet_data(self.task_id)["summary"],dict(total=1,equipment=0,odf=0,iodf=1))
        self.assertEqual(database.get_task_items(self.task_id)[0]["id"],self.record_id)
        audits=[a for a in database.get_audit_logs() if a["action"]=="cabinet.delete"]
        self.assertEqual(len(audits),1)
        self.assertEqual(audits[0]["actor_id"],self.worker_id)
        snapshot=audits[0]["detail"]
        self.assertEqual((snapshot["task_id"],snapshot["cabinet_barcode"],snapshot["cabinet_name"]),(self.task_id,"R1","A01"))
        new,created=database.create_cabinet(self.task_id,"odf","R1",False,"A03","",self.worker_id,"corrected-rack")
        self.assertTrue(created)
        self.assertNotEqual(new["id"],rack["id"])

    def test_delete_requires_current_task_owner_and_unlocked_task(self):
        rack,_=database.create_cabinet(self.task_id,"iodf","R1",False,"A01","",self.worker_id)
        other=database.create_user("other-worker","其他现场","worker","南区")["id"]
        with self.assertRaisesRegex(ValueError,"本人"):
            database.delete_task_rack(self.task_id,rack["id"],other)
        other_task=database.create_task("另一任务","机房B",self.worker_id,"南区")
        self.assertFalse(database.delete_task_rack(other_task,rack["id"],self.worker_id))
        conn=database.get_conn()
        conn.execute("UPDATE tasks SET status='done' WHERE id=?",(self.task_id,))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(ValueError,"完成锁定"):
            database.delete_task_rack(self.task_id,rack["id"],self.worker_id)
        self.assertEqual(len(database.get_task_cabinets(self.task_id)),1)

    def test_field_cannot_delete_admin_corrected_or_supplemented_racks(self):
        admin = database.create_user("rack-delete-admin", "管理员", "admin")["id"]
        rack, _ = database.create_cabinet(self.task_id, "odf", "OLD", False, "AA01A", "", self.worker_id)
        values = dict(cabinet_type="odf", cabinet_barcode="CORRECTED", no_barcode=False,
                      rack_no="AA02A", correction_reason="核实更正")
        corrected = database.save_admin_rack(self.task_id, values, admin, rack["id"])
        supplemented = database.save_admin_rack(self.task_id, {
            **values, "cabinet_type": "iodf", "cabinet_barcode": "SUPPLEMENTED", "rack_no": "AA03A",
        }, admin)
        worker = database.get_user_by_id(self.worker_id)
        for current in (corrected, supplemented):
            with self.subTest(rack_id=current["id"]):
                with self.assertRaises(HTTPException) as caught:
                    main.api_delete_task_rack(self.task_id, current["id"], worker)
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn("管理员", caught.exception.detail)
        self.assertEqual({item["id"] for item in database.get_task_cabinets(self.task_id)},
                         {corrected["id"], supplemented["id"]})
        self.assertFalse(any(item["action"] == "cabinet.delete" for item in database.get_audit_logs()))
        for current in (corrected, supplemented):
            self.assertTrue(database.void_capture_record("cabinet_records", current["id"], admin,
                                                        "管理员核实作废", self.task_id))
        self.assertEqual(database.get_task_cabinets(self.task_id), [])

    def test_delete_cannot_remove_device_cabinet(self):
        database.submit_scan(self.record_id,"ASSET-1",False,"CAB-1",False,"A01","MODEL-A","",self.worker_id)
        cabinet=database.get_task_cabinets(self.task_id)[0]
        with self.assertRaisesRegex(ValueError,"ODF / IODF"):
            database.delete_task_rack(self.task_id,cabinet["id"],self.worker_id)
        self.assertEqual(database.get_task_cabinets(self.task_id)[0]["id"],cabinet["id"])

    def test_delete_rolls_back_when_audit_cannot_be_saved(self):
        rack,_=database.create_cabinet(self.task_id,"odf","R1",False,"A01","",self.worker_id)
        conn=database.get_conn()
        conn.execute("""CREATE TRIGGER reject_rack_delete_audit BEFORE INSERT ON audit_logs
                        WHEN NEW.action='cabinet.delete' BEGIN SELECT RAISE(ABORT,'audit failure'); END""")
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.IntegrityError):
            database.delete_task_rack(self.task_id,rack["id"],self.worker_id)
        self.assertEqual(database.get_task_cabinets(self.task_id)[0]["id"],rack["id"])

    def test_api_delete_rejects_replaying_deleted_registration(self):
        user=database.get_user_by_id(self.worker_id)
        req=CabinetCreateRequest(task_id=self.task_id,cabinet_type="odf",cabinet_barcode="R1",rack_no="A01",client_request_id="register-once")
        rack=main.api_create_cabinet(req,user)["cabinet"]
        self.assertTrue(main.api_delete_task_rack(self.task_id,rack["id"],user)["deleted"])
        self.assertFalse(main.api_delete_task_rack(self.task_id,rack["id"],user)["deleted"])
        with self.assertRaises(HTTPException) as error:
            main.api_create_cabinet(req,user)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(database.get_task_cabinets(self.task_id),[])
        corrected=main.api_create_cabinet(req.model_copy(update={"rack_no":"A02","client_request_id":"register-corrected"}),user)
        self.assertTrue(corrected["created"])
        self.assertEqual(corrected["cabinet"]["rack_no"],"A02")

    def test_equipment_summary_reads_legacy_device_without_registry(self):
        database.submit_scan(self.record_id,"ASSET-1",False,"CAB-1",False,"A01","MODEL-A","",self.worker_id)
        conn=database.get_conn()
        conn.execute("DELETE FROM cabinet_records WHERE task_id=?",(self.task_id,))
        conn.commit()
        conn.close()
        data=database.get_task_cabinet_data(self.task_id)
        self.assertEqual(data["summary"],dict(total=1,equipment=1,odf=0,iodf=0))
        cabinet=data["cabinets"][0]
        self.assertEqual((cabinet["cabinet_barcode"],cabinet["rack_no"]),("CAB-1","A01"))
        self.assertEqual(cabinet["created_by_name"],"现场人员")
        self.assertEqual(database.get_task_cabinets(self.task_id),[],"汇总不得回写或迁移历史原始数据")

    def test_summary_deduplicates_shared_barcode_and_unbarcoded_position(self):
        database.batch_create_records(self.task_id,[dict(device_name=f"设备{i}",expected_model="MODEL-A",expected_barcode=f"ASSET-{i}") for i in (2,3,4)])
        records=database.get_task_items(self.task_id)
        for index,record in enumerate(records):
            database.submit_scan(record["id"],f"ASSET-{index+1}",False,"CAB-1" if index<2 else "",index>=2,"A01" if index<2 else "A02","MODEL-A","",self.worker_id)
        database.create_cabinet(self.task_id,"odf","ODF-1",False,"ODF-A01","",self.worker_id)
        data=database.get_task_cabinet_data(self.task_id)
        self.assertEqual(data["summary"],dict(total=3,equipment=2,odf=1,iodf=0))
        equipment=[c for c in data["cabinets"] if c["cabinet_type"]=="equipment"]
        self.assertEqual(len({c["id"] for c in equipment}),2)
        self.assertEqual({(c["cabinet_barcode"],c["rack_no"]) for c in equipment},{("CAB-1","A01"),("","A02")})

    def test_resubmit_and_missing_do_not_keep_stale_equipment_cabinets(self):
        database.submit_scan(self.record_id,"ASSET-1",False,"OLD",False,"A01","MODEL-A","",self.worker_id)
        database.submit_scan(self.record_id,"ASSET-1",False,"NEW",False,"A02","MODEL-A","",self.worker_id)
        data=database.get_task_cabinet_data(self.task_id)
        self.assertEqual([c["cabinet_barcode"] for c in data["cabinets"]],["NEW"])
        self.assertEqual(data["cabinets"][0]["rack_no"],"A02")
        database.mark_record_missing(self.record_id,"现场未找到",created_by=self.worker_id)
        self.assertEqual(database.get_task_cabinet_data(self.task_id)["summary"]["equipment"],0)

    def test_correction_changes_report_miniprogram_and_export_together(self):
        database.submit_scan(self.record_id,"ASSET-1",False,"OLD",False,"A01","MODEL-A","",self.worker_id)
        admin=database.create_user("summary-admin","审核管理员","admin","南区")
        values=dict(actual_model="MODEL-A",asset_barcodes="ASSET-1",asset_no_barcode=False,
                    cabinet_barcodes="NEW",cabinet_no_barcode=False,rack_no="A02",notes="",record_type="normal")
        correction=database.create_record_correction(self.record_id,values,"条码录入错误","已核实",admin["id"])
        responses=[main.api_report(self.task_id,admin),main.api_task_items(self.task_id,admin)]
        for response in responses:
            cabinets=response["cabinet_records"]
            self.assertEqual(len(cabinets),1)
            self.assertEqual((cabinets[0]["cabinet_barcode"],cabinets[0]["rack_no"]),("NEW","A02"))
            self.assertEqual(cabinets[0]["created_by_name"],"审核管理员")
            self.assertEqual(cabinets[0]["scanned_at"],correction["corrected_at"])
            self.assertIn("管理员修正",cabinets[0]["source_label"])
        summary=next(t for t in main.api_summary(admin) if t["id"]==self.task_id)
        self.assertEqual(summary["cabinets"][0]["cabinet_barcode"],summary["records"][0]["cabinet_barcodes"])
        for response in (main.api_export(self.task_id,admin),main.api_ai_export_inventory([self.task_id],admin)):
            workbook=openpyxl.load_workbook(io.BytesIO(response.body))
            rows=list(workbook["传输综合柜明细"].values)
            self.assertEqual(len(rows),2)
            row=dict(zip(rows[0],rows[1]))
            self.assertEqual((row["机柜资产条码"],row["设备机柜位"]),("NEW","A02"))
            self.assertEqual(row["登记人"],"审核管理员")
        raw=database.get_record_by_id(self.record_id)
        self.assertEqual(raw["original_cabinet_barcodes"],"OLD")
        values.update(cabinet_no_barcode=True,cabinet_barcodes="",rack_no="A03")
        database.create_record_correction(self.record_id,values,"条码录入错误","确认无条码",admin["id"])
        cabinet=database.get_task_cabinet_data(self.task_id)["cabinets"][0]
        self.assertTrue(cabinet["no_barcode"])
        self.assertEqual((cabinet["cabinet_barcode"],cabinet["rack_no"]),("","A03"))

    def test_same_barcode_with_different_positions_retains_both_for_review(self):
        database.batch_create_records(self.task_id,[dict(device_name="设备2",expected_model="MODEL-A",expected_barcode="ASSET-2")])
        for index,record in enumerate(database.get_task_items(self.task_id)):
            database.submit_scan(record["id"],f"ASSET-{index+1}",False,"CAB-1",False,f"A0{index+1}","MODEL-A","",self.worker_id)
        cabinets=database.get_task_cabinet_data(self.task_id)["cabinets"]
        self.assertEqual(len(cabinets),1)
        self.assertEqual(cabinets[0]["rack_no"],"A01、A02")
        self.assertIn("待核实",cabinets[0]["notes"])

    def test_unsubmitted_values_are_not_equipment_inventory(self):
        conn=database.get_conn()
        conn.execute("UPDATE scan_records SET cabinet_barcodes='DRAFT',rack_no='A01' WHERE id=?",(self.record_id,))
        conn.commit()
        conn.close()
        self.assertEqual(database.get_task_cabinet_data(self.task_id)["summary"]["equipment"],0)

    def test_export_same_cabinet_in_different_tasks_keeps_each_batch(self):
        second=database.create_task("第二批次","机房A",self.worker_id,"南区")
        database.batch_create_records(second,[dict(device_name="设备2",expected_model="MODEL-A",expected_barcode="ASSET-2")])
        second_record=database.get_task_items(second)[0]["id"]
        for record in (self.record_id,second_record):
            database.submit_scan(record,"ASSET-1",False,"CAB-1",False,"A01","MODEL-A","",self.worker_id)
        admin=database.create_user("export-admin","管理员","admin","南区")
        response=main.api_ai_export_inventory([self.task_id,second],admin)
        workbook=openpyxl.load_workbook(io.BytesIO(response.body))
        rows=list(workbook["传输综合柜明细"].values)
        self.assertEqual(len(rows),3)
        self.assertEqual({row[0] for row in rows[1:]},{self.task_id,second})


if __name__ == "__main__":
    unittest.main()
