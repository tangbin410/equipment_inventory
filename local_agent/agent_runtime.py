"""Small provider-neutral tool-calling loop for the local agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .tools import get_tool_spec, resolve_tool_name, run_tool, tool_definitions


ModelCall = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]


@dataclass
class AgentRunResult:
    status: str
    content: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0


def _message_from_response(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("模型响应缺少 choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("模型响应缺少 message")
    return message


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls")
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _parse_arguments(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    raw = function.get("arguments", {})
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError("工具参数格式无效")
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    return parsed


def run_agent(
    messages: list[dict[str, Any]],
    model_call: ModelCall,
    *,
    max_steps: int = 6,
    allow_approval_required: bool = False,
    include_approval_required: bool = True,
) -> AgentRunResult:
    """Run a bounded model -> tool -> observation loop.

    The model callback is injected so this runtime can be tested without a live LLM.
    """
    history = list(messages)
    for step in range(1, max_steps + 1):
        response = model_call(history, tool_definitions(include_approval_required=include_approval_required))
        message = _message_from_response(response)
        history.append(message)
        calls = _tool_calls(message)
        if not calls:
            return AgentRunResult(
                status="completed",
                content=str(message.get("content") or ""),
                messages=history,
                steps=step,
            )

        pending: list[dict[str, Any]] = []
        for call in calls:
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            wire_name = str(function.get("name") or "")
            name = resolve_tool_name(wire_name)
            arguments = _parse_arguments(call)
            spec = get_tool_spec(name)
            if spec.category.value == "approval_required" and not allow_approval_required:
                pending.append({"id": call.get("id", ""), "name": name, "arguments": arguments})
                continue
            result = run_tool(name, arguments, allow_approval_required=allow_approval_required)
            history.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "name": wire_name,
                "content": json.dumps(result, ensure_ascii=False),
            })
        if pending:
            return AgentRunResult(
                status="waiting_approval",
                messages=history,
                pending_tool_calls=pending,
                steps=step,
            )

    return AgentRunResult(status="failed", content="Agent 达到最大执行步数", messages=history, steps=max_steps)
