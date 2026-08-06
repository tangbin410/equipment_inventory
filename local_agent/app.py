"""Standalone local agent for inventory AI analysis."""

from __future__ import annotations

import json
import hmac
import re
from datetime import datetime
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request

from .config import agent_token, deepseek_config, load_local_env
from .context_policy import CONTEXT_POLICY_VERSION, apply_context_policy
from .agent_runtime import run_agent
from .llm_client import http_error_detail, post_deepseek_chat, post_deepseek_chat_response
from .prompt_loader import render_inventory_analysis_prompt
from .rules import ANALYSIS_KEYS
from .tools import run_tool, tool_manifest


load_local_env()
app = FastAPI(title="Equipment Inventory Local Agent")


def _require_agent_token(request: Request) -> None:
    expected = agent_token()
    auth_value = request.headers.get("Authorization", "")
    provided = auth_value.removeprefix("Bearer ").strip() if auth_value.startswith("Bearer ") else ""
    provided = provided or request.headers.get("X-Agent-Token", "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="local agent token invalid")


def _parse_json_array(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        data = json.loads(text[start:end + 1]) if start >= 0 and end > start else []
    if isinstance(data, dict):
        for key in ("analysis_items", "items", "result", "问题清单"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []
    return [
        {key: str(item.get(key) or "") for key in ANALYSIS_KEYS}
        for item in data
        if isinstance(item, dict)
    ]


def _merge_items(rule_items: list[dict[str, str]], model_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in [*rule_items, *model_items]:
        normalized = {key: str(item.get(key) or "") for key in ANALYSIS_KEYS}
        if not normalized["问题类型"]:
            continue
        key = (normalized["问题类型"], normalized["涉及机房"], normalized["涉及设备"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged[:30]


def _clean_chat_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _chat_history(value: Any) -> list[dict[str, str]]:
    history = value if isinstance(value, list) else []
    result = []
    for item in history[-8:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = _clean_chat_text(item.get("content"))
        if content:
            result.append({"role": role, "content": content})
    return result


def _approval_message(pending_tool_calls: list[dict[str, Any]]) -> str:
    names = {str(call.get("name") or "") for call in pending_tool_calls}
    if "report.export_weekly_inventory" in names:
        return "检测到需要管理员确认的导出操作。请核对当前任务范围后点击“确认导出 Excel”，确认前不会创建文件。"
    if "adjustment.create_review_draft" in names:
        return "检测到需要管理员确认的整改草稿生成操作。确认后只生成待审核草稿，不会下发到小程序。"
    if "adjustment.dispatch_confirmed_task" in names:
        return "检测到需要管理员确认的整改任务下发操作。请核对草稿 ID 和现场人员 ID，确认后才会下发到小程序。"
    if "closure.create_reminder" in names:
        return "检测到需要管理员确认的系统内催办。请核对整改 ID，确认后只记录催办，不发送微信、短信，也不改变任务状态。"
    return "检测到需要管理员确认的操作。确认前不会修改业务数据。"


def chat(payload: dict[str, Any]) -> dict[str, Any]:
    message = _clean_chat_text(payload.get("message"))
    if not message:
        raise ValueError("message is required")
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    safe_payload, context_meta = apply_context_policy(context)
    rows = safe_payload.get("rows") if isinstance(safe_payload.get("rows"), list) else []
    precheck = run_tool("inventory.precheck", {"rows": rows})
    _, model_name, _ = deepseek_config()
    now_text = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    messages = [
        {
            "role": "system",
            "content": (
                "你是运营商传输网管资产盘点本地 agent 的对话助手。"
                f"当前本地 agent 调用 DeepSeek，配置的 LLM 模型名称是：{model_name}。"
                f"当前本地系统时间是：{now_text}。"
                "你可以回答自身模型、agent功能、操作流程、盘点规则解释，以及脱敏盘点上下文、预检结果和对话历史中的问题。"
                f"当管理员询问你是什么模型或LLM时，必须用第一人称回答：我是模型 {model_name}，由本地 agent 受控调用。"
                "当管理员询问今天日期、现在时间、几月几号时，直接依据当前本地系统时间回答。"
                "不得直接读取本地文件、查询数据库或修改业务数据；不得声称已经下发、整改或闭环。"
                "如果判断依据不足，要向管理员提出明确确认项。"
            ),
        },
        {
            "role": "system",
            "content": "When the administrator asks to export Excel, call the export tool to create a pending approval request; never create the file before confirmation. When the administrator asks to generate adjustment review drafts, call the draft tool to create a pending approval request; never dispatch a task. Only call the adjustment dispatch tool when the administrator provides an exact adjustment ID and worker ID; never infer either ID. When asked about adjustment closure status or overdue items, call the read-only closure tracking tool. Only call the reminder tool when the administrator provides an exact adjustment ID; never infer the ID, never claim that WeChat or SMS was sent, and never change task status.",
        },
        *_chat_history(payload.get("history")),
        {
            "role": "user",
            "content": (
                f"管理员问题：{message}\n\n"
                f"上下文管控信息：{json.dumps(context_meta, ensure_ascii=False)}\n"
                f"本地预检结果：{json.dumps(precheck, ensure_ascii=False)}\n"
                f"脱敏盘点上下文：{json.dumps(safe_payload, ensure_ascii=False)}\n\n"
                "请用简洁中文回答；如需要管理员判断，请列出确认项。"
            ),
        },
    ]
    def model_call(history: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return post_deepseek_chat_response(history, temperature=0.2, tools=tools)

    run_result = run_agent(
        messages,
        model_call,
        max_steps=6,
        include_approval_required=True,
    )
    answer = run_result.content
    if run_result.status == "waiting_approval":
        answer = _approval_message(run_result.pending_tool_calls)
    elif run_result.status != "completed":
        raise RuntimeError(f"Agent 未完成对话：{run_result.status}")
    return {
        "answer": answer.strip(),
        "status": run_result.status,
        "pending_tool_calls": run_result.pending_tool_calls,
        "context_meta": context_meta,
        "tool_manifest": tool_manifest(include_approval_required=True),
        "agent": "local-inventory-agent",
        "model_name": model_name,
    }


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload, context_meta = apply_context_policy(payload)
    rows = safe_payload.get("rows") if isinstance(safe_payload.get("rows"), list) else []
    rule_items = run_tool("inventory.run_rules", {"rows": rows})
    model_name = ""
    model_error = ""
    try:
        _, model_name, _ = deepseek_config()
        messages = render_inventory_analysis_prompt(
            safe_payload,
            rule_items,
            tool_manifest(include_approval_required=False),
            context_meta,
        )

        def model_call(history: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
            return post_deepseek_chat_response(history, temperature=0.2, tools=tools)

        run_result = run_agent(
            messages,
            model_call,
            max_steps=6,
            include_approval_required=False,
        )
        if run_result.status != "completed":
            raise RuntimeError(f"Agent 未完成分析：{run_result.status}")
        model_items = _parse_json_array(run_result.content)
    except Exception as exc:
        model_error = str(exc)
        model_items = []
    if model_error:
        context_meta["model_error"] = model_error
    return {
        "analysis_items": _merge_items(rule_items, model_items),
        "rule_items": rule_items,
        "context_meta": context_meta,
        "tool_manifest": tool_manifest(include_approval_required=False),
        "agent": "local-inventory-agent",
        "model_name": model_name,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    _, model, _ = deepseek_config()
    return {
        "ok": True,
        "agent": "local-inventory-agent",
        "model_provider": "deepseek",
        "model_name": model,
        "context_policy_version": CONTEXT_POLICY_VERSION,
        "tool_count": len(tool_manifest()),
    }


@app.get("/tools")
def tools(request: Request) -> dict[str, Any]:
    _require_agent_token(request)
    return {
        "agent": "local-inventory-agent",
        "context_policy_version": CONTEXT_POLICY_VERSION,
        "tools": tool_manifest(),
    }


@app.post("/analyze")
async def agent_entry(request: Request) -> dict[str, Any]:
    _require_agent_token(request)
    payload = await request.json()
    request_type = payload.get("type") if isinstance(payload, dict) else None
    try:
        if request_type == "health_check":
            _, model, _ = deepseek_config()
            content = post_deepseek_chat(
                [
                    {"role": "system", "content": "你是连通性检测助手。"},
                    {"role": "user", "content": "请只回复 OK"},
                ],
                temperature=0,
            )
            return {"ok": True, "message": f"local agent ok, model replied: {content.strip()[:40]}", "model_name": model}
        return analyze(payload)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=502, detail=http_error_detail(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/chat")
async def chat_entry(request: Request) -> dict[str, Any]:
    _require_agent_token(request)
    payload = await request.json()
    try:
        return chat(payload if isinstance(payload, dict) else {})
    except requests.HTTPError as exc:
        raise HTTPException(status_code=502, detail=http_error_detail(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
