"""AI 盘点分析 agent。

第一版内置在后端进程内；配置 INVENTORY_AI_AGENT_URL 后可平滑切到独立本地 agent 服务。
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from local_agent.context_policy import apply_context_policy
from local_agent.prompt_loader import BUSINESS_RULES_VERSION, load_inventory_business_rules, render_inventory_analysis_prompt

from .database import split_codes

PROMPT_VERSION = BUSINESS_RULES_VERSION


def _load_local_env() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


_load_local_env()
REQUEST_TIMEOUT = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))
CHAT_HISTORY_LIMIT = 8
CHAT_MESSAGE_LIMIT = 1200

SENSITIVE_PATTERNS = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证]"),
    (re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "[IP]"),
]


def _format_bool(value: Any) -> str:
    return "是" if bool(value) else "否"


def device_extra_value(record: dict[str, Any], key: str) -> str:
    extra = record.get("device_extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            extra = {}
    if not isinstance(extra, dict):
        return ""
    return str(extra.get(key) or "").strip()


def _mask_free_text(value: str) -> str:
    text = str(value or "")
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _mapper(prefix: str):
    mapping: dict[str, str] = {}

    def map_value(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text not in mapping:
            mapping[text] = f"{prefix}_{len(mapping) + 1:03d}"
        return mapping[text]

    return map_value


def _map_code_list(value: str, mapper) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text == "无条码":
        return text
    mapped = [mapper(part) for part in sorted(split_codes(text))]
    return ",".join(mapped)


def build_inventory_rows(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把任务清单字段和现场盘点字段合并成一张原始数据表。"""
    rows: list[dict[str, Any]] = []
    for task in tasks:
        for record in task.get("records") or []:
            rows.append({
                "任务ID": task.get("id"),
                "导入批次": task.get("import_batch_id") or "",
                "任务名称": task.get("task_name") or "",
                "机房名称": task.get("room_name") or "",
                "片区": task.get("area_name") or "汇聚及核心",
                "领取人": task.get("assigned_name") or "-",
                "任务状态": task.get("status") or "",
                "任务创建时间": task.get("created_at") or "",
                "领取时间": task.get("assigned_at") or task.get("claimed_at") or "",
                "现场完成时间": task.get("field_completed_at") or task.get("completed_at") or "",
                "设备名称": record.get("device_name") or "",
                "清单设备型号": record.get("expected_model") or "",
                "清单资产条码": record.get("expected_barcode") or "",
                "清单设备机架": record["expected_rack_no"] if "expected_rack_no" in record else device_extra_value(record, "设备机架"),
                "现场设备型号": record.get("actual_model") or "",
                "现场资产条码": record.get("asset_barcodes") or ("无条码" if record.get("asset_no_barcode") else ""),
                "资产无条码": _format_bool(record.get("asset_no_barcode")),
                "现场机柜条码": record.get("cabinet_barcodes") or ("无条码" if record.get("cabinet_no_barcode") else ""),
                "机柜无条码": _format_bool(record.get("cabinet_no_barcode")),
                "现场机架号": record.get("rack_no") or "",
                "现场状态": "未盘点" if record.get("match_status") == "未扫" else (record.get("field_status") or "正常"),
                "盘点时间": record.get("scanned_at") or "",
                "盘亏原因": record.get("missing_reason") or "",
                "现场备注": record.get("notes") or "",
            })
    return rows


def desensitize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """脱敏标识类字段，保留数据质量特征供模型分析。"""
    map_room = _mapper("ROOM")
    map_task = _mapper("TASK")
    map_batch = _mapper("BATCH")
    map_area = _mapper("AREA")
    map_user = _mapper("USER")
    map_device = _mapper("DEVICE")
    map_asset = _mapper("ASSET")
    map_cabinet = _mapper("CABINET")
    map_location = _mapper("LOCATION")

    sensitive_maps: dict[str, dict[str, str]] = defaultdict(dict)
    result = []
    for row in rows:
        item = dict(row)
        replacements = {
            "导入批次": map_batch,
            "任务名称": map_task,
            "机房名称": map_room,
            "片区": map_area,
            "领取人": map_user,
            "设备名称": map_device,
            "资产资源名称": map_device, "综资网元名称": map_device, "现场网元名称": map_device,
            "资产地点编码": map_location, "综资地点编码": map_location, "现场地点编码": map_location,
        }
        for field, mapper in replacements.items():
            original = str(item.get(field) or "")
            masked = mapper(original)
            item[field] = masked
            if original and masked:
                sensitive_maps[field][masked] = original
        for field, mapper in {"资产信息条码": map_asset, "清单资产条码": map_asset, "现场资产条码": map_asset, "现场机柜条码": map_cabinet}.items():
            original = str(item.get(field) or "")
            masked = _map_code_list(original, mapper)
            item[field] = masked
            if original and masked:
                for masked_code, original_code in zip(masked.split(","), sorted(split_codes(original))):
                    if masked_code and original_code:
                        sensitive_maps[field][masked_code] = original_code
        item["现场备注"] = _mask_free_text(item.get("现场备注") or "")
        result.append(item)
    return result, dict(sensitive_maps)


