"""AI 盘点分析 agent。

第一版内置在后端进程内；配置 INVENTORY_AI_AGENT_URL 后可平滑切到独立本地 agent 服务。
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests

PROMPT_VERSION = "inventory-ai-v1"


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

SENSITIVE_PATTERNS = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证]"),
    (re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "[IP]"),
]


def _format_bool(value: Any) -> str:
    return "是" if bool(value) else "否"


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
    parts = re.split(r"[,，、/;；\s]+", text)
    mapped = [mapper(part) for part in parts if part]
    return ",".join(mapped)


def build_inventory_rows(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把任务清单字段和现场盘点字段合并成一张原始数据表。"""
    rows: list[dict[str, Any]] = []
    for task in tasks:
        for record in task.get("records") or []:
            rows.append({
                "任务ID": task.get("id"),
                "任务名称": task.get("task_name") or "",
                "机房名称": task.get("room_name") or "",
                "片区": task.get("area_name") or "默认片区",
                "领取人": task.get("assigned_name") or "-",
                "任务状态": task.get("status") or "",
                "任务创建时间": task.get("created_at") or "",
                "领取时间": task.get("assigned_at") or task.get("claimed_at") or "",
                "现场完成时间": task.get("field_completed_at") or task.get("completed_at") or "",
                "设备名称": record.get("device_name") or "",
                "清单设备型号": record.get("expected_model") or "",
                "清单资产条码": record.get("expected_barcode") or "",
                "现场设备型号": record.get("actual_model") or "",
                "现场资产条码": record.get("asset_barcodes") or ("无条码" if record.get("asset_no_barcode") else ""),
                "资产无条码": _format_bool(record.get("asset_no_barcode")),
                "现场机柜条码": record.get("cabinet_barcodes") or ("无条码" if record.get("cabinet_no_barcode") else ""),
                "机柜无条码": _format_bool(record.get("cabinet_no_barcode")),
                "现场机架号": record.get("rack_no") or "",
                "现场状态": record.get("field_status") or "正常",
                "盘点时间": record.get("scanned_at") or "",
                "盘亏原因": record.get("missing_reason") or "",
                "现场备注": record.get("notes") or "",
                "凭证照片状态": "已上传" if record.get("evidence_photo") else "未上传",
            })
    return rows


def desensitize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """脱敏标识类字段，保留数据质量特征供模型分析。"""
    map_room = _mapper("ROOM")
    map_task = _mapper("TASK")
    map_area = _mapper("AREA")
    map_user = _mapper("USER")
    map_device = _mapper("DEVICE")
    map_asset = _mapper("ASSET")
    map_cabinet = _mapper("CABINET")

    sensitive_maps: dict[str, dict[str, str]] = defaultdict(dict)
    result = []
    for row in rows:
        item = dict(row)
        replacements = {
            "任务名称": map_task,
            "机房名称": map_room,
            "片区": map_area,
            "领取人": map_user,
            "设备名称": map_device,
        }
        for field, mapper in replacements.items():
            original = str(item.get(field) or "")
            masked = mapper(original)
            item[field] = masked
            if original and masked:
                sensitive_maps[field][masked] = original
        for field, mapper in {"清单资产条码": map_asset, "现场资产条码": map_asset, "现场机柜条码": map_cabinet}.items():
            original = str(item.get(field) or "")
            masked = _map_code_list(original, mapper)
            item[field] = masked
            if original and masked:
                for masked_code, original_code in zip(masked.split(","), re.split(r"[,，、/;；\s]+", original)):
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
    return [
        {
            "role": "system",
            "content": (
                "你是运营商传输网管资产盘点分析 agent。你只能基于用户给出的脱敏数据做只读分析，"
                "不能编造不存在的设备或机房，不能要求系统自动修改数据。输出必须是 JSON 数组。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请按以下固定规则分析资产盘点数据：未盘集中、现场型号缺失、现场资产条码缺失、"
                "机柜条码缺失、盘亏/盘盈记录集中、同一资产条码重复、同一机房同类问题集中、"
                "现场备注中提示的共性整改问题。输出数组元素字段固定为："
                "问题类型、涉及机房、涉及设备、风险等级、判断依据、整改建议、优先级。"
                "风险等级只能是高/中/低，优先级只能是P1/P2/P3。"
                f"\n\n数据：{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]


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


def _call_external_agent(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = requests.post(_external_agent_url(), json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    items = data.get("analysis_items") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def check_model_available() -> dict[str, Any]:
    """真实调用一次模型，确认当前配置是否可用。"""
    external_agent_url = _external_agent_url()
    try:
        if external_agent_url:
            response = requests.post(
                external_agent_url,
                json={"type": "health_check", "message": "ping"},
                timeout=min(REQUEST_TIMEOUT, 20),
            )
            response.raise_for_status()
            return {"ok": True, "message": "本地 agent 服务可用"}
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


def analyze_inventory(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    rows = build_inventory_rows(tasks)
    masked_rows, sensitive_maps = desensitize_rows(rows)
    payload = {
        "prompt_version": PROMPT_VERSION,
        "input_summary": input_summary(rows),
        "rows": masked_rows,
    }
    external_agent_url = _external_agent_url()
    items = _call_external_agent(payload) if external_agent_url else _call_deepseek(payload)
    restored_items = _restore_sensitive_values(items, sensitive_maps)
    return {
        "model_provider": "local-agent" if external_agent_url else "deepseek",
        "model_name": external_agent_url if external_agent_url else os.getenv("DEEPSEEK_MODEL", "").strip(),
        "prompt_version": PROMPT_VERSION,
        "input_summary": payload["input_summary"],
        "desensitized_payload": json.dumps(payload, ensure_ascii=False),
        "sensitive_maps": sensitive_maps,
        "analysis_items": restored_items,
    }
