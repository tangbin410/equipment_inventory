import json
import unittest
from unittest.mock import patch

from backend import ai_agent
from local_agent.app import analyze, chat, health
from local_agent.context_policy import MAX_CONTEXT_ROWS, apply_context_policy
from local_agent.prompt_loader import BUSINESS_RULES_VERSION, load_inventory_business_rules, render_inventory_analysis_prompt
from local_agent.rules import ANALYSIS_KEYS, run_rules
from local_agent.tools.registry import run_tool


def model_response(content="[]"):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class ThreeWayAgentRulesTests(unittest.TestCase):
    def setUp(self):
        self.row = {"导入批次": "批次1", "机房名称": "机房A", "设备名称": "网元甲",
                    "清单资产条码": "001", "现场资产条码": "001", "盘点时间": "已盘",
                    "清单设备型号": "OSN-3500", "现场设备型号": "OptiX OSN3500", "现场状态": "正常"}
        self.context = {"rows": [self.row], "data_sources": ["综资", "现场"]}
        self.tasks = [{"id": 1, "import_batch_id": "批次1", "room_name": "机房A", "status": "done",
                       "records": [{"id": 1, "device_name": "网元甲", "expected_barcode": "001",
                                    "asset_barcodes": "001", "expected_model": "OSN-3500",
                                    "actual_model": "OptiX OSN3500", "scanned_at": "已盘", "match_status": "不一致"}]}]

    def assert_common_system_rules(self, messages):
        rules = load_inventory_business_rules()
        self.assertTrue(any(message.get("role") == "system" and rules in message.get("content", "") for message in messages))
        self.assertNotIn("{{business_rules}}", json.dumps(messages, ensure_ascii=False))

    def test_local_analysis_and_chat_receive_same_authoritative_rules(self):
        for action, payload in [(analyze, self.context), (chat, {"message": "解释三方比对与闭环规则", "context": self.context})]:
            with self.subTest(action=action.__name__), \
                 patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
                 patch("local_agent.app.post_deepseek_chat_response", return_value=model_response()) as model:
                result = action(payload)
                self.assert_common_system_rules(model.call_args.args[0])
                self.assertEqual(result["context_meta"]["business_rules_version"], BUSINESS_RULES_VERSION)
                self.assertEqual(result["context_meta"]["missing_data_sources"], ["资产"])

    def test_direct_analysis_and_chat_use_same_rules_and_report_missing_asset_input(self):
        for action in (lambda: ai_agent.analyze_inventory(self.tasks), lambda: ai_agent.chat_inventory("解释三方比对", self.tasks)):
            with patch("backend.ai_agent._external_agent_url", return_value=""), \
                 patch("backend.ai_agent._post_deepseek_chat", return_value="[]") as model:
                result = action()
                self.assert_common_system_rules(model.call_args.args[0])
                self.assertEqual(result["context_meta"]["provided_data_sources"], ["综资", "现场"])
                self.assertEqual(result["context_meta"]["missing_data_sources"], ["资产"])

    def test_external_agent_requests_do_not_pretend_to_include_asset_snapshot(self):
        with patch("backend.ai_agent._external_agent_url", return_value="http://agent.local/analyze"), \
             patch("backend.ai_agent._call_external_agent", return_value={"analysis_items": [], "model_name": "test"}) as call:
            result = ai_agent.analyze_inventory(self.tasks)
        self.assertEqual(call.call_args.args[0]["data_sources"], ["综资", "现场"])
        self.assertEqual(result["prompt_version"], BUSINESS_RULES_VERSION)

    def test_truncated_direct_analysis_identifies_its_actual_scope(self):
        payload = {"rows": [self.row] * (MAX_CONTEXT_ROWS + 1), "data_sources": ["综资", "现场"]}
        messages = ai_agent._analysis_prompt(payload)
        # 读取模型实际看到的元数据，不能只在服务响应里报告截断。
        user_message = messages[1]["content"]
        metadata_json = user_message.split("上下文管控信息：\n", 1)[1].split("\n\n", 1)[0]
        metadata = json.loads(metadata_json)
        self.assertTrue(metadata["truncated"])
        self.assertEqual(metadata["raw_row_count"], MAX_CONTEXT_ROWS + 1)
        self.assertEqual(metadata["sent_row_count"], MAX_CONTEXT_ROWS)
        self.assertEqual(metadata["missing_data_sources"], ["资产"])

    def test_empty_or_invalid_source_declarations_are_not_assumed_complete(self):
        for sources in (None, "资产综资现场", ["未知系统"]):
            with self.subTest(sources=sources):
                _, metadata = apply_context_policy({"rows": [], "data_sources": sources})
                self.assertEqual(metadata["provided_data_sources"], [])
                self.assertEqual(metadata["missing_data_sources"], ["资产", "综资", "现场"])

    def test_rendered_analysis_contract_matches_seven_field_parser(self):
        messages = render_inventory_analysis_prompt(self.context, [], [], {})
        self.assert_common_system_rules(messages)
        self.assertNotIn("{{payload_json}}", messages[1]["content"])
        content = json.dumps([{**{key: "test" for key in ANALYSIS_KEYS}, "设备健康程度": "AI猜测"}], ensure_ascii=False)
        self.assertEqual(set(ai_agent._parse_analysis_items(content)[0]), set(ANALYSIS_KEYS))

    def test_equal_and_different_models_do_not_produce_automatic_model_judgments(self):
        for actual in ("OSN-3500", "另一种复杂型号"):
            with self.subTest(actual=actual):
                rows = [{**self.row, "现场设备型号": actual}]
                self.assertEqual(run_rules(rows), [])

    def test_multibarcode_devices_bypass_all_automatic_findings(self):
        rows = [{**self.row, "设备名称": name, "清单资产条码": "001，002",
                 "现场资产条码": "001;003", "现场设备型号": ""} for name in ("网元甲", "网元乙")]
        findings = run_rules(rows)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(item["问题类型"] == "多条码设备待人工核对" for item in findings))
        self.assertTrue(all("全部由管理员填写" in item["整改建议"] for item in findings))

    def test_unfound_devices_are_not_reported_as_missing_labels_or_models(self):
        rows = [{**self.row, "设备名称": name, "现场状态": "盘亏", "现场资产条码": "",
                 "现场设备型号": "", "现场机柜条码": ""} for name in ("甲", "乙", "丙")]
        findings = run_rules(rows)
        self.assertEqual(len(findings), 1)
        self.assertIn("现场未找到", findings[0]["问题类型"])
        self.assertIn("不据此定性", findings[0]["判断依据"])

    def test_closure_tool_counts_pending_verification_separately(self):
        result = run_tool("closure.track_adjustment_loop", {"adjustments": [
            {"adjustment_id": 1, "status": "pending_review"},
            {"adjustment_id": 2, "status": "pending_verify"},
            {"adjustment_id": 3, "status": "done"},
        ]})
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["status_counts"]["pending_verify"], 1)
        self.assertEqual(result["status_counts"]["done"], 1)
        self.assertTrue(result["read_only"])

    def test_health_exposes_current_business_rule_version_without_llm_call(self):
        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")):
            self.assertEqual(health()["business_rules_version"], BUSINESS_RULES_VERSION)


if __name__ == "__main__":
    unittest.main()