def input_summary(rows: list[dict[str, Any]]) -> str:
    rooms = {row.get("机房名称") for row in rows if row.get("机房名称")}
    areas = {row.get("片区") for row in rows if row.get("片区")}
    scanned = sum(1 for row in rows if row.get("盘点时间"))
    pending = len(rows) - scanned
    return f"任务记录{len(rows)}条，涉及机房{len(rooms)}个，片区{len(areas)}个，已盘{scanned}条，未盘{pending}条"


def _analysis_prompt(payload: dict[str, Any]) -> list[dict[str, str]]:
    safe_payload, context_meta = apply_context_policy(payload)
    return render_inventory_analysis_prompt(safe_payload, [], [], context_meta)


def _deepseek_config() -> tuple[str, str, str]:
    api_key = _clean_env_value(os.getenv("DEEPSEEK_API_KEY", ""))
    model = _clean_env_value(os.getenv("DEEPSEEK_MODEL", ""))
    base_url = _clean_env_value(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    if not model:
        raise RuntimeError("未配置 DEEPSEEK_MODEL")
    if _has_control_chars(api_key) or len(api_key) < 10:
        raise RuntimeError("DEEPSEEK_API_KEY contains invalid or invisible characters. Please rerun configure_ai_env.bat and paste the key again.")
    if _has_control_chars(model):
        raise RuntimeError("DEEPSEEK_MODEL contains invalid or invisible characters. Please rerun configure_ai_env.bat.")
    if _has_control_chars(base_url) or not base_url.lower().startswith(("http://", "https://")):
        raise RuntimeError("DEEPSEEK_BASE_URL is invalid. Please use a URL like https://api.deepseek.com.")
    return api_key, model, base_url


def _external_agent_url() -> str:
    return _clean_env_value(os.getenv("INVENTORY_AI_AGENT_URL", ""))


def _external_agent_chat_url() -> str:
    url = _external_agent_url()
    if not url:
        return ""
    return re.sub(r"/analyze/?$", "/chat", url.rstrip("/")) if url.rstrip("/").endswith("/analyze") else f"{url.rstrip('/')}/chat"


def _agent_headers() -> dict[str, str]:
    token = _clean_env_value(os.getenv("INVENTORY_AGENT_TOKEN", ""))
    return {"Authorization": f"Bearer {token}"} if token else {}


def _clean_env_value(value: str) -> str:
    return str(value or "").strip().strip("\"'")


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in str(value or ""))


def _http_error_detail(response: requests.Response | None, fallback: str) -> str:
    if response is None:
        return fallback
    status = f"HTTP {response.status_code}"
    if response.reason:
        status = f"{status} {response.reason}"
    text = response.text.strip()
    if not text:
        return f"{status}: empty response body"
    try:
        data = response.json()
    except ValueError:
        return f"{status}: {text[:300]}"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or json.dumps(error, ensure_ascii=False)
            return f"{status}: {message}"
        detail = data.get("detail") or data.get("message")
        if detail:
            return f"{status}: {detail}"
    return f"{status}: {text[:300]}"


