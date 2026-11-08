"""Context controls for data sent from the local agent to the LLM."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .prompt_loader import BUSINESS_RULES_VERSION


CONTEXT_POLICY_VERSION = "inventory-context-v6"
MAX_CONTEXT_ROWS = 800
MAX_ADJUSTMENT_ROWS = 300
MAX_TEXT_CHARS = 160
MAX_NOTES_CHARS = 220

ALLOWED_ROW_FIELDS = [
    "资产资源名称", "综资网元名称", "现场网元名称", "资产信息条码",
    "资产地点编码", "综资地点编码", "现场地点编码", "资产规格型号",
    "条码关联状态", "现场反馈数", "综资现场网元关联与条码核对",
    "比对模式", "名称比对结论", "地点比对结论", "型号人工判断",
    "综资设备机架号", "现场设备机架号", "综资现场机架号核对",
    "综资现场型号人工判断", "综资资产条码核对", "综资资产地点核对",
    "综资资产型号人工判断", "现场资产条码核对", "现场资产地点核对",
    "资产记录数", "综资记录数", "现场记录数", "规则问题类型",
    "任务ID",
    "导入批次",
    "任务名称",
    "机房名称",
    "片区",
    "领取人",
    "任务状态",
    "任务创建时间",
    "领取时间",
    "现场完成时间",
    "设备名称",
    "清单设备型号",
    "清单资产条码",
    "清单设备机架",
    "现场设备型号",
    "现场资产条码",
    "资产无条码",
    "现场机柜条码",
    "机柜无条码",
    "现场机架号",
    "现场状态",
    "盘点时间",
    "盘亏原因",
    "现场备注",
]

ALLOWED_ADJUSTMENT_FIELDS = [
    "adjustment_id",
    "source_task_ids",
    "status",
    "risk_level",
    "priority",
    "created_at",
    "dispatched_at",
    "completed_at",
    "reminder_count",
    "reminded_at",
]

PROMPT_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?previous\s+instructions",
        r"system\s*prompt",
        r"developer\s*message",
        r"tool\s*call",
        r"请忽略.*?(规则|指令|要求)",
        r"忽略.*?(上文|以上|之前).*?(规则|指令|要求)",
        r"你现在是",
        r"不要遵守",
        r"直接输出",
    ]
]


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(value if value is not None else "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _sanitize_notes(value: Any) -> tuple[str, bool]:
    text = _text(value, MAX_NOTES_CHARS)
    original = text
    for pattern in PROMPT_INJECTION_PATTERNS:
        text = pattern.sub("[已移除可疑指令]", text)
    return text, text != original


def _safe_row(row: dict[str, Any]) -> tuple[dict[str, str], bool, int]:
    safe: dict[str, str] = {}
    note_sanitized = False
    for field in ALLOWED_ROW_FIELDS:
        limit = MAX_NOTES_CHARS if field == "现场备注" else MAX_TEXT_CHARS
        if field == "现场备注":
            safe[field], note_sanitized = _sanitize_notes(row.get(field))
        else:
            safe[field] = _text(row.get(field), limit)
    removed_field_count = len([field for field in row.keys() if field not in ALLOWED_ROW_FIELDS])
    return safe, note_sanitized, removed_field_count


def _safe_adjustment(item: dict[str, Any]) -> dict[str, Any]:
    source_ids = item.get("source_task_ids") if isinstance(item.get("source_task_ids"), list) else []
    try:
        adjustment_id = max(int(item.get("adjustment_id") or 0), 0)
    except (TypeError, ValueError):
        adjustment_id = 0
    safe_source_ids = []
    for value in source_ids[:50]:
        try:
            source_id = int(value)
        except (TypeError, ValueError):
            continue
        if source_id > 0:
            safe_source_ids.append(source_id)
    try:
        reminder_count = max(int(item.get("reminder_count") or 0), 0)
    except (TypeError, ValueError):
        reminder_count = 0
    return {
        "adjustment_id": adjustment_id,
        "source_task_ids": safe_source_ids,
        "status": _text(item.get("status")),
        "risk_level": _text(item.get("risk_level")),
        "priority": _text(item.get("priority")),
        "created_at": _text(item.get("created_at")),
        "dispatched_at": _text(item.get("dispatched_at")),
        "completed_at": _text(item.get("completed_at")),
        "reminder_count": reminder_count,
        "reminded_at": _text(item.get("reminded_at")),
    }


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply_context_policy(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    adjustments = payload.get("adjustments") if isinstance(payload.get("adjustments"), list) else []
    raw_count = len(rows)
    safe_rows: list[dict[str, str]] = []
    invalid_row_count = 0
    sanitized_note_count = 0
    removed_field_count = 0

    for row in rows[:MAX_CONTEXT_ROWS]:
        if not isinstance(row, dict):
            invalid_row_count += 1
            continue
        safe_row, note_sanitized, removed_fields = _safe_row(row)
        safe_rows.append(safe_row)
        sanitized_note_count += 1 if note_sanitized else 0
        removed_field_count += removed_fields

    safe_payload = {
        "prompt_version": _text(payload.get("prompt_version")),
        "input_summary": _text(payload.get("input_summary"), 500),
        "data_sources": [
            source for source in ("资产", "综资", "现场")
            if isinstance(payload.get("data_sources"), list) and source in payload["data_sources"]
        ],
        "rows": safe_rows,
        "adjustments": [
            _safe_adjustment(item)
            for item in adjustments[:MAX_ADJUSTMENT_ROWS]
            if isinstance(item, dict)
        ],
    }
    context_meta = {
        "business_rules_version": BUSINESS_RULES_VERSION,
        "provided_data_sources": safe_payload["data_sources"],
        "missing_data_sources": [source for source in ("资产", "综资", "现场") if source not in safe_payload["data_sources"]],
        "context_policy_version": CONTEXT_POLICY_VERSION,
        "allowed_fields": ALLOWED_ROW_FIELDS,
        "raw_row_count": raw_count,
        "sent_row_count": len(safe_rows),
        "truncated": raw_count > len(safe_rows),
        "truncated_row_count": max(raw_count - MAX_CONTEXT_ROWS, 0),
        "invalid_row_count": invalid_row_count,
        "dropped_row_count": raw_count - len(safe_rows),
        "removed_field_count": removed_field_count,
        "sanitized_note_count": sanitized_note_count,
        "max_context_rows": MAX_CONTEXT_ROWS,
        "adjustment_row_count": len(adjustments),
        "sent_adjustment_row_count": len(safe_payload["adjustments"]),
        "max_adjustment_rows": MAX_ADJUSTMENT_ROWS,
        "max_text_chars": MAX_TEXT_CHARS,
        "max_notes_chars": MAX_NOTES_CHARS,
        "payload_hash": _hash_payload(safe_payload),
    }
    return safe_payload, context_meta
