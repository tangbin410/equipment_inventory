"""Whitelisted tools for the local inventory agent.

The model is not allowed to call arbitrary Python functions. The agent may only
invoke tools registered here, and write-like tools require an explicit approval
path before they can run.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def _inventory_run_rules(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return run_rules(rows)


def _inventory_precheck(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    room_count = len({str(row.get("机房名称") or "").strip() for row in rows if isinstance(row, dict) and row.get("机房名称")})
    scanned_count = sum(1 for row in rows if isinstance(row, dict) and str(row.get("盘点时间") or "").strip())
    return {
        "row_count": len(rows),
        "room_count": room_count,
        "scanned_count": scanned_count,
        "unscanned_count": max(len(rows) - scanned_count, 0),
        "rule_items": run_rules(rows),
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
    ),
    "inventory.precheck": ToolSpec(
        name="inventory.precheck",
        category=ToolCategory.READ_ONLY,
        description="对导入或已完成盘点数据做本地预检，只返回统计和规则命中清单。",
        handler=_inventory_precheck,
        capability="自动上传机房清单后的预检",
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
        description="定期导出本周资产盘点报告；涉及原始业务数据导出，必须由管理员配置计划和确认权限。",
        handler=_approval_required,
        enabled=False,
        capability="定期导出本周资产盘点报告",
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
        description="根据 AI 分析结果生成整改意见和资产调整任务草稿，等待管理员审核。",
        handler=_approval_required,
        enabled=False,
        capability="输出整改意见管理员审核",
    ),
    "adjustment.dispatch_confirmed_task": ToolSpec(
        name="adjustment.dispatch_confirmed_task",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="将管理员确认后的资产调整任务下发到现场人员小程序；禁止模型直接执行。",
        handler=_approval_required,
        enabled=False,
        capability="审核无误后下发资产整改任务",
    ),
    "closure.track_adjustment_loop": ToolSpec(
        name="closure.track_adjustment_loop",
        category=ToolCategory.APPROVAL_REQUIRED,
        description="跟进异常资产整改闭环；涉及业务状态变更和催办，必须由管理员授权。",
        handler=_approval_required,
        enabled=False,
        capability="跟进异常资产闭环",
    ),
}


def tool_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "category": spec.category.value,
            "enabled": spec.enabled,
            "approval_required": spec.category == ToolCategory.APPROVAL_REQUIRED,
            "capability": spec.capability,
            "description": spec.description,
        }
        for spec in _TOOLS.values()
    ]


def run_tool(name: str, payload: dict[str, Any], *, allow_approval_required: bool = False) -> Any:
    spec = _TOOLS.get(name)
    if spec is None:
        raise KeyError(f"未注册工具：{name}")
    if not spec.enabled:
        raise PermissionError(f"工具未启用：{name}")
    if spec.category == ToolCategory.APPROVAL_REQUIRED and not allow_approval_required:
        raise PermissionError(f"工具需要管理员确认：{name}")
    return spec.handler(payload)