def _post_deepseek_chat(messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    api_key, model, base_url = _deepseek_config()
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _parse_analysis_items(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        data = json.loads(match.group(0)) if match else []
    if isinstance(data, dict):
        for key in ("问题清单", "analysis_items", "items", "result"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []
    allowed = ["问题类型", "涉及机房", "涉及设备", "风险等级", "判断依据", "整改建议", "优先级"]
    return [{key: str(item.get(key) or "") for key in allowed} for item in data if isinstance(item, dict)]


def _call_deepseek(payload: dict[str, Any]) -> list[dict[str, Any]]:
    content = _post_deepseek_chat(_analysis_prompt(payload), temperature=0.2)
    return _parse_analysis_items(content)


def _call_external_agent(payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(_external_agent_url(), json=payload, headers=_agent_headers(), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    items = data.get("analysis_items") if isinstance(data, dict) else data
    return {
        "analysis_items": items if isinstance(items, list) else [],
        "model_name": _clean_env_value(data.get("model_name", "")) if isinstance(data, dict) else "",
        "context_meta": data.get("context_meta", {}) if isinstance(data, dict) else {},
        "tool_manifest": data.get("tool_manifest", []) if isinstance(data, dict) else [],
    }


def _clean_chat_text(value: str, limit: int = CHAT_MESSAGE_LIMIT) -> str:
    text = _mask_free_text(str(value or "").strip())
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _normalize_chat_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for item in history[-CHAT_HISTORY_LIMIT:]:
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = _clean_chat_text(str(item.get("content") or ""))
        if content:
            result.append({"role": role, "content": content})
    return result


def _chat_prompt(message: str, history: list[dict[str, str]], payload: dict[str, Any]) -> list[dict[str, str]]:
    model_name = _clean_env_value(os.getenv("DEEPSEEK_MODEL", "")) or "未配置模型名称"
    now_text = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    system = (
        "你是运营商传输网管资产盘点本地 agent 的对话助手。"
        f"当前本地 agent 直连 DeepSeek，配置的 LLM 模型名称是：{model_name}。"
        f"当前本地系统时间是：{now_text}。"
        "你可以支持日常聊天、回答自身模型、agent功能、操作流程、盘点规则解释，以及当前管理员选定范围内的脱敏盘点上下文问题。"
        f"当管理员询问你是什么模型或LLM时，必须用第一人称回答：我是模型 {model_name}，由本地 agent 受控调用。"
        "当管理员询问今天日期、现在时间、几月几号时，直接依据当前本地系统时间回答。"
        "不要编造机房、设备、条码、人员或任务；不能直接读取本地文件、查询数据库或修改业务数据。"
        "如果判断依据不足，要向管理员提出需要确认的问题。"
        "涉及整改、下发、闭环时，只能给出建议或待确认事项，不能声称已经执行。"
        f"\n\n{load_inventory_business_rules()}"
    )
    context, context_meta = apply_context_policy(payload)
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({
        "role": "user",
        "content": (
            f"管理员问题：{message}\n\n"
            f"上下文管控信息：{json.dumps(context_meta, ensure_ascii=False)}\n"
            f"脱敏盘点上下文：{json.dumps(context, ensure_ascii=False)}\n\n"
            "请用简洁中文回答。若需要管理员确认，请明确列出确认项。"
        ),
    })
    return messages


def _restore_sensitive_text(text: str, sensitive_maps: dict[str, dict[str, str]]) -> str:
    restored = str(text or "")
    reverse_map = {
        masked: original
        for field_map in sensitive_maps.values()
        for masked, original in field_map.items()
    }
    for masked, original in sorted(reverse_map.items(), key=lambda item: len(item[0]), reverse=True):
        restored = restored.replace(masked, original)
    return restored


def _call_external_agent_chat(message: str, history: list[dict[str, str]], payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        _external_agent_chat_url(),
        json={"message": message, "history": history, "context": payload},
        headers=_agent_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "answer": str(data.get("answer") or "") if isinstance(data, dict) else "",
        "status": str(data.get("status") or "completed") if isinstance(data, dict) else "completed",
        "pending_tool_calls": data.get("pending_tool_calls", []) if isinstance(data, dict) else [],
        "model_name": _clean_env_value(data.get("model_name", "")) if isinstance(data, dict) else "",
        "context_meta": data.get("context_meta", {}) if isinstance(data, dict) else {},
        "tool_manifest": data.get("tool_manifest", []) if isinstance(data, dict) else [],
    }


def check_model_available() -> dict[str, Any]:
    """真实调用一次模型，确认当前配置是否可用。"""
    external_agent_url = _external_agent_url()
    try:
        if external_agent_url:
            response = requests.post(
                external_agent_url,
                json={"type": "health_check", "message": "ping"},
                headers=_agent_headers(),
                timeout=min(REQUEST_TIMEOUT, 20),
            )
            response.raise_for_status()
            data = response.json()
            model_name = data.get("model_name", "") if isinstance(data, dict) else ""
            return {"ok": True, "message": "本地 agent 服务可用", "model_name": _clean_env_value(model_name)}
        content = _post_deepseek_chat(
            [
                {"role": "system", "content": "你是模型连通性检测助手。"},
                {"role": "user", "content": "请只回复 OK"},
            ],
            temperature=0,
        )
        return {"ok": True, "message": f"DeepSeek 模型可用，返回：{content.strip()[:40]}"}
    except requests.HTTPError as exc:
        detail = _http_error_detail(exc.response, str(exc))
        return {"ok": False, "message": f"模型接口返回错误：{detail}"}
    except requests.RequestException as exc:
        return {"ok": False, "message": f"网络或接口连接失败：{exc}"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def _restore_sensitive_values(items: list[dict[str, Any]], sensitive_maps: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    reverse_map = {
        masked: original
        for field_map in sensitive_maps.values()
        for masked, original in field_map.items()
    }
    restored = []
    for item in items:
        row = {}
        for key, value in item.items():
            text = str(value or "")
            for masked, original in reverse_map.items():
                text = text.replace(masked, original)
            row[key] = text
        restored.append(row)
    return restored


def analyze_inventory(tasks: list[dict[str, Any]], comparison: dict[str, Any] | None = None) -> dict[str, Any]:
    from .three_way import agent_rows
    rows = agent_rows(comparison) if comparison is not None else build_inventory_rows(tasks)
    masked_rows, sensitive_maps = desensitize_rows(rows)
    payload = {
        "prompt_version": PROMPT_VERSION,
        "input_summary": input_summary(rows),
        "rows": masked_rows,
        "data_sources": ["资产", "综资", "现场"] if comparison and comparison.get("asset_version") else ["综资", "现场"],
    }
    external_agent_url = _external_agent_url()
    context_meta: dict[str, Any] = {}
    tool_manifest: list[dict[str, Any]] = []
    if external_agent_url:
        agent_result = _call_external_agent(payload)
        items = agent_result["analysis_items"]
        model_name = agent_result["model_name"] or _clean_env_value(os.getenv("DEEPSEEK_MODEL", ""))
        context_meta = agent_result.get("context_meta") or {}
        tool_manifest = agent_result.get("tool_manifest") or []
    else:
        _, context_meta = apply_context_policy(payload)
        items = _call_deepseek(payload)
        model_name = _clean_env_value(os.getenv("DEEPSEEK_MODEL", ""))
    restored_items = _restore_sensitive_values(items, sensitive_maps)
    return {
        "model_provider": "local-agent" if external_agent_url else "deepseek",
        "model_name": model_name,
        "prompt_version": PROMPT_VERSION,
        "input_summary": payload["input_summary"],
        "desensitized_payload": json.dumps(payload, ensure_ascii=False),
        "sensitive_maps": sensitive_maps,
        "context_meta": context_meta,
        "tool_manifest": tool_manifest,
        "analysis_items": restored_items,
    }


def chat_inventory(
    message: str,
    tasks: list[dict[str, Any]],
    history: list[dict[str, Any]] | None = None,
    adjustments: list[dict[str, Any]] | None = None,
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """面向管理员的只读对话：后端取数并脱敏，模型只能看脱敏上下文。"""
    from .three_way import agent_rows
    rows = agent_rows(comparison) if comparison is not None else build_inventory_rows(tasks)
    masked_rows, sensitive_maps = desensitize_rows(rows)
    safe_message = _clean_chat_text(message)
    if not safe_message:
        raise ValueError("请输入需要咨询 AI agent 的问题")
    normalized_history = _normalize_chat_history(history or [])
    payload = {
        "prompt_version": PROMPT_VERSION,
        "input_summary": input_summary(rows) if rows else "当前未选择任务，未附带资产盘点上下文",
        "rows": masked_rows,
        "adjustments": adjustments or [],
        "data_sources": ["资产", "综资", "现场"] if comparison and comparison.get("asset_version") else ["综资", "现场"],
    }
    external_agent_url = _external_agent_url()
    context_meta: dict[str, Any] = {}
    tool_manifest: list[dict[str, Any]] = []
    agent_status = "completed"
    pending_tool_calls: list[dict[str, Any]] = []
    if external_agent_url:
        agent_result = _call_external_agent_chat(safe_message, normalized_history, payload)
        answer = agent_result["answer"]
        agent_status = agent_result.get("status") or "completed"
        pending_tool_calls = agent_result.get("pending_tool_calls") or []
        model_name = agent_result["model_name"] or _clean_env_value(os.getenv("DEEPSEEK_MODEL", ""))
        context_meta = agent_result.get("context_meta") or {}
        tool_manifest = agent_result.get("tool_manifest") or []
    else:
        _, context_meta = apply_context_policy(payload)
        answer = _post_deepseek_chat(_chat_prompt(safe_message, normalized_history, payload), temperature=0.2)
        model_name = _clean_env_value(os.getenv("DEEPSEEK_MODEL", ""))
    return {
        "answer": _restore_sensitive_text(answer, sensitive_maps),
        "status": agent_status,
        "pending_tool_calls": pending_tool_calls,
        "model_provider": "local-agent" if external_agent_url else "deepseek",
        "model_name": model_name,
        "input_summary": payload["input_summary"],
        "context_meta": context_meta,
        "tool_manifest": tool_manifest,
    }
