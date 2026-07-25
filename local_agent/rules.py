"""Deterministic inventory rules before LLM reasoning."""

from __future__ import annotations

from collections import Counter, defaultdict
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


def run_rules(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not rows:
        return items

    by_room: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_room[_room(row)].append(row)

    for room, room_rows in by_room.items():
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
                    "优先核对现场盘点任务是否完成、设备是否迁移或已退网，并安排二次补盘。",
                ))

        no_asset = [
            row for row in room_rows
            if _is_yes(row.get("资产无条码")) or _is_missing_barcode(row.get("现场资产条码"))
        ]
        if len(no_asset) >= 2:
            items.append(_item(
                "现场资产条码缺失",
                room,
                _join_top([_device(row) for row in no_asset]),
                "中",
                f"{room} 有 {len(no_asset)} 条记录缺少现场资产条码或标记无条码",
                "补贴资产条码或补录可追溯编号，整改后重新扫码确认。",
            ))

        no_cabinet = [
            row for row in room_rows
            if _is_yes(row.get("机柜无条码")) or _is_missing_barcode(row.get("现场机柜条码"))
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
            if _text(row.get("盘点时间")) and not _text(row.get("现场设备型号"))
        ]
        if len(missing_model) >= 2:
            items.append(_item(
                "现场设备型号缺失",
                room,
                _join_top([_device(row) for row in missing_model]),
                "低",
                f"{room} 有 {len(missing_model)} 条已盘记录未填写现场设备型号",
                "复核设备铭牌或网管资源信息，补录现场型号。",
            ))

    barcode_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        barcode_text = _text(row.get("现场资产条码"))
        if _is_missing_barcode(barcode_text):
            continue
        for barcode in [part.strip() for part in barcode_text.replace("，", ",").split(",") if part.strip()]:
            if barcode not in {"无条码", "无"}:
                barcode_rows[barcode].append(row)

    for barcode, dup_rows in barcode_rows.items():
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

    status_counter = Counter(_text(row.get("现场状态")) for row in rows if _text(row.get("现场状态")))
    for status, count in status_counter.items():
        if status != "正常" and count >= 3:
            affected = [row for row in rows if _text(row.get("现场状态")) == status]
            items.append(_item(
                f"现场状态异常集中：{status}",
                _join_top(sorted({_room(row) for row in affected}), 3),
                _join_top([_device(row) for row in affected], 5),
                "中",
                f"现场状态为“{status}”的记录共 {count} 条",
                "按异常状态分类复核，形成整改责任人、整改期限和复盘结果。",
            ))

    return items[:20]

