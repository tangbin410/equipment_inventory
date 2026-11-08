"""Whitelisted tools for the local inventory agent.

The model is not allowed to call arbitrary Python functions. The agent may only
invoke tools registered here, and write-like tools require an explicit approval
path before they can run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from ..rules import run_rules


class ToolCategory(str, Enum):
    READ_ONLY = "read_only"
    APPROVAL_REQUIRED = "approval_required"


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: ToolCategory
    description: str
    handler: ToolHandler
    enabled: bool = True
    capability: str = ""
    input_schema: dict[str, Any] | None = None


def _inventory_run_rules(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return run_rules(rows)


def _inventory_precheck(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    room_count = len({str(row.get("机房名称") or "").strip() for row in rows if isinstance(row, dict) and row.get("机房名称")})
    scanned_count = sum(1 for row in rows if isinstance(row, dict) and (int(row.get("现场反馈数", row.get("现场记录数")) or 0) > 0 if row.get("比对模式") else bool(str(row.get("盘点时间") or "").strip())))
    return {
        "row_count": len(rows),
        "room_count": room_count,
        "scanned_count": scanned_count,
        "unscanned_count": max(len(rows) - scanned_count, 0),
        "rule_items": run_rules(rows),
    }


EXPORT_COLUMNS = [
    "任务ID", "任务名称", "机房名称", "片区", "领取人", "任务状态", "任务创建时间",
    "领取时间", "现场完成时间", "设备名称", "清单设备型号", "清单资产条码",
    "现场设备型号", "现场资产条码", "资产无条码", "现场机柜条码", "机柜无条码",
    "现场机架号", "现场状态", "盘点时间", "盘亏原因", "现场备注",
]


def _report_preview(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    sample_limit = payload.get("sample_limit", 5)
    try:
        sample_limit = max(1, min(int(sample_limit), 10))
    except (TypeError, ValueError):
        sample_limit = 5
    sample_rows = [
        {column: str(row.get(column) or "") for column in EXPORT_COLUMNS}
        for row in rows[:sample_limit]
        if isinstance(row, dict)
    ]
    return {
        "row_count": len(rows),
        "columns": EXPORT_COLUMNS,
        "sample_rows": sample_rows,
        "file_created": False,
        "next_step": "管理员确认导出范围后，再调用 Excel 导出工具。",
    }


def _closure_track(payload: dict[str, Any]) -> dict[str, Any]:
    adjustments = payload.get("adjustments") if isinstance(payload.get("adjustments"), list) else []
    overdue_days = 7
    counts = {status: 0 for status in ("pending_review", "dispatched", "pending_verify", "done", "rejected")}
    overdue_items = []
    now = datetime.now()
    for item in adjustments:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
        if status != "dispatched":
            continue
        dispatched_at = str(item.get("dispatched_at") or "").strip()
        try:
            age_days = int((now - datetime.fromisoformat(dispatched_at)).total_seconds() // 86400)
        except (TypeError, ValueError):
            continue
        if age_days >= overdue_days:
            overdue_items.append({
                "adjustment_id": item.get("adjustment_id"),
                "source_task_ids": item.get("source_task_ids") or [],
                "risk_level": str(item.get("risk_level") or ""),
                "priority": str(item.get("priority") or ""),
                "dispatched_at": dispatched_at,
                "reminder_count": max(int(item.get("reminder_count") or 0), 0),
                "reminded_at": str(item.get("reminded_at") or ""),
                "age_days": age_days,
            })
    return {
        "total": sum(counts.values()),
        "status_counts": counts,
        "overdue_days": overdue_days,
        "overdue_count": len(overdue_items),
        "overdue_items": overdue_items[:20],
        "read_only": True,
        "next_step": "如需催办或改变整改状态，必须由管理员通过独立审批入口执行。",
    }


_ROWS_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["rows"],
    "additionalProperties": False,
}
_PREVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {"type": "object"}},
        "sample_limit": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["rows"],
    "additionalProperties": False,
}
_CLOSURE_SCHEMA = {
    "type": "object",
    "properties": {
        "adjustments": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["adjustments"],
    "additionalProperties": False,
}


def _approval_required(_: dict[str, Any]) -> None:
    raise PermissionError("该工具需要管理员确认后才能执行")


_TOOLS: dict[str, ToolSpec] = {
    "inventory.run_rules": ToolSpec(
        name="inventory.run_rules",
        category=ToolCategory.READ_ONLY,
        description="对脱敏盘点数据执行本地确定性规则分析。",
        handler=_inventory_run_rules,
        capability="预检、定期分析已完成盘点数据",
        input_schema=_ROWS_SCHEMA,
    ),
    "inventory.precheck": ToolSpec(
        name="inventory.precheck",
        category=ToolCategory.READ_ONLY,
        description="对导入或已完成盘点数据做本地预检，只返回统计和规则命中清单。",
        handler=_inventory_precheck,
        capability="自动上传机房清单后的预检",
        input_schema=_ROWS_SCHEMA,
    ),
    "report.preview_inventory": ToolSpec(
        name="report.preview_inventory",
        category=ToolCategory.READ_ONLY,
        description="预览当前脱敏盘点数据的 Excel 导出列、行数和样例，不创建文件。",
        handler=_report_preview,
        capability="预览 Excel 导出范围",
        input_schema=_PREVIEW_SCHEMA,
    ),
    "inventory.upload_room_list": ToolSpec(
        name="inventory.upload_room_list",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="自动上传机房清单；涉及文件读取和业务入库，必须走后台上传接口和管理员确认。",
        handler=_approval_required,
        enabled=False,
        capability="自动上传机房清单",
    ),
    "inventory.dispatch_room_tasks": ToolSpec(
        name="inventory.dispatch_room_tasks",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="下发机房盘点任务到现场人员小程序；必须管理员确认任务范围、片区和领取人。",
        handler=_approval_required,
        enabled=False,
        capability="下发机房任务",
    ),
    "report.export_weekly_inventory": ToolSpec(
        name="report.export_weekly_inventory",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="导出当前选定的资产盘点 Excel；涉及原始业务数据导出，必须由管理员明确确认。",
        handler=_approval_required,
        enabled=True,
        capability="定期导出本周资产盘点报告",
        input_schema={
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["task_ids"],
            "additionalProperties": False,
        },
    ),
    "analysis.analyze_completed_rooms": ToolSpec(
        name="analysis.analyze_completed_rooms",
        category=ToolCategory.READ_ONLY,
        description="定期分析已完成盘点的机房资产，只读取后台传入的脱敏上下文。",
        handler=_inventory_precheck,
        capability="定期分析已完成盘点的机房资产",
    ),
    "adjustment.create_review_draft": ToolSpec(
        name="adjustment.create_review_draft",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="根据当前选定任务生成待管理员审核的整改草稿；只创建草稿，不下发到小程序。",
        handler=_approval_required,
        enabled=True,
        capability="输出整改意见管理员审核",
        input_schema={
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["task_ids"],
            "additionalProperties": False,
        },
    ),
    "adjustment.dispatch_confirmed_task": ToolSpec(
        name="adjustment.dispatch_confirmed_task",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="仅将涉及现场处理的整改草稿下发给指定现场人员；资产和综资由管理员在原系统修改，无需派单。必须提供明确的草稿 ID 和人员 ID，并等待管理员确认。",
        handler=_approval_required,
        enabled=True,
        capability="审核无误后下发资产整改任务",
        input_schema={
            "type": "object",
            "properties": {
                "adjustment_id": {"type": "integer", "minimum": 1},
                "assigned_to": {"type": "integer", "minimum": 1},
                "review_note": {"type": "string", "maxLength": 200},
            },
            "required": ["adjustment_id", "assigned_to"],
            "additionalProperties": False,
        },
    ),
    "closure.track_adjustment_loop": ToolSpec(
        name="closure.track_adjustment_loop",
        category=ToolCategory.READ_ONLY,
        description="按固定 7 天阈值只读汇总待审核、已下发、待验收、已闭环、已驳回和超时状态。已闭环仅引用台账状态，不代表独立验证三方一致；不催办、不关闭、不修改数据。",
        handler=_closure_track,
        enabled=True,
        capability="跟进异常资产闭环",
        input_schema=_CLOSURE_SCHEMA,
    ),
    "closure.create_reminder": ToolSpec(
        name="closure.create_reminder",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="为指定已下发整改任务记录一次系统内催办；必须提供明确的整改 ID 并等待管理员确认，不发送微信或短信，不改变任务状态。",
        handler=_approval_required,
        enabled=True,
        capability="管理员确认后记录整改催办",
        input_schema={
            "type": "object",
            "properties": {
                "adjustment_id": {"type": "integer", "minimum": 1},
                "reminder_note": {"type": "string", "maxLength": 200},
            },
            "required": ["adjustment_id"],
            "additionalProperties": False,
        },
    ),
}


def tool_manifest(*, include_approval_required: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "category": spec.category.value,
            "enabled": spec.enabled,
            "approval_required": spec.category == ToolCategory.APPROVAL_REQUIRED,
            "capability": spec.capability,
            "description": spec.description,
            "input_schema": spec.input_schema or {"type": "object", "additionalProperties": False},
        }
        for spec in _TOOLS.values()
        if include_approval_required or spec.category != ToolCategory.APPROVAL_REQUIRED
    ]


def tool_definitions(*, include_approval_required: bool = True) -> list[dict[str, Any]]:
    """Return enabled tools in the OpenAI-compatible tool schema format."""
    return [
        {
            "type": "function",
            "function": {
                # DeepSeek follows the OpenAI function-name constraint and
                # rejects dots. Keep dotted names internally, but expose a
                # reversible wire name to the model.
                "name": model_tool_name(spec.name),
                "description": spec.description,
                "parameters": spec.input_schema or {"type": "object", "additionalProperties": False},
            },
        }
        for spec in _TOOLS.values()
        if spec.enabled and (include_approval_required or spec.category != ToolCategory.APPROVAL_REQUIRED)
    ]


def model_tool_name(name: str) -> str:
    """Map an internal dotted tool name to a provider-compatible name."""
    return str(name).replace(".", "__")


def resolve_tool_name(name: str) -> str:
    """Resolve either an internal name or a model wire name."""
    candidate = str(name)
    if candidate in _TOOLS:
        return candidate
    internal = candidate.replace("__", ".")
    if internal in _TOOLS:
        return internal
    return candidate


def get_tool_spec(name: str) -> ToolSpec:
    spec = _TOOLS.get(name)
    if spec is None:
        raise KeyError(f"未注册工具：{name}")
    if not spec.enabled:
        raise PermissionError(f"工具未启用：{name}")
    return spec


def run_tool(name: str, payload: dict[str, Any], *, allow_approval_required: bool = False) -> Any:
    spec = get_tool_spec(name)
    if spec.category == ToolCategory.APPROVAL_REQUIRED and not allow_approval_required:
        raise PermissionError(f"工具需要管理员确认：{name}")
    return spec.handler(payload)
