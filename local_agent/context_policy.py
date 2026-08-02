"""Context controls for data sent from the local agent to the LLM."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


CONTEXT_POLICY_VERSION = "inventory-context-v1"
MAX_CONTEXT_ROWS = 800
MAX_TEXT_CHARS = 160
MAX_NOTES_CHARS = 220

ALLOWED_ROW_FIELDS = [
    "任务ID",
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
    "凭证照片状态",
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
    text = str(value or "").strip()
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


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply_context_policy(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
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
        "rows": safe_rows,
    }
    context_meta = {
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
        "max_text_chars": MAX_TEXT_CHARS,
        "max_notes_chars": MAX_NOTES_CHARS,
        "payload_hash": _hash_payload(safe_payload),
    }
    return safe_payload, context_meta
