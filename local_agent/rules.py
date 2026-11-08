"""Deterministic inventory rules before LLM reasoning."""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any


ANALYSIS_KEYS = ["问题类型", "涉及机房", "涉及设备", "风险等级", "判断依据", "整改建议", "优先级"]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _room(row: dict[str, Any]) -> str:
    return _text(row.get("机房名称")) or "未标识机房"


def _device(row: dict[str, Any]) -> str:
    return _text(row.get("设备名称")) or "多设备"


def _is_yes(value: Any) -> bool:
    return _text(value) in {"是", "true", "True", "1", "yes", "YES"}


def _is_missing_barcode(value: Any) -> bool:
    text = _text(value)
    return not text or text in {"无条码", "无", "-", "N/A", "NA"}


def _priority(risk: str) -> str:
    return {"高": "P1", "中": "P2", "低": "P3"}.get(risk, "P3")


def _item(problem: str, room: str, device: str, risk: str, basis: str, suggestion: str) -> dict[str, str]:
    return {
        "问题类型": problem,
        "涉及机房": room,
        "涉及设备": device,
        "风险等级": risk,
        "判断依据": basis,
        "整改建议": suggestion,
        "优先级": _priority(risk),
    }


def _join_top(values: list[str], limit: int = 5) -> str:
    filtered = [value for value in values if value]
    return "、".join(filtered[:limit]) if filtered else "多设备"


def _codes(value: Any) -> set[str]:
    if _is_missing_barcode(value):
        return set()
    return {part.strip().upper() for part in re.split(r"[,，;；\r\n]+", _text(value)) if part.strip()}


def _field_present(row: dict[str, Any]) -> bool:
    return bool(_text(row.get("盘点时间"))) and _text(row.get("现场状态")) not in {"盘亏", "现场未找到", "未找到", "未盘点"}


def run_rules(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not rows:
        return items

    if any(row.get("比对模式") for row in rows):
        for row in rows:
            basis = _text(row.get("规则问题类型"))
            if not basis:
                continue
            manual = row.get("比对模式") == "多条码人工模式B"
            items.append(_item(
                "多条码人工核对" if manual else "三方信息待处理", _room(row), _device(row), "中", basis,
                "由管理员完成三方人工核对，确认问题后再建单。" if manual else "依据页面固定规则核实问题；资产和综资由管理员在原系统修改并更新数据，现场问题审核后派单，三方一致后验收。",
            ))
        return items

    # 多条码采用人工模式 B，不能进入其他自动规则或拆成单码自动判断。
    automatic_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        expected_count = len(_codes(row.get("清单资产条码")))
        actual_count = len(_codes(row.get("现场资产条码")))
        if max(expected_count, actual_count) > 1:
            items.append(_item(
                "多条码设备待人工核对", _room(row), _device(row), "中",
                f"清单条码 {expected_count} 个，现场条码 {actual_count} 个；本设备采用人工模式 B。",
                "三方对照值及核对结论全部由管理员填写，系统只展示、保存和留痕；不自动判差或派单。",
            ))
        else:
            automatic_rows.append(row)

    by_room: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in automatic_rows:
        by_room[(_text(row.get("导入批次")), _room(row))].append(row)

    for (_, room), room_rows in by_room.items():
        total = len(room_rows)
        unscanned = [row for row in room_rows if not _text(row.get("盘点时间"))]
        if unscanned:
            ratio = len(unscanned) / max(total, 1)
            if len(unscanned) >= 3 or ratio >= 0.3:
                risk = "高" if ratio >= 0.5 else "中"
                items.append(_item(
                    "未盘设备集中",
                    room,
                    _join_top([_device(row) for row in unscanned]),
                    risk,
                    f"{room} 共 {total} 条清单，未盘 {len(unscanned)} 条，占比 {ratio:.0%}",
                    "未完成任务先补充现场采集；已完成任务录入错误由管理员依据凭证修正，严重问题转整改，不退回整间机房重盘。",
                ))

        no_asset = [
            row for row in room_rows
            if _field_present(row) and (_is_yes(row.get("资产无条码")) or _is_missing_barcode(row.get("现场资产条码")))
        ]
        if len(no_asset) >= 2:
            items.append(_item(
                "现场资产条码缺失",
                room,
                _join_top([_device(row) for row in no_asset]),
                "中",
                f"{room} 有 {len(no_asset)} 条记录缺少现场资产条码或标记无条码",
                "待管理员核实并补充真实资产条码；涉及实物补贴等现场操作时，经管理员审核后派单。未补齐条码不能认定三方一致。",
            ))

        no_cabinet = [
            row for row in room_rows
            if _field_present(row) and (_is_yes(row.get("机柜无条码")) or _is_missing_barcode(row.get("现场机柜条码")))
        ]
        if len(no_cabinet) >= 2:
            items.append(_item(
                "机柜条码缺失",
                room,
                _join_top([_device(row) for row in no_cabinet]),
                "中",
                f"{room} 有 {len(no_cabinet)} 条记录缺少机柜条码或标记无条码",
                "补齐机柜条码，统一机柜编码口径，避免后续定位和维护派单困难。",
            ))

        missing_model = [
            row for row in room_rows
            if _field_present(row) and not _text(row.get("现场设备型号"))
        ]
        if len(missing_model) >= 2:
            items.append(_item(
                "现场设备型号缺失",
                room,
                _join_top([_device(row) for row in missing_model]),
                "低",
                f"{room} 有 {len(missing_model)} 条已盘记录未填写现场设备型号",
                "由管理员核实并补充型号数据；三方规格型号是否一致须管理员整体判断，AI 不自行判定。",
            ))

    barcode_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in automatic_rows:
        if not _field_present(row):
            continue
        barcode_text = _text(row.get("现场资产条码"))
        if _is_missing_barcode(barcode_text):
            continue
        for barcode in _codes(barcode_text):
            if barcode not in {"无条码", "无"}:
                barcode_rows[(_text(row.get("导入批次")), barcode)].append(row)

    for (_, barcode), dup_rows in barcode_rows.items():
        devices = {_device(row) for row in dup_rows}
        rooms = {_room(row) for row in dup_rows}
        if len(dup_rows) >= 2 and (len(devices) > 1 or len(rooms) > 1):
            items.append(_item(
                "同一资产条码重复",
                _join_top(sorted(rooms), 3),
                _join_top(sorted(devices), 5),
                "高",
                f"资产条码 {barcode} 出现在 {len(dup_rows)} 条记录中",
                "以现场照片、设备铭牌和资产系统为准核实唯一归属，清理重复或误扫记录。",
            ))

    status_counter = Counter(_text(row.get("现场状态")) for row in automatic_rows if _text(row.get("现场状态")))
    for status, count in status_counter.items():
        if status not in {"正常", "未盘点"} and count >= 3:
            affected = [row for row in automatic_rows if _text(row.get("现场状态")) == status]
            display_status = {"盘亏": "现场未找到", "盘盈": "现场新增"}.get(status, status)
            items.append(_item(
                f"现场记录待人工确认：{display_status}",
                _join_top(sorted({_room(row) for row in affected}), 3),
                _join_top([_device(row) for row in affected], 5),
                "中",
                f"现场记录状态为“{display_status}”的共 {count} 条；不据此定性财务盘盈盘亏。",
                "管理员先确认问题；现场操作审核后派单，资产和综资问题由管理员在原系统修改后上传核对。",
            ))

    return items[:20]

