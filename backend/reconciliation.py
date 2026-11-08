"""历史双边报告兼容规则，不用于当前三方比对或整改验收。当前入口为 three_way.py。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .database import normalize_model, split_codes


def _task_complete(task: dict[str, Any]) -> bool:
    return task.get("status") == "done" and bool(task.get("field_completed_at") or task.get("completed_at"))


def _issue(
    task: dict[str, Any],
    record: dict[str, Any],
    suffix: str,
    issue_type: str,
    category: str,
    basis: str,
    suggestion: str,
    *,
    source_value: str = "",
    field_value: str = "",
    can_create_adjustment: bool = True,
    source_task_ids: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "issue_key": f"{record.get('id')}:{suffix}",
        "task_id": int(task["id"]),
        "import_batch_id": task.get("import_batch_id") or "",
        "record_id": int(record["id"]),
        "source_task_ids": source_task_ids or [int(task["id"])],
        "task_name": task.get("task_name") or "",
        "room_name": task.get("room_name") or "",
        "area_name": task.get("area_name") or "汇聚及核心",
        "device_name": record.get("device_name") or "",
        "issue_type": issue_type,
        "category": category,
        "basis": basis,
        "suggestion": suggestion,
        "source_value": source_value,
        "field_value": field_value,
        "risk_level": "中",
        "priority": "中",
        "can_create_adjustment": can_create_adjustment,
        "scanned_at": record.get("scanned_at") or "",
    }


def build_reconciliation(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """按固定业务规则核对现场结果和综资快照，不调用 AI。"""
    expected_by_code: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    actual_by_code: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)

    for task in tasks:
        batch_id = task.get("import_batch_id") or ""
        for record in task.get("records") or []:
            record_type = record.get("record_type") or "normal"
            if record_type != "surplus":
                for code in split_codes(record.get("expected_barcode") or ""):
                    expected_by_code[(batch_id, code)].append((task, record))
            if record_type != "missing" and not record.get("asset_no_barcode") and record.get("match_status") != "未扫":
                for code in split_codes(record.get("asset_barcodes") or ""):
                    actual_by_code[(batch_id, code)].append((task, record))

    issues: list[dict[str, Any]] = []
    relocated_expected_ids: set[int] = set()
    location_actual_ids: set[int] = set()
    duplicate_actual_ids: set[int] = set()

    location_pairs: set[tuple[int, int]] = set()
    for batch_code, actual_rows in actual_by_code.items():
        _, code = batch_code
        if len(actual_rows) > 1:
            duplicate_actual_ids.update(int(record["id"]) for _, record in actual_rows)
        for actual_task, actual_record in actual_rows:
            for expected_task, expected_record in expected_by_code.get(batch_code, []):
                if expected_task.get("room_name") == actual_task.get("room_name"):
                    continue
                pair = (int(expected_record["id"]), int(actual_record["id"]))
                if pair in location_pairs:
                    continue
                location_pairs.add(pair)
                key_suffix = f"location:{expected_record['id']}:{actual_record['id']}"
                item = _issue(
                    actual_task,
                    actual_record,
                    key_suffix,
                    "综资机房位置待整改",
                    "location",
                    f"条码 {code} 在综资归属 {expected_task.get('room_name') or '-'}，现场位于 {actual_task.get('room_name') or '-'}",
                    "以现场机房为最终位置，人工确认后修改综资位置。",
                    source_value=expected_task.get("room_name") or "",
                    field_value=actual_task.get("room_name") or "",
                    can_create_adjustment=_task_complete(actual_task),
                    source_task_ids=sorted({int(expected_task["id"]), int(actual_task["id"])}),
                )
                item["issue_key"] = f"location:{expected_record['id']}:{actual_record['id']}"
                item["device_name"] = expected_record.get("device_name") or actual_record.get("device_name") or ""
                issues.append(item)
                relocated_expected_ids.add(int(expected_record["id"]))
                location_actual_ids.add(int(actual_record["id"]))

    matched_count = 0
    device_count = 0
    for task in tasks:
        complete = _task_complete(task)
        for record in task.get("records") or []:
            device_count += 1
            record_id = int(record["id"])
            record_type = record.get("record_type") or "normal"

            if record_id in duplicate_actual_ids:
                issues.append(_issue(
                    task, record, "duplicate", "现场重复扫码待复核", "field_review",
                    "同一资产条码在本次核对范围内被多个设备使用。",
                    "管理员核实设备与条码绑定关系；录入错误修正反馈，严重问题转整改。",
                    field_value=record.get("asset_barcodes") or "",
                    can_create_adjustment=complete,
                ))

            if record_type == "surplus":
                if record_id not in location_actual_ids:
                    issues.append(_issue(
                        task, record, "unmapped", "现场设备待综资认领", "source_missing",
                        "现场已发现设备，但当前核对范围内没有匹配的综资资产条码。",
                        "先核对条码和设备身份，确认后补充或修正综资记录。",
                        field_value=record.get("asset_barcodes") or record.get("actual_model") or "",
                        can_create_adjustment=complete,
                    ))
                continue

            if record_type == "missing":
                if record_id not in relocated_expected_ids:
                    issues.append(_issue(
                        task, record, "batch-wait", "待全批次完成后判断", "waiting",
                        "当前记录现场未找到；在完整导入批次建立前不定性为盘亏。",
                        "完成全批次盘点，并排除跨机房位置变化后再由管理员判断。",
                        source_value=record.get("expected_barcode") or record.get("expected_model") or "",
                        can_create_adjustment=False,
                    ))
                continue

            if record.get("match_status") == "未扫":
                issues.append(_issue(
                    task, record, "unscanned", "待现场完成盘点", "waiting",
                    "该综资设备尚无现场盘点结果。",
                    "完成现场扫码和型号录入后再进行核对。",
                    source_value=record.get("expected_barcode") or record.get("expected_model") or "",
                    can_create_adjustment=False,
                ))
                continue

            record_has_issue = record_id in duplicate_actual_ids or record_id in location_actual_ids
            expected_model = record.get("expected_model") or ""
            actual_model = record.get("actual_model") or ""
            if normalize_model(expected_model) != normalize_model(actual_model):
                issues.append(_issue(
                    task, record, "model", "综资型号待整改", "model",
                    f"综资型号为 {expected_model or '-'}，现场型号为 {actual_model or '-'}。",
                    "以现场型号为准，人工确认后修改综资型号。",
                    source_value=expected_model,
                    field_value=actual_model,
                    can_create_adjustment=complete,
                ))
                record_has_issue = True

            expected_rack = (record.get("expected_rack_no") or "").strip()
            actual_rack = (record.get("rack_no") or "").strip()
            if expected_rack and actual_rack and normalize_model(expected_rack) != normalize_model(actual_rack):
                issues.append(_issue(
                    task, record, "rack", "综资设备机架号待整改", "rack",
                    f"综资设备机架号为 {expected_rack}，现场机架号为 {actual_rack}。",
                    "以现场确认的设备机架号为准，人工确认后修改综资机架号。",
                    source_value=expected_rack,
                    field_value=actual_rack,
                    can_create_adjustment=complete,
                ))
                record_has_issue = True

            expected_codes = split_codes(record.get("expected_barcode") or "")
            actual_codes = split_codes(record.get("asset_barcodes") or "")
            if record.get("asset_no_barcode"):
                issues.append(_issue(
                    task, record, "no-barcode", "现场无条码待复核", "field_review",
                    "现场确认设备没有可扫描资产条码。",
                    "管理员复核无条码事实，再决定是否补码或修正综资关联。",
                    source_value=", ".join(sorted(expected_codes)),
                    can_create_adjustment=complete,
                ))
                record_has_issue = True
            elif record_id not in duplicate_actual_ids and record_id not in location_actual_ids and expected_codes != actual_codes:
                issues.append(_issue(
                    task, record, "barcode", "综资资产条码关联待整改", "barcode",
                    "综资与现场资产条码集合不一致；条码顺序不影响结果。",
                    "以现场核实的条码集合为准，人工确认后修改综资关联。",
                    source_value=", ".join(sorted(expected_codes)) or "无",
                    field_value=", ".join(sorted(actual_codes)) or "无",
                    can_create_adjustment=complete,
                ))
                record_has_issue = True

            # 当前综资快照没有独立的机柜条码字段，暂不把现场机柜条码与设备资产条码混比。
            # 后续导入机柜条码基准后，再增加 cabinet barcode 集合核对。

            if not record_has_issue:
                matched_count += 1

    issues.sort(key=lambda item: (not item["can_create_adjustment"], item["area_name"], item["room_name"], item["record_id"], item["issue_type"]))
    actionable_count = sum(1 for item in issues if item["can_create_adjustment"])
    waiting_count = sum(1 for item in issues if item["category"] == "waiting")
    return {
        "summary": {
            "task_count": len(tasks),
            "complete_task_count": sum(1 for task in tasks if _task_complete(task)),
            "device_count": device_count,
            "matched_count": matched_count,
            "issue_count": len(issues),
            "actionable_count": actionable_count,
            "waiting_count": waiting_count,
            "batch_complete": bool(tasks) and all(_task_complete(task) and task.get("batch_scope_complete", True) for task in tasks),
        },
        "issues": issues,
        "rules": [
            "不同导入批次独立核对，不把往期条码视为本轮重复扫码或位置变化",
            "现场型号为准",
            "资产条码按标准化后的完整集合比对，忽略顺序",
            "综资与现场设备机架号逐项比对，现场机架号为准",
            "现场机柜条码已采集；综资没有机柜条码基准前不做强制判差",
            "设备现场所在机房为最终位置",
            "未找到设备在全批次完成前不定性为盘亏",
        ],
    }
