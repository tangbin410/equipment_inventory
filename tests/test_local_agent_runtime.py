import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from local_agent.agent_runtime import run_agent
from local_agent.app import analyze, chat


class LocalAgentRuntimeTests(unittest.TestCase):
    def test_model_can_call_read_only_tool_then_finish(self):
        responses = iter([
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "inventory.precheck",
                                "arguments": '{"rows": []}',
                            },
                        }],
                    },
                }],
            },
            {
                "choices": [{
                    "message": {"role": "assistant", "content": "预检完成"},
                }],
            },
        ])

        result = run_agent(
            [{"role": "user", "content": "检查盘点数据"}],
            lambda messages, tools: next(responses),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, "预检完成")
        self.assertEqual(result.steps, 2)
        self.assertTrue(any(message.get("role") == "tool" for message in result.messages))

    def test_agent_stops_for_approval_required_tool(self):
        responses = iter([
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "report.export_weekly_inventory",
                                "arguments": "{}",
                            },
                        }],
                    },
                }],
            },
        ])

        result = run_agent(
            [{"role": "user", "content": "导出本周报表"}],
            lambda messages, tools: next(responses),
        )

        self.assertEqual(result.status, "waiting_approval")
        self.assertEqual(result.pending_tool_calls[0]["name"], "report.export_weekly_inventory")

    def test_analysis_endpoint_uses_read_only_agent_loop(self):
        responses = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_3",
                            "type": "function",
                            "function": {
                                "name": "inventory.precheck",
                                "arguments": '{"rows": []}',
                            },
                        }],
                    },
                }],
            },
            {
                "choices": [{
                    "message": {"role": "assistant", "content": "[]"},
                }],
            },
        ]

        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
             patch("local_agent.app.post_deepseek_chat_response", side_effect=responses) as model_call:
            result = analyze({"prompt_version": "test", "input_summary": "", "rows": []})

        self.assertEqual(result["analysis_items"], [])
        self.assertEqual(model_call.call_count, 2)

    def test_preview_tool_does_not_create_file(self):
        from local_agent.tools.registry import run_tool

        result = run_tool("report.preview_inventory", {
            "rows": [{"机房名称": "ROOM_001", "设备名称": "DEVICE_001"}],
            "sample_limit": 1,
        })

        self.assertEqual(result["row_count"], 1)
        self.assertEqual(len(result["sample_rows"]), 1)
        self.assertFalse(result["file_created"])

    def test_model_tool_names_follow_provider_constraint(self):
        from local_agent.tools.registry import tool_definitions

        names = [item["function"]["name"] for item in tool_definitions()]
        self.assertTrue(names)
        self.assertTrue(all("." not in name for name in names))

    def test_chat_can_preview_export_scope(self):
        responses = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_4",
                            "type": "function",
                            "function": {
                                "name": "report.preview_inventory",
                                "arguments": '{"rows": [], "sample_limit": 3}',
                            },
                        }],
                    },
                }],
            },
            {
                "choices": [{
                    "message": {"role": "assistant", "content": "当前没有可导出的数据。"},
                }],
            },
        ]

        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
             patch("local_agent.app.post_deepseek_chat_response", side_effect=responses) as model_call:
            result = chat({"message": "预览 Excel 导出范围", "context": {"rows": []}, "history": []})

        self.assertEqual(result["answer"], "当前没有可导出的数据。")
        self.assertEqual(model_call.call_count, 2)

    def test_chat_returns_export_approval_request_without_creating_file(self):
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_export",
                        "type": "function",
                        "function": {
                            "name": "report__export_weekly_inventory",
                            "arguments": '{"task_ids": [1]}',
                        },
                    }],
                },
            }],
        }

        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
             patch("local_agent.app.post_deepseek_chat_response", return_value=response):
            result = chat({"message": "确认导出 Excel", "context": {"rows": []}, "history": []})

        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["pending_tool_calls"][0]["name"], "report.export_weekly_inventory")

    def test_chat_returns_draft_approval_request_without_dispatching(self):
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_draft",
                        "type": "function",
                        "function": {
                            "name": "adjustment__create_review_draft",
                            "arguments": '{"task_ids": [1]}',
                        },
                    }],
                },
            }],
        }

        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
             patch("local_agent.app.post_deepseek_chat_response", return_value=response):
            result = chat({"message": "生成整改草稿", "context": {"rows": []}, "history": []})

        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["pending_tool_calls"][0]["name"], "adjustment.create_review_draft")
        self.assertIn("整改草稿", result["answer"])

    def test_chat_returns_dispatch_approval_with_exact_ids(self):
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_dispatch",
                        "type": "function",
                        "function": {
                            "name": "adjustment__dispatch_confirmed_task",
                            "arguments": '{"adjustment_id": 12, "assigned_to": 3, "review_note": "审核通过"}',
                        },
                    }],
                },
            }],
        }

        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
             patch("local_agent.app.post_deepseek_chat_response", return_value=response):
            result = chat({"message": "将整改草稿12下发给人员3", "context": {"rows": []}, "history": []})

        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["pending_tool_calls"][0]["arguments"]["adjustment_id"], 12)
        self.assertEqual(result["pending_tool_calls"][0]["arguments"]["assigned_to"], 3)
        self.assertIn("现场人员 ID", result["answer"])

    def test_closure_tracking_is_read_only_and_detects_overdue(self):
        from local_agent.tools.registry import run_tool

        result = run_tool("closure.track_adjustment_loop", {
            "adjustments": [
                {
                    "adjustment_id": 12,
                    "source_task_ids": [1],
                    "status": "dispatched",
                    "risk_level": "高",
                    "priority": "P1",
                    "dispatched_at": (datetime.now() - timedelta(days=8)).isoformat(),
                },
                {"adjustment_id": 13, "status": "done"},
            ],
        })

        self.assertTrue(result["read_only"])
        self.assertEqual(result["status_counts"]["dispatched"], 1)
        self.assertEqual(result["status_counts"]["done"], 1)
        self.assertEqual(result["overdue_count"], 1)
        self.assertEqual(result["overdue_items"][0]["adjustment_id"], 12)

    def test_chat_can_call_read_only_closure_tracking(self):
        responses = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_closure",
                            "type": "function",
                            "function": {
                                "name": "closure__track_adjustment_loop",
                                "arguments": '{"adjustments": []}',
                            },
                        }],
                    },
                }],
            },
            {
                "choices": [{
                    "message": {"role": "assistant", "content": "当前没有整改闭环任务。"},
                }],
            },
        ]

        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
             patch("local_agent.app.post_deepseek_chat_response", side_effect=responses) as model_call:
            result = chat({"message": "查询整改闭环状态", "context": {"adjustments": []}, "history": []})

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "当前没有整改闭环任务。")
        self.assertEqual(model_call.call_count, 2)

    def test_chat_returns_reminder_approval_without_mutating_task(self):
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_reminder",
                        "type": "function",
                        "function": {
                            "name": "closure__create_reminder",
                            "arguments": '{"adjustment_id": 12, "reminder_note": "请跟进处理"}',
                        },
                    }],
                },
            }],
        }

        with patch("local_agent.app.deepseek_config", return_value=("test-key", "test-model", "http://llm.local")), \
             patch("local_agent.app.post_deepseek_chat_response", return_value=response):
            result = chat({"message": "催办整改任务12", "context": {"adjustments": []}, "history": []})

        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["pending_tool_calls"][0]["name"], "closure.create_reminder")
        self.assertEqual(result["pending_tool_calls"][0]["arguments"]["adjustment_id"], 12)
        self.assertIn("不发送微信、短信", result["answer"])


if __name__ == "__main__":
    unittest.main()
