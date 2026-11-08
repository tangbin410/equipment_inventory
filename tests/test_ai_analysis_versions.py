import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
import openpyxl

from backend import database, main
from backend.main import api_ai_analyze
from backend.models import AIAnalysisRequest


class AIAnalysisVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = (database.DB_DIR, database.DB_PATH, database.BACKUP_DIR)
        database.DB_DIR = root
        database.DB_PATH = root / "inventory.db"
        database.BACKUP_DIR = root / "backups"
        database.init_db()
        conn = database.get_conn()
        self.task_id = conn.execute(
            """INSERT INTO tasks
               (task_name, room_name, status, created_at, completed_at, field_completed_at)
               VALUES ('测试任务', '测试机房', 'done', '2026-08-01T08:00:00',
                       '2026-08-01T10:00:00', '2026-08-01T10:00:00')"""
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_DIR, database.DB_PATH, database.BACKUP_DIR = self.original_paths
        self.temp_dir.cleanup()

    def save_success(self, completed_at):
        return database.save_ai_analysis_result(
            task_ids=[self.task_id],
            analysis_scope="测试机房",
            model_provider="test",
            model_name="test-model",
            prompt_version="test",
            input_summary="",
            desensitized_payload="{}",
            context_meta="{}",
            analysis_result="[]",
            status="success",
            error_message="",
            created_by=None,
            task_completion_versions=[(self.task_id, completed_at)],
        )

    def test_same_completion_time_cannot_generate_twice(self):
        completed_at = "2026-08-01T10:00:00"
        first_id = self.save_success(completed_at)

        with self.assertRaisesRegex(ValueError, "已经生成过"):
            self.save_success(completed_at)

        versions = database.list_analyzed_ai_task_versions([(self.task_id, completed_at)])
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["analysis_id"], first_id)

    def test_new_completion_time_can_generate_again(self):
        first_completed_at = "2026-08-01T10:00:00"
        second_completed_at = "2026-08-02T10:00:00"
        self.save_success(first_completed_at)
        second_id = self.save_success(second_completed_at)

        versions = database.list_analyzed_ai_task_versions([
            (self.task_id, first_completed_at),
            (self.task_id, second_completed_at),
        ])
        self.assertEqual(len(versions), 2)
        self.assertIn(second_id, {item["analysis_id"] for item in versions})

    def test_unfinished_task_is_rejected_before_model_call(self):
        task = {
            "id": self.task_id,
            "room_name": "测试机房",
            "status": "doing",
            "field_completed_at": None,
        }
        with patch("backend.main._ai_status_payload", return_value={"enabled": True, "available": True}), \
             patch("backend.main._get_task_or_404", return_value=task), \
             patch("backend.main.get_report", return_value=[]), \
             patch("backend.main.analyze_inventory") as analyze:
            with self.assertRaises(HTTPException) as caught:
                api_ai_analyze(AIAnalysisRequest(task_ids=[self.task_id]), {"id": 1})

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("只能分析现场已完成", caught.exception.detail)
        analyze.assert_not_called()

    def save_history(self, task_ids, status="success"):
        return database.save_ai_analysis_result(
            task_ids=task_ids,analysis_scope="测试机房范围",model_provider="test",model_name="test-model",
            prompt_version="test",input_summary="设备比对摘要",desensitized_payload="DO_NOT_EXPOSE",
            context_meta="{}",analysis_result=json.dumps([{"问题类型":"条码待核实","整改建议":"管理员核实"}],ensure_ascii=False) if status=="success" else "[]",
            status=status,error_message="模拟模型超时" if status=="failed" else "",created_by=None)

    def test_history_filters_exact_task_ids_and_includes_joint_reports(self):
        own=self.save_history([self.task_id])
        joint=self.save_history([self.task_id,self.task_id+1])
        self.save_history([self.task_id+10])
        result=main.api_ai_results([self.task_id],1,20,{})
        self.assertEqual(result["total"],2)
        self.assertEqual([r["id"] for r in result["items"]],[joint,own])
        for item in result["items"]:
            self.assertNotIn("analysis_result",item)
            self.assertNotIn("desensitized_payload",item)
        self.assertEqual(database.list_ai_analysis_results([],1,1)["total"],3)
        second_page=database.list_ai_analysis_results([self.task_id],2,1)
        self.assertEqual([r["id"] for r in second_page["items"]],[own])

    def test_saved_report_can_be_viewed_and_downloaded_without_model_service(self):
        report_id=self.save_history([self.task_id])
        with patch("backend.main._ai_status_payload") as status,patch("backend.main.analyze_inventory") as analyze:
            report=main.api_ai_result(report_id,{})
            downloaded=main.api_download_ai_result(report_id,{})
        status.assert_not_called()
        analyze.assert_not_called()
        self.assertEqual(report["analysis_items"][0]["问题类型"],"条码待核实")
        self.assertEqual(report["task_ids"],[self.task_id])
        self.assertTrue(report["created_at"])
        self.assertNotIn("desensitized_payload",report)
        workbook=openpyxl.load_workbook(io.BytesIO(downloaded.body))
        self.assertEqual(workbook["整改清单"]["B2"].value,"条码待核实")
        with self.assertRaises(HTTPException) as error:
            main.api_ai_result(report_id+1000,{})
        self.assertEqual(error.exception.status_code,404)

    def test_failed_history_retains_error_for_inspection(self):
        report_id=self.save_history([self.task_id],"failed")
        report=main.api_ai_result(report_id,{})
        self.assertEqual(report["status"],"failed")
        self.assertEqual(report["error_message"],"模拟模型超时")
        self.assertEqual(report["analysis_items"],[])
        self.assertEqual(database.list_ai_analysis_results([self.task_id])["total"],1)

    def test_repeated_analysis_opens_existing_report_and_changed_data_creates_new_one(self):
        group=dict(group_key="网元甲",fingerprint="source-v1",model_status="待判断",manual_decision=None,versions={})
        comparison=dict(groups=[group],warnings=[])
        model_result=dict(model_provider="test",model_name="test-model",prompt_version="test",input_summary="摘要",
                          desensitized_payload="{}",analysis_items=[{"问题类型":"条码待核实"}])
        request=AIAnalysisRequest(task_ids=[self.task_id])
        with patch("backend.main._ai_status_payload",return_value={"enabled":True,"available":True}), \
             patch("backend.main.three_way.read",return_value=comparison), \
             patch("backend.main.analyze_inventory",return_value=model_result) as model:
            first=api_ai_analyze(request,{"id":None})
            second=api_ai_analyze(request,{"id":None})
            self.assertEqual(first["id"],second["id"])
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(second["analysis_items"],model_result["analysis_items"])
            self.assertEqual(model.call_count,1)
            self.assertEqual(database.list_ai_analysis_results([self.task_id])["total"],1)
            group["fingerprint"]="source-v2"
            third=api_ai_analyze(request,{"id":None})
            self.assertNotEqual(third["id"],first["id"])
            self.assertEqual(model.call_count,2)
            self.assertEqual(database.list_ai_analysis_results([self.task_id])["total"],2)


if __name__ == "__main__":
    unittest.main()
