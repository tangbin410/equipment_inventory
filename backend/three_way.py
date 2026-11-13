"""三方比对与整改的确定性规则；AI 只读取结论，不参与状态变更。"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from uuid import uuid4
from . import database as db, inventory_pairing

SYSTEMS = ("资产", "综资", "现场")
STATES = ("待判断", "一致", "不一致", "无法确认")


def text(value):
    return str(value if value is not None else "").strip()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def init_schema(conn):
    if "room_code" not in {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}:
        conn.execute("ALTER TABLE tasks ADD COLUMN room_code TEXT NOT NULL DEFAULT ''")
    if "comparison_json" not in {r["name"] for r in conn.execute("PRAGMA table_info(ai_adjustment_tasks)")}:
        conn.execute("ALTER TABLE ai_adjustment_tasks ADD COLUMN comparison_json TEXT NOT NULL DEFAULT ''")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS resource_baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
            rows_json TEXT NOT NULL, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_resource_baselines_task ON resource_baselines(task_id, id);
        CREATE TABLE IF NOT EXISTS comparison_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_key TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL, kind TEXT NOT NULL,
            decision_json TEXT NOT NULL, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_comparison_decisions_group ON comparison_decisions(group_key, id);
        CREATE TABLE IF NOT EXISTS comparison_source_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL,
            kind TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_comparison_changes_entity ON comparison_source_changes(entity,kind,id);
        CREATE TABLE IF NOT EXISTS comparison_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER REFERENCES ai_adjustment_tasks(id),
            action TEXT NOT NULL, data_json TEXT NOT NULL, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL
        );
    """)


def event(conn, action, actor, data, case_id=None):
    conn.execute("INSERT INTO comparison_events(case_id,action,data_json,actor_id,created_at) VALUES(?,?,?,?,?)",
                 (case_id, action, encode(data), actor, datetime.now().isoformat()))


def selected_tasks(conn, ids):
    tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE COALESCE(is_deleted,0)=0 ORDER BY id DESC")]
    if ids:
        ids = set(ids)
        selected = [t for t in tasks if t["id"] in ids]
        if len(selected) != len(ids):
            raise ValueError("选中的任务不存在或已作废")
    else:
        completed = [t for t in tasks if t["status"] == "done"]
        selected = [t for t in completed if t["import_batch_id"] == completed[0]["import_batch_id"]] if completed else []
    if len({t["import_batch_id"] for t in selected}) > 1:
        raise ValueError("请按导入批次分别比对，不混用历史现场数据")
    return selected


def source_row(system, barcode, name, code, model, task=None, record_id=None, rack_no=""):
    return dict(system=system, barcode=text(barcode), name=text(name), location_code=text(code),
                model=text(model), rack_no=text(rack_no), task_id=task["id"] if task else None, record_id=record_id,
                room_name=task["room_name"] if task else "")


def source_records(group, system):
    """网元归属记录与缺码记录分别保存，条码只是被核对的字段。"""
    return group["sources"][system] + group.get("pending_sources", {}).get(system, [])


def record_links(row):
    links = []
    for field in ("record_id", "entity_id"):
        if row.get(field):
            links.append((row.get("task_id"), field, row[field]))
    return links


def attach_uncoded_feedback(groups, uncoded, batch):
    # 只沿用同一任务设备记录的归属；绝不按名称、型号猜测条码。
    owners = defaultdict(set)
    for index, group in enumerate(groups):
        group["pending_sources"] = {s: [] for s in SYSTEMS}
        for system in SYSTEMS:
            group["sources"][system] = [r for r in group["sources"][system] if db.split_codes(r["barcode"])]
            for row in group["sources"][system]:
                for link in record_links(row):
                    owners[link].add(index)
    orphans = {}
    for row in uncoded:
        candidates = set().union(*(owners[link] for link in record_links(row)))
        if len(candidates) == 1:
            group = groups[next(iter(candidates))]
        else:
            identity = record_links(row)
            key = f"{batch}:empty:{row['task_id']}:" + (f"{identity[0][1]}:{identity[0][2]}" if identity else fingerprint(row)[:16])
            if key not in orphans:
                orphans[key] = dict(group_key=key, barcode="", manual=False, no_barcode=True,
                                    sources={s: [] for s in SYSTEMS}, pending_sources={s: [] for s in SYSTEMS})
            group = orphans[key]
        group["pending_sources"][row["system"]].append(row)
    groups.extend(orphans.values())
    for group in groups:
        group["pending_association"] = any(group["pending_sources"].values())
    return groups


def relation(rows, field, pending=None):
    pending = pending or {s: [] for s in SYSTEMS}
    detail = "；".join(f"{s}：" + (" / ".join(r[field] or "未填写" for r in rows[s] + pending[s]) if rows[s] or pending[s] else "缺失") + ("（已有记录，条码待补，尚未关联）" if pending[s] else "") for s in SYSTEMS)
    if any(len(rows[s]) > 1 for s in SYSTEMS):
        return "待处理", detail + "（重复关联，待管理员处理）"
    present = [r[field] for s in SYSTEMS for r in rows[s] if r[field]]
    if len(set(present)) > 1:
        return "不一致", detail
    if any(pending.values()):
        return "待关联", detail
    if len(present) != 3:
        return "待处理", detail
    return "一致", detail


def resource_field_comparison(resources, fields, missing):
    """按同一任务内的唯一网元名称关联；条码只在关联后核对。"""
    owners = defaultdict(set)
    names = defaultdict(int)
    for index, row in enumerate(resources):
        if row["name"]:
            names[(row["task_id"], row["name"])] += 1
        for link in record_links(row):
            owners[link].add(index)
    owners_by_name = defaultdict(set)
    for index, row in enumerate(resources):
        if row["name"]:
            owners_by_name[(row["task_id"], row["name"])].add(index)
    paired = defaultdict(list)
    unmatched = []
    conflicts = set()
    for row in fields:
        name_candidates = owners_by_name[(row["task_id"], row["name"])] if row["name"] else set()
        linked_candidates = set().union(*(owners[link] for link in record_links(row)))
        if len(name_candidates) == 1 and (not linked_candidates or linked_candidates == name_candidates):
            paired[next(iter(name_candidates))].append(row)
        else:
            status = ("同名多条，待管理员关联" if len(name_candidates) > 1
                      else "网元名称与设备记录归属冲突" if linked_candidates
                      else "现场网元名称未填写" if not row["name"]
                      else "综资无对应网元名称")
            if status == "网元名称与设备记录归属冲突":
                conflicts.update(linked_candidates)
            unmatched.append((row, status))

    result = []
    for index, resource in enumerate(resources):
        feedback = paired[index]
        if resource["name"] and names[(resource["task_id"], resource["name"])] > 1:
            status = "同名多条，待管理员关联"
        elif not resource["name"]:
            status = "综资网元名称未填写"
        elif index in conflicts:
            status = "网元名称与设备记录归属冲突"
        elif len(feedback) > 1:
            status = "重复现场反馈"
        elif not feedback:
            status = "现场登记盘亏" if (resource.get("task_id"), resource.get("record_id")) in missing else "现场未反馈"
        elif not db.split_codes(resource["barcode"]):
            status = "综资条码未填写"
        elif not db.split_codes(feedback[0]["barcode"]):
            status = "现场条码待补录"
        elif len(db.split_codes(resource["barcode"])) != 1 or len(db.split_codes(feedback[0]["barcode"])) != 1:
            status = "多条码人工核对"
        else:
            status = "一致" if db.split_codes(resource["barcode"]) == db.split_codes(feedback[0]["barcode"]) else "条码不一致"
        field = feedback[0] if len(feedback) == 1 else None
        rack_status = pair_value_status(resource, field, "rack_no") if field and status not in {"同名多条，待管理员关联", "综资网元名称未填写"} else status
        result.append(dict(room_name=resource["room_name"], name=resource["name"], resource=resource,
                           field=field, status=status, barcode_status=status, rack_status=rack_status,
                           model_status="待判断", feedback=feedback))
    for field, status in unmatched:
        result.append(dict(room_name=field["room_name"], name=field["name"], resource=None,
                           field=field, status=status, barcode_status=status, rack_status=status,
                           model_status="待判断", feedback=[field]))
    return result


def pair_value_status(left, right, field):
    if not left or not right or not left.get(field) or not right.get(field):
        return "待处理"
    if field == "barcode":
        left_codes, right_codes = db.split_codes(left[field]), db.split_codes(right[field])
        if len(left_codes) != 1 or len(right_codes) != 1:
            return "待判断"
        return "一致" if left_codes == right_codes else "不一致"
    return "一致" if text(left[field]) == text(right[field]) else "不一致"


def rack_from_extra(raw):
    try:
        extra = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        return text(extra.get("设备机架") or extra.get("rack_no"))
    except (TypeError, ValueError):
        return ""


def asset_rack_type(fields):
    """只识别 ODF/IODF，不用规格型号中的“机架/机柜”判断设备类别。"""
    for key in ("资源类别", "资产名称"):
        value = text(fields.get(key)).upper()
        for kind in ("IODF", "ODF"):
            if re.search(rf"(?<![A-Z]){kind}(?![A-Z])", value):
                return kind.lower()
    match = re.search(r"(?:^|[/\\])\s*(IODF|ODF)(?=[-_|\s]|$)", text(fields.get("资源名称")).upper())
    return match.group(1).lower() if match else ""


def cabinet_position(value):
    value = text(value)
    return value[len("设备机架-"):] if value.startswith("设备机架-") else value


def equipment_cabinet_comparison(tasks, resources, fields, assets):
    """综资仅提供设备所属柜位；柜条码只核对现场与资产，不能冒充综资柜条码。"""
    grouped = {}
    task_by_id = {t["id"]: t for t in tasks}
    task_by_code = {text(t["room_code"]): t for t in tasks}
    barcode_positions = defaultdict(set)

    def get_group(task, position, identity=""):
        key = (task["id"], position or identity)
        if key not in grouped:
            grouped[key] = dict(task_id=task["id"], room_name=task["room_name"], room_code=task["room_code"],
                                position=position, resources=[], fields=[], assets=[])
        return grouped[key]

    for row in resources:
        position = cabinet_position(row.get("rack_no"))
        if position:
            get_group(task_by_id[row["task_id"]], position)["resources"].append(row)
    for row in fields:
        position = cabinet_position(row.get("rack_no"))
        group = get_group(task_by_id[row["task_id"]], position, f"field:{row['record_id']}")
        group["fields"].append(row)
        for barcode in db.split_codes(row.get("cabinet_barcodes", "")):
            barcode_positions[(row["task_id"], barcode)].add(position)
    for asset in assets.values():
        if asset["entity_kind"] == "cabinet":
            task = task_by_code.get(asset["location_code"])
            if task:
                get_group(task, cabinet_position(asset["name"]), f"asset:{asset['barcode']}")["assets"].append(asset)

    result = []
    for group in grouped.values():
        resource_rows, field_rows, asset_rows = group.pop("resources"), group.pop("fields"), group.pop("assets")
        resource_names = list(dict.fromkeys(text(r.get("rack_no")) for r in resource_rows))
        asset_names = list(dict.fromkeys(a["name"] for a in asset_rows))
        field_codes = set().union(*(db.split_codes(r.get("cabinet_barcodes", "")) for r in field_rows))
        asset_codes = {a["barcode"] for a in asset_rows}
        no_barcode = any(r.get("cabinet_no_barcode") for r in field_rows)
        problems = []
        if not resource_rows:
            problems.append("综资设备清单未找到该柜位")
        if not field_rows:
            problems.append("现场未反馈该机柜")
        if not asset_rows:
            problems.append("资产表未找到对应机柜资源名称")
        if len(asset_rows) > 1:
            problems.append("机柜资源同名多条，待管理员核实")
        if len(field_codes) > 1:
            problems.append("同一柜位登记多个机柜条码，待管理员核实")
        if no_barcode:
            problems.append("现场机柜登记无条码" + ("，与其他设备的机柜条码反馈冲突" if field_codes else ""))
        if any(len(barcode_positions[(group["task_id"], code)]) > 1 for code in field_codes):
            problems.append("同一机柜条码对应多个柜位，待管理员核实")
        position_status = "一致" if resource_rows and field_rows and group["position"] else "待核实"
        barcode_status = "待核实"
        if field_codes and asset_codes and not no_barcode:
            barcode_status = "一致" if field_codes == asset_codes else "不一致"
        if barcode_status == "不一致":
            problems.append("现场与资产机柜条码不一致")
        location_status = "一致" if field_rows and asset_rows and all(a["location_code"] == group["room_code"] for a in asset_rows) else "待核实"
        result.append(dict(**group,
            resource_name=(resource_names or asset_names or [group["position"] or "未填写柜位"])[0],
            resource_position=group["position"] if resource_rows else "", field_position=group["position"] if field_rows else "",
            field_barcodes="、".join(sorted(field_codes)) or ("现场无条码" if no_barcode else "未反馈"),
            asset_barcodes="、".join(sorted(asset_codes)), asset_records=asset_rows,
            field_devices=list(dict.fromkeys(r["name"] for r in field_rows)),
            resource_devices=list(dict.fromkeys(r["name"] for r in resource_rows)),
            position_status=position_status, barcode_status=barcode_status, location_status=location_status,
            resource_barcode_status="未提供（综资清单仅含柜位）", problems=problems,
            consistent=bool(not problems and position_status == barcode_status == location_status == "一致"),
            status="已核对（综资仅含柜位）" if not problems and position_status == barcode_status == location_status == "一致" else "待核实 / 存在差异"))
    return result


def rack_comparison(conn, tasks, assets):
    """现场机架与资产机架取并集；资产独有机架也必须显示，历史记录只用于分类。"""
    by_name = defaultdict(list)
    for asset in assets.values():
        if asset["name"]:
            by_name[(asset["location_code"], asset["name"])].append(asset)
    result, used_assets = [], set()

    def append_row(task, name, fields, matches, rack_type):
        field = fields[0] if len(fields) == 1 else None
        asset = matches[0] if len(matches) == 1 else None
        status = ("同名多条现场记录，待管理员关联" if len(fields) > 1 else
                  "同名多条，待管理员关联" if len(matches) > 1 else
                  "现场未反馈" if not fields else
                  "资产表未找到对应资源名称" if not matches else "")
        used_assets.update(a["barcode"] for a in matches)
        result.append(dict(task_id=task["id"], cabinet_id=field["record_id"] if field else None,
                           room_name=task["room_name"], room_code=task["room_code"], rack_type=rack_type.upper(),
                           resource_name=name, field_barcode="、".join(f["barcode"] or "现场无条码" for f in fields) or "未反馈",
                           field_location_code=field["location_code"] if field else "",
                           field_status="现场已登记" if fields else "资产表有记录，现场未反馈",
                           asset_barcodes="、".join(a["barcode"] for a in matches),
                           asset_location_codes="、".join(dict.fromkeys(a["location_code"] for a in matches)),
                           asset_records=matches,
                           barcode_status=status or pair_value_status(field, asset, "barcode"),
                           location_status=status or pair_value_status(field, asset, "location_code"),
                           resource_status="综资机架基线待补"))

    for task in tasks:
        current = defaultdict(list)
        rack_types = {}
        for raw in conn.execute("SELECT * FROM cabinet_records WHERE task_id=? AND is_void=0 AND cabinet_type IN ('odf','iodf') ORDER BY id", (task["id"],)):
            rack = db._cabinet_from_row(raw)
            name = text(rack["rack_no"])
            current[name].append(source_row("现场", "" if rack["no_barcode"] else rack["cabinet_barcode"],
                                            name, task["room_code"], "", task, rack["id"], name))
            rack_types[name] = rack["cabinet_type"]
        for name, fields in current.items():
            matches = by_name[(text(task["room_code"]), name)] if name else []
            append_row(task, name, fields, matches, rack_types[name])

    asset_only = defaultdict(list)
    task_by_code = {text(t["room_code"]): t for t in tasks if text(t["room_code"])}
    for asset in assets.values():
        if asset["entity_kind"] == "rack" and asset["barcode"] not in used_assets and asset["location_code"] in task_by_code:
            asset_only[(asset["location_code"], asset["name"])].append(asset)
    for (code, name), matches in asset_only.items():
        append_row(task_by_code[code], name, [], matches, matches[0]["rack_type"])
    return result


def _paired_sources(conn, tasks, resources, fields, missing):
    """拼销只派生当前视图，原采集和原综资记录继续保留。"""
    task_map = {task["id"]: task for task in tasks}
    notices, outgoing_names, stale = [], set(), {}
    for pair in inventory_pairing.get_active_pairs(conn):
        before, found = pair["missing"], pair["surplus"]
        if not any(row and row["task_id"] in task_map for row in (before, found)):
            continue
        meta = inventory_pairing._metadata(pair)
        notices.append(meta)
        if not pair["valid"]:
            for record_id in (pair["missing_record_id"], pair["surplus_record_id"]):
                stale[record_id] = meta
            continue
        source_id, target_id = before["task_id"], found["task_id"]
        resources = [row for row in resources if not (row["task_id"] == source_id and row.get("record_id") == before["id"])]
        fields = [row for row in fields if row.get("record_id") not in (before["id"], found["id"])]
        missing.discard((source_id, before["id"]))
        if pair["cross_room"]:
            outgoing_names.add((text(before.get("resource_location_code") or before["room_code"]), before["device_name"]))
        if target_id not in task_map:
            continue
        task = task_map[target_id]
        # 跨机房仍引用迁出机房的综资，不能把确认拼销当成已完成调拨。
        resource = source_row("综资", before["expected_barcode"], before["device_name"],
                              before.get("resource_location_code") or before["room_code"],
                              before["expected_model"], task, found["id"], before.get("expected_rack_no"))
        field = source_row("现场", "" if found["asset_no_barcode"] else found["asset_barcodes"],
                           before["device_name"], task["room_code"], found["actual_model"], task, found["id"], found["rack_no"])
        for row in (resource, field):
            row.update(entity_id=f"pair:{pair['id']}", inventory_pairing=meta)
        resource["source_label"] = "拼销综资参考" + (f"（{before['room_name']}）" if pair["cross_room"] else "")
        field.update(barcode_state="现场无条码" if found["asset_no_barcode"] else "已采集" if field["barcode"] else "未填写",
                     cabinet_barcodes=found.get("cabinet_barcodes") or "", cabinet_no_barcode=bool(found.get("cabinet_no_barcode")),
                     record_type="normal", source_label="拼销后的现场反馈", revision_id=found.get("latest_correction_id") or 0,
                     updated_at=found.get("corrected_at") or found.get("scanned_at"))
        resources.append(resource)
        fields.append(field)
    for row in resources + fields:
        if row.get("record_id") in stale:
            row["inventory_pairing"] = stale[row["record_id"]]
    return resources, fields, notices, outgoing_names


def build(conn, task_ids):
    tasks = selected_tasks(conn, task_ids)
    asset_status = db.get_asset_info_status(conn)
    warnings = []
    baselines = {}
    for task in tasks:
        baseline = conn.execute("SELECT * FROM resource_baselines WHERE task_id=? ORDER BY id DESC LIMIT 1", (task["id"],)).fetchone()
        if baseline:
            rows = json.loads(baseline["rows_json"])
            baselines[task["id"]] = (baseline["id"], rows)
            codes = {text(row.get("location_code")) for row in rows}
            # 本次比对范围服从最新综资基线；不改写历史任务清单。
            task["room_code"] = next(iter(codes)) if len(codes) == 1 else ""
            if not task["room_code"]:
                warnings.append(f"{task['room_name']}最新综资基线缺少唯一的地点编码，请先核实")
    if not tasks:
        warnings.append("暂无现场已完成的任务")
    if any(t["status"] != "done" for t in tasks):
        warnings.append("仅现场人工确认完成的机房可以正式比对")
    for t in tasks:
        if not text(t["room_code"]):
            warnings.append(f"{t['room_name']}（任务 #{t['id']}）缺少地点编码，请先补齐")
    if not asset_status:
        warnings.append("请先上传资产信息表")
    elif not {"资源名称", "地点编码", "规格型号"}.issubset(asset_status["headers"]):
        warnings.append("资产信息表缺少资源名称、地点编码或规格型号表头，请更新资产信息表")
    codes = {text(t["room_code"]) for t in tasks if text(t["room_code"])}
    batch = tasks[0]["import_batch_id"] if tasks else ""
    assets = {}
    if asset_status:
        for r in conn.execute("SELECT * FROM asset_info_records WHERE batch_id=?", (asset_status["id"],)):
            f = json.loads(r["fields_json"])
            # 先按综资机房地点编码限定资产范围，再按资源名称关联。
            if text(f.get("地点编码")) not in codes:
                continue
            asset = source_row("资产", f.get(asset_status["barcode_column"]), f.get("资源名称"), f.get("地点编码"), f.get("规格型号"))
            asset.update(rack_type=asset_rack_type(f), resource_category=text(f.get("资源类别")), asset_name=text(f.get("资产名称")))
            assets[r["barcode_norm"]] = asset
    known_racks = {}
    for task in tasks:
        for rack in db.get_task_cabinets(task["id"]) + db.get_prior_room_racks(task["id"]):
            if rack["cabinet_type"] in ("odf", "iodf"):
                known_racks[(text(task["room_code"]), rack["rack_no"])] = rack["cabinet_type"]
    resources, fields = [], []
    missing = set()
    revisions = {}
    for task in tasks:
        records = [dict(r) for r in conn.execute("SELECT * FROM scan_records WHERE task_id=? AND COALESCE(is_void,0)=0 ORDER BY id", (task["id"],))]
        baseline = baselines.get(task["id"])
        revisions[str(task["id"])] = baseline[0] if baseline else 0
        current_resources = baseline[1] if baseline else [
            source_row("综资", r["expected_barcode"], r["device_name"], task["room_code"], r["expected_model"], task, r["id"], rack_from_extra(r.get("device_extra")))
            for r in records if r["record_type"] != "surplus"]
        for resource in current_resources:
            resource.setdefault("rack_no", "")
        resources.extend(current_resources)
        for raw in records:
            r = db.apply_latest_record_correction(conn, raw)
            if r["record_type"] == "missing":
                missing.add((task["id"], raw["id"]))
                continue
            if not r.get("scanned_at"):
                continue
            row = source_row("现场", "" if r["asset_no_barcode"] else r["asset_barcodes"], r["device_name"], task["room_code"], r["actual_model"], task, r["id"], r.get("rack_no"))
            # 名称、地点来自该现场记录所属清单；绝不按实扫条码倒推名称。
            extra = json.loads(raw.get("device_extra") or "{}")
            if extra.get("comparison_entity"):
                row["entity_id"] = extra["comparison_entity"]
            owners = [r for r in current_resources if r.get("record_id") == raw["id"] or r.get("entity_id") and r.get("entity_id") == row.get("entity_id")]
            if len(owners) == 1:
                row["location_code"] = owners[0]["location_code"]
                if not r.get("device_name_corrected_at"):
                    row["name"] = owners[0]["name"]
            row["barcode_state"] = "现场无条码" if r["asset_no_barcode"] else "未填写" if not row["barcode"] else "已采集"
            row["record_type"] = r["record_type"]
            row.update(cabinet_barcodes=r.get("cabinet_barcodes") or "", cabinet_no_barcode=bool(r.get("cabinet_no_barcode")))
            source_label = "现场整改反馈" if r.get("correction_reason") == "现场整改反馈" else "管理员修正" if r.get("latest_correction_id") else r.get("inherited_source_label") or "现场原始反馈"
            row.update(source_label=source_label,
                       revision_id=r.get("latest_correction_id") or 0, updated_at=r.get("corrected_at") or r.get("scanned_at"),
                       actor_id=r.get("corrected_by") or r.get("created_by"), original_model=r.get("original_actual_model"), original_barcode=r.get("original_asset_barcodes"))
            fields.append(row)
    resources, fields, inventory_pairs, outgoing_names = _paired_sources(conn, tasks, resources, fields, missing)
    device_names = {(r["location_code"], r["name"]) for r in resources if r["name"]} | outgoing_names
    for asset in assets.values():
        asset["rack_type"] = asset["rack_type"] or known_racks.get((asset["location_code"], asset["name"]), "")
        # 部分网元资产名称也叫“传输综合柜”；同地点的网元名称已对应时仍属于设备。
        is_cabinet = not asset["rack_type"] and (asset["name"].startswith("设备机架-") or (
            "传输综合柜" in asset["asset_name"] and (asset["location_code"], asset["name"]) not in device_names))
        asset["entity_kind"] = "unlinked" if not asset["name"] else "rack" if asset["rack_type"] else "cabinet" if is_cabinet else "device"
    scoped_assets = list(assets.values())
    unlinked_assets = [a for a in scoped_assets if a["entity_kind"] == "unlinked"]
    racks = rack_comparison(conn, tasks, assets)
    cabinets = equipment_cabinet_comparison(tasks, [r for r in resources if not r.get("inventory_pairing", {}).get("cross_room")], fields, assets)
    stage_one = resource_field_comparison(resources, fields, missing)
    # 网元是业务关联键，记录 ID 保证同名网元不会被静默合并。
    assets_by_name = defaultdict(list)
    for asset in assets.values():
        if asset["entity_kind"] == "device":
            assets_by_name[(asset["location_code"], asset["name"])].append(asset)
    resource_names = defaultdict(list)
    for resource in resources:
        resource_names[(resource["task_id"], resource["name"])].append(resource)
    groups = []
    used_group_keys = set()
    used_assets = set()
    for index, pair in enumerate(stage_one):
        resource, feedback = pair["resource"], pair["feedback"]
        identity = resource or pair["field"]
        key = f"{batch}:net:{identity['task_id']}:{identity.get('entity_id') or identity.get('record_id') or index}"
        if not resource:
            key += ":unmatched-field"
        if key in used_group_keys:
            key += f":duplicate:{index}"
        used_group_keys.add(key)
        match_code = (next(t["room_code"] for t in tasks if t["id"] == resource["task_id"])
                      if resource and resource.get("inventory_pairing", {}).get("cross_room") else resource["location_code"] if resource else "")
        matches = assets_by_name.get((match_code, resource["name"]), []) if resource and resource["name"] else []
        if matches:
            used_assets.update(a["barcode"] for a in matches)
        related = {"资产": matches, "综资": [resource] if resource else [], "现场": feedback}
        pending = {s: [] for s in SYSTEMS}
        for system in ("综资", "现场"):
            pending[system] = [r for r in related[system] if not db.split_codes(r["barcode"])]
            related[system] = [r for r in related[system] if db.split_codes(r["barcode"])]
        manual_mode = any(len(db.split_codes(r["barcode"])) > 1 for s in ("综资", "现场") for r in related[s] + pending[s])
        display_barcode = identity["barcode"] or next((r["barcode"] for r in feedback if r["barcode"]), "") or (matches[0]["barcode"] if len(matches) == 1 else "")
        groups.append(dict(group_key=key, barcode=display_barcode, manual=manual_mode,
                           no_barcode=not any(related.values()), sources=related, pending_sources=pending,
                           pending_association=any(pending.values()), pair=pair,
                           duplicate_resource=bool(resource and len(resource_names[(resource["task_id"], resource["name"])]) > 1),
                           duplicate_asset=len(matches) > 1))
    # 所选机房内资产表独有的资源仍需报管理员；其他机房资产不进入本轮。
    asset_only = defaultdict(list)
    for asset in scoped_assets:
        if asset["entity_kind"] == "device" and asset["barcode"] not in used_assets and (asset["location_code"], asset["name"]) not in outgoing_names:
            asset_only[(asset["location_code"], asset["name"])].append(asset)
    for matches in asset_only.values():
        groups.append(dict(group_key=f"{batch}:asset:{matches[0]['barcode']}", barcode="、".join(a["barcode"] for a in matches), manual=False,
                           no_barcode=False, sources={"资产": matches, "综资": [], "现场": []},
                           pending_sources={s: [] for s in SYSTEMS}, pending_association=False,
                           pair=None, duplicate_resource=False, duplicate_asset=len(matches) > 1))
    task_map = {t["id"]: t for t in tasks}
    decisions = defaultdict(list)
    for row in conn.execute("SELECT d.*,u.name AS actor_name FROM comparison_decisions d LEFT JOIN users u ON u.id=d.actor_id ORDER BY d.id DESC"):
        decisions[row["group_key"]].append(dict(row))
    legacy_owners = defaultdict(int)
    for group in groups:
        if group["barcode"]:
            legacy_owners[f"{batch}:barcode:{group['barcode']}"] += 1
    active_cases = {}
    for row in conn.execute("SELECT id,status,comparison_json FROM ai_adjustment_tasks WHERE comparison_json!='' AND status!='rejected' ORDER BY CASE WHEN status='done' THEN 1 ELSE 0 END,id DESC"):
        meta = json.loads(row["comparison_json"])
        for linked in current_case_groups({"groups": groups}, meta):
            active_cases.setdefault(linked["group_key"], dict(id=row["id"], status=row["status"], fingerprint=meta["snapshot"]["fingerprint"]))
    changes = {(r["entity"], r["kind"]): r["generation"] for r in conn.execute("SELECT entity,kind,MAX(id) AS generation FROM comparison_source_changes GROUP BY entity,kind")}
    for g in groups:
        rows = g["sources"]
        all_rows = {s: source_records(g, s) for s in SYSTEMS}
        g["inventory_pairing"] = next((r["inventory_pairing"] for s in SYSTEMS for r in all_rows[s] if r.get("inventory_pairing")), None)
        entities = [source_entity(s, r) for s in SYSTEMS for r in all_rows[s]]
        full_generations = {entity: changes.get((entity, "full"), 0) for entity in entities}
        model_generations = {entity: changes.get((entity, "model"), 0) for entity in entities}
        comparison_rows = {s: [{k:r[k] for k in ("system","barcode","name","location_code","model","rack_no","task_id","record_id","room_name","entity_id") if k in r} for r in all_rows[s]] for s in SYSTEMS}
        g["fingerprint"] = fingerprint(dict(rows=comparison_rows, generations=full_generations, **({"pending": {s: bool(g["pending_sources"][s]) for s in SYSTEMS}} if g["pending_association"] else {})))
        g["model_fingerprint"] = fingerprint(dict(models={s: [r["model"] for r in all_rows[s]] for s in SYSTEMS}, generations=model_generations))
        for pair_key, pair_systems in (("field", ("综资", "现场")), ("asset", ("综资", "资产"))):
            pair_entities = {source_entity(s, r) for s in pair_systems for r in all_rows[s]}
            g[f"{pair_key}_model_fingerprint"] = fingerprint(dict(
                models={s: [r["model"] for r in all_rows[s]] for s in pair_systems},
                generations={entity: model_generations[entity] for entity in pair_entities}))
        g["task_ids"] = sorted({r["task_id"] for s in SYSTEMS for r in all_rows[s] if r.get("task_id")}) or [t["id"] for t in tasks]
        g["scope_task_ids"] = [t["id"] for t in tasks]
        owner = next((t for t in tasks if rows["资产"] and t["room_code"] == rows["资产"][0]["location_code"]), None) if not any(rows[s] for s in ("现场", "综资")) else None
        owner = owner or (task_map[g["task_ids"][0]] if g["task_ids"] else None)
        if owner and not any(rows[s] for s in ("现场", "综资")):
            g["task_ids"] = [owner["id"]]
        g["room_name"] = "、".join(sorted({r["room_name"] for s in SYSTEMS for r in all_rows[s] if r.get("room_name")})) or (owner["room_name"] if owner else "")
        g["area_name"] = owner["area_name"] if owner else db.USER_AREA_CORE
        g["device_name"] = "、".join(sorted({r["name"] for s in SYSTEMS for r in all_rows[s] if r["name"]}))
        pair = g.get("pair")
        g["stage_one_status"] = pair["status"] if pair else "无综资或现场记录"
        g["versions"] = dict(asset_batch_id=asset_status["id"] if asset_status else None, resources=revisions,
                             field={str(t["id"]): dict(completed_at=t.get("field_completed_at") or t.get("completed_at"), feedback_versions={str(r["record_id"]): r.get("revision_id") or r.get("updated_at") for r in fields if r["task_id"]==t["id"]}) for t in tasks})
        legacy_key = f"{batch}:barcode:{g['barcode']}"
        historical = list(decisions[g["group_key"]])
        if legacy_owners[legacy_key] == 1:
            historical.extend(decisions[legacy_key])
        if g["manual"] and pair:
            identity = pair["resource"] or pair["field"]
            manual_key = f"{batch}:manual:{identity['task_id']}:{identity.get('entity_id') or identity.get('record_id')}"
            historical.extend(decisions[manual_key])
        g["history"] = [{**d, "decision": json.loads(d["decision_json"])} for d in sorted(historical, key=lambda d: d["id"], reverse=True)]
        g["case"] = active_cases.get(g["group_key"])
        g["problems"] = []
        g["model_status"] = "待判断"
        g["field_model_status"] = "待判断"
        g["asset_model_status"] = "待判断"
        g["manual_decision"] = None
        resource = all_rows["综资"][0] if len(all_rows["综资"]) == 1 else None
        field = all_rows["现场"][0] if len(all_rows["现场"]) == 1 else None
        asset = rows["资产"][0] if len(rows["资产"]) == 1 else None
        g["name_status"] = pair_value_status(resource, asset, "name")
        g["location_status"] = pair_value_status(resource, asset, "location_code")
        g["name_detail"] = f"综资：{resource['name'] if resource else '缺失或同名多条'}；资产：{asset['name'] if asset else '缺失或同名多条'}"
        g["location_detail"] = f"综资：{resource['location_code'] if resource else '缺失'}；资产：{asset['location_code'] if asset else '缺失'}"
        g["checks"] = {
            "综资现场": {
                "资产条码": pair_value_status(resource, field, "barcode"),
                "机架号": pair_value_status(resource, field, "rack_no"),
                "设备型号": "待判断",
            },
            "综资资产": {
                "资源名称": g["name_status"],
                "资产条码": pair_value_status(resource, asset, "barcode"),
                "规格型号": "待判断",
                "地点编码": g["location_status"],
            },
            "现场资产": {
                "资产条码": pair_value_status(field, asset, "barcode"),
                "地点编码": pair_value_status(field, asset, "location_code"),
            },
        }
        # 两处型号各自人工判断；地点的现场值来自其所属综资清单。
        if g["manual"]:
            for values in g["checks"].values():
                for label in values:
                    values[label] = "待判断"
        if g["manual"]:
            current = next((d for d in decisions[g["group_key"]] if d["kind"] == "manual" and d["source_fingerprint"] == g["fingerprint"]), None)
            if current:
                g["manual_decision"] = json.loads(current["decision_json"])
                for pair_name, values in g["checks"].items():
                    for label in values:
                        values[label] = g["manual_decision"].get("pair_statuses", {}).get(pair_name, {}).get(label, "待判断")
                g["name_status"] = g["checks"]["综资资产"]["资源名称"]
                g["location_status"] = "一致" if g["checks"]["综资资产"]["地点编码"] == g["checks"]["现场资产"]["地点编码"] == "一致" else "待判断"
                g["field_model_status"] = g["checks"]["综资现场"]["设备型号"]
                g["asset_model_status"] = g["checks"]["综资资产"]["规格型号"]
                g["model_status"] = "一致" if g["field_model_status"] == g["asset_model_status"] == "一致" else "不一致" if "不一致" in (g["field_model_status"], g["asset_model_status"]) else "无法确认" if "无法确认" in (g["field_model_status"], g["asset_model_status"]) else "待判断"
            else:
                g["name_status"] = g["location_status"] = "待判断"
            if not current or any(status != "一致" for values in g["checks"].values() for status in values.values()):
                g["problems"].append("多条码设备待人工核对")
        else:
            if g["duplicate_resource"] or g["duplicate_asset"]:
                g["problems"].append("同名多条，待管理员关联")
            if not all_rows["综资"]:
                g["problems"].append("资产表资源名称在综资中未找到对应网元")
            if resource and not rows["资产"]:
                g["problems"].append("资产表未找到对应资源名称")
            if resource and not all_rows["现场"]:
                g["problems"].append("综资与现场网元名称关联冲突，待管理员处理" if pair and pair["status"] == "网元名称与设备记录归属冲突" else "现场未找到对应网元反馈")
            if len(all_rows["现场"]) > 1:
                g["problems"].append("同一网元多条现场反馈，待管理员关联")
            for s in ("综资", "现场"):
                if g["pending_sources"][s]:
                    state = "登记为无资产条码" if any(r.get("barcode_state") == "现场无条码" for r in g["pending_sources"][s]) else "资产条码未填写"
                    g["problems"].append(f"{s}已有记录，但{state}，待管理员补录")
            for pair_name, checks in g["checks"].items():
                for label, status in checks.items():
                    if label in ("设备型号", "规格型号"):
                        continue
                    if status != "一致" and not (pair_name == "综资资产" and label == "资源名称" and not asset):
                        g["problems"].append(f"{pair_name}{label}{status}：{label}原值为" +
                            f" {({'综资现场': resource, '综资资产': resource, '现场资产': field}[pair_name] or {}).get('rack_no' if label == '机架号' else 'barcode' if label == '资产条码' else 'location_code' if label == '地点编码' else 'name') or '未填写'} / " +
                            f"{({'综资现场': field, '综资资产': asset, '现场资产': asset}[pair_name] or {}).get('rack_no' if label == '机架号' else 'barcode' if label == '资产条码' else 'location_code' if label == '地点编码' else 'name') or '未填写'}")
            found_field = found_asset = False
            for previous in decisions[g["group_key"]]:
                if previous["kind"] != "model":
                    continue
                saved = json.loads(previous["decision_json"])
                if not found_field and (
                    saved.get("field_fingerprint") == g["field_model_fingerprint"] or
                    "field_fingerprint" not in saved and previous["source_fingerprint"] == g["model_fingerprint"]):
                    g["field_model_status"] = saved.get("field_status", saved.get("status", "待判断"))
                    found_field = True
                if not found_asset and (
                    saved.get("asset_fingerprint") == g["asset_model_fingerprint"] or
                    "asset_fingerprint" not in saved and previous["source_fingerprint"] == g["model_fingerprint"]):
                    g["asset_model_status"] = saved.get("asset_status", saved.get("status", "待判断"))
                    found_asset = True
            if not resource or not field or not resource["model"] or not field["model"]:
                g["field_model_status"] = "待判断"
            if not resource or not asset or not resource["model"] or not asset["model"]:
                g["asset_model_status"] = "待判断"
            g["checks"]["综资现场"]["设备型号"] = g["field_model_status"]
            g["checks"]["综资资产"]["规格型号"] = g["asset_model_status"]
            g["model_status"] = "一致" if g["field_model_status"] == g["asset_model_status"] == "一致" else "不一致" if "不一致" in (g["field_model_status"], g["asset_model_status"]) else "无法确认" if "无法确认" in (g["field_model_status"], g["asset_model_status"]) else "待判断"
            for pair_name, status in (("综资现场设备型号", g["field_model_status"]), ("综资资产规格型号", g["asset_model_status"])):
                if status != "一致":
                    g["problems"].append(f"{pair_name}{status}（管理员判断）")
        if pair:
            pair["model_status"] = g["field_model_status"]
            pair["group_key"] = g["group_key"]
        if g["manual"] and g["pending_association"]:
            g["problems"].append("已有设备反馈缺少资产条码，待补录后关联")
        pairing_meta = g["inventory_pairing"]
        if pairing_meta and pairing_meta["role"] == "stale":
            g["problems"].insert(0, "拼销来源已变化，待重新核实：" + pairing_meta["invalid_reason"])
        elif pairing_meta and pairing_meta["cross_room"]:
            g["problems"].insert(0, f"待跨机房调拨：{pairing_meta['source_room_name']} → {pairing_meta['target_room_name']}，综资及资产仍需办理调拨")
        g["consistent"] = not warnings and not g["problems"]
        g["can_create_adjustment"] = not warnings and any(("不一致" in p or "未找到" in p or "缺失" in p or "无资产条码" in p) and "待判断" not in p for p in g["problems"])
        if g["manual"] and g["manual_decision"]:
            g["can_create_adjustment"] = not warnings and any(status == "不一致" for values in g["checks"].values() for status in values.values())
        if pairing_meta:
            g["can_create_adjustment"] = False
    return dict(tasks=tasks, stage_one=stage_one, groups=groups, issues=groups, warnings=warnings, asset_version=asset_status,
                rack_comparison=racks, cabinet_comparison=cabinets, unlinked_assets=unlinked_assets, scoped_assets=scoped_assets,
                inventory_pairings=inventory_pairs,
                asset_counts=dict(total=len(scoped_assets),
                                  named=sum(bool(a["name"]) for a in scoped_assets),
                                  resource_names=len({(a["location_code"], a["name"]) for a in scoped_assets if a["name"]}),
                                  device=sum(a["entity_kind"] in ("device", "cabinet") for a in scoped_assets),
                                  cabinet=sum(a["entity_kind"] == "cabinet" for a in scoped_assets),
                                  rack=sum(a["entity_kind"] == "rack" for a in scoped_assets),
                                  unlinked=len(unlinked_assets)),
                summary=dict(batch_complete=bool(tasks) and all(t["status"] == "done" for t in tasks) and not any(r["id"] not in {t["id"] for t in tasks} or r["status"] != "done" for r in conn.execute("SELECT id,status FROM tasks WHERE import_batch_id=? AND COALESCE(is_deleted,0)=0", (batch,))), task_count=len(tasks), device_count=len(groups), matched_count=sum(g["consistent"] for g in groups),
                             issue_count=sum(not g["consistent"] for g in groups), pending_model_count=sum(g["model_status"] == "待判断" for g in groups)))


def _read_scope(conn, task_ids, completed_only=False):
    """允许跨批次选择机房，各批独立核对后汇总，保留原判断及工单关联。"""
    tasks = [dict(row) for row in conn.execute("SELECT * FROM tasks WHERE COALESCE(is_deleted,0)=0 ORDER BY id DESC")]
    if task_ids:
        ids = set(task_ids)
        selected = [task for task in tasks if task["id"] in ids]
        if len(selected) != len(ids):
            raise ValueError("选中的任务不存在或已作废")
    else:
        latest = {}
        completed = [task for task in tasks if task["status"] == "done"]
        completed.sort(key=lambda task: (text(task.get("field_completed_at") or task.get("completed_at") or task.get("created_at")).replace(" ", "T"), task["id"]), reverse=True)
        for task in completed:
            latest.setdefault(task["room_name"], task)
        selected = list(latest.values())
    if completed_only and any(task["status"] != "done" for task in selected):
        raise ValueError("仅现场人工确认完成的机房可以进行三方比对")
    if len({task["room_name"] for task in selected}) != len(selected):
        raise ValueError("同一机房不能同时选择多次盘点记录，请保留需要核对的一次记录")
    if not selected:
        return build(conn, [])
    batches = defaultdict(list)
    for task in selected:
        batches[task["import_batch_id"]].append(task["id"])
    parts = [build(conn, ids) for ids in batches.values()]
    codes = {}
    for part in parts:
        for task in part["tasks"]:
            code = text(task["room_code"])
            if code and code in codes:
                raise ValueError(f"所选机房地点编码 {code} 重复，请核实机房与地点编码的对应关系")
            if code:
                codes[code] = task["id"]
    if len(parts) == 1:
        return parts[0]
    result = {key: [row for part in parts for row in part[key]]
              for key in ("tasks", "stage_one", "groups", "rack_comparison", "cabinet_comparison", "unlinked_assets", "scoped_assets")}
    result["issues"] = result["groups"]
    result["inventory_pairings"] = list({pair["id"]: pair for part in parts for pair in part["inventory_pairings"]}.values())
    result["warnings"] = list(dict.fromkeys(warning for part in parts for warning in part["warnings"]))
    result["asset_version"] = parts[0]["asset_version"]
    assets = result["scoped_assets"]
    result["asset_counts"] = dict(total=len(assets), named=sum(bool(asset["name"]) for asset in assets),
                                resource_names=len({(asset["location_code"], asset["name"]) for asset in assets if asset["name"]}),
                                device=sum(asset["entity_kind"] in ("device", "cabinet") for asset in assets),
                                cabinet=sum(asset["entity_kind"] == "cabinet" for asset in assets),
                                rack=sum(asset["entity_kind"] == "rack" for asset in assets), unlinked=len(result["unlinked_assets"]))
    groups = result["groups"]
    result["summary"] = dict(batch_complete=all(part["summary"]["batch_complete"] for part in parts),
                             task_count=len(result["tasks"]), device_count=len(groups), matched_count=sum(group["consistent"] for group in groups),
                             issue_count=sum(not group["consistent"] for group in groups), pending_model_count=sum(group["model_status"] == "待判断" for group in groups))
    return result


def read(task_ids):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN")
        return _read_scope(conn, task_ids)
    finally:
        conn.close()


def read_completed(task_ids):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN")
        return _read_scope(conn, task_ids, completed_only=True)
    finally:
        conn.close()


def find_group(conn, task_ids, group_key, expected=None):
    result = build(conn, task_ids)
    if result["warnings"]:
        raise ValueError("；".join(result["warnings"]))
    group = next((g for g in result["groups"] if g["group_key"] == group_key), None)
    if not group:
        raise ValueError("比对记录已变化，请刷新")
    if expected and expected != group["fingerprint"]:
        raise ValueError("原始数据已更新，请刷新后重新判断")
    return group


def save_decision(task_ids, group_key, expected, kind, decision, actor):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        g = find_group(conn, task_ids, group_key, expected)
        if g["pending_association"]:
            raise ValueError("已有设备反馈缺少资产条码，请先补录并完成关联后再确认三方判断")
        if kind == "model":
            if g["manual"]:
                raise ValueError("多条码设备请填写完整人工比对")
            if any(decision.get(f) not in STATES for f in ("field_status", "asset_status")):
                raise ValueError("请分别判断综资与现场、综资与资产的型号")
            for status_field, systems in (("field_status", ("综资", "现场")), ("asset_status", ("综资", "资产"))):
                if decision[status_field] == "一致" and any(len(source_records(g, s)) != 1 or not source_records(g, s)[0]["model"] for s in systems):
                    raise ValueError("对应两方型号缺失或重复，不能判断为一致")
            decision["field_fingerprint"] = g["field_model_fingerprint"]
            decision["asset_fingerprint"] = g["asset_model_fingerprint"]
            source_fp = g["model_fingerprint"]
        elif kind == "manual":
            if not g["manual"]:
                raise ValueError("仅多条码设备使用完整人工比对")
            pair_statuses = decision.get("pair_statuses", {})
            if set(pair_statuses) != set(g["checks"]) or any(
                set(pair_statuses[pair]) != set(labels) or any(status not in STATES for status in pair_statuses[pair].values())
                for pair, labels in g["checks"].items()):
                raise ValueError("请完整填写三组逐项人工判断")
            values = decision.get("values", {})
            for system in SYSTEMS:
                row = values.get(system, {})
                if any(not text(row.get(f)) for f in ("barcode", "name", "location_code", "model")):
                    raise ValueError("请完整填写三个系统的条码、名称、地点编码和型号原值")
            if all(status == "一致" for labels in pair_statuses.values() for status in labels.values()) and any(not source_records(g, system) for system in SYSTEMS):
                raise ValueError("原始三方数据不完整，请先在原系统补齐后更新数据")
            source_fp = g["fingerprint"]
        else:
            raise ValueError("判断类型无效")
        if not text(decision.get("note")):
            raise ValueError("请填写判断依据")
        conn.execute("INSERT INTO comparison_decisions(group_key,source_fingerprint,kind,decision_json,actor_id,created_at) VALUES(?,?,?,?,?,?)",
                     (group_key, source_fp, kind, encode(decision), actor, datetime.now().isoformat()))
        event(conn, "comparison.decision", actor, dict(group_key=group_key, fingerprint=source_fp, decision=decision))
        conn.commit()
        return read(task_ids)
    finally:
        conn.close()


def validate_room_codes(conn, room_codes):
    known = {r["room_name"]: text(r["room_code"]) for r in conn.execute("SELECT room_name,room_code FROM tasks WHERE COALESCE(is_deleted,0)=0 AND room_code!=''")}
    for name, code in room_codes.items():
        code = text(code)
        if not code:
            raise ValueError(f"{name}的地点编码不能为空")
        if name in known and known[name] != code:
            raise ValueError(f"{name}已有地点编码 {known[name]}，机房与地点编码必须一一对应")
        if any(n != name and c == code for n, c in {**known, **room_codes}.items()):
            raise ValueError(f"地点编码 {code} 对应多个机房，请检查")


def set_room_code(task_id, code, actor):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        t = conn.execute("SELECT * FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (task_id,)).fetchone()
        if not t:
            raise ValueError("任务不存在")
        if t["room_code"]:
            raise ValueError("已设置地点编码，请更新综资基线处理数据错误，不能覆盖历史现场归属")
        validate_room_codes(conn, {t["room_name"]: text(code)})
        conn.execute("UPDATE tasks SET room_code=? WHERE room_name=? AND room_code=''", (text(code), t["room_name"]))
        event(conn, "task.location.fill", actor, dict(task_id=task_id, room_name=t["room_name"], location_code=text(code)))
        conn.commit()
    finally:
        conn.close()


def replace_baseline(task_id, devices, actor):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (task_id,)).fetchone()
        if not task or task["status"] != "done":
            raise ValueError("仅现场已完成的任务可更新综资基线")
        if not devices or any(not text(d.get("room_code")) for d in devices):
            raise ValueError("综资更新清单不能为空，且地点编码必填")
        originals = [dict(r) for r in conn.execute("SELECT * FROM scan_records WHERE task_id=? AND record_type!='surplus'", (task_id,))]
        prior = conn.execute("SELECT rows_json FROM resource_baselines WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        prior_rows = json.loads(prior["rows_json"]) if prior else []
        rows = []
        names_in_upload = defaultdict(int)
        for device in devices:
            names_in_upload[text(device["device_name"])] += 1
        candidate_ids = []
        for d in devices:
            name_matches = [r for r in originals if r["device_name"] == d["device_name"]]
            if len(name_matches) == 1 and names_in_upload[text(d["device_name"])] == 1:
                matches = name_matches
            elif name_matches:
                matches = []
            else:
                matches = [r for r in originals if r["expected_barcode"] and db.split_codes(r["expected_barcode"]) == db.split_codes(d["expected_barcode"])]
            candidate_ids.append(matches[0]["id"] if len(matches) == 1 else None)
        claims = defaultdict(int)
        for candidate in candidate_ids:
            if candidate is not None:
                claims[candidate] += 1
        for d, candidate in zip(devices, candidate_ids):
            record_id = candidate if candidate is not None and claims[candidate] == 1 else None
            row = source_row("综资", d["expected_barcode"], d["device_name"], d["room_code"], d["expected_model"], task, record_id, rack_from_extra(d.get("device_extra")))
            if record_id is None:
                previous = [r for r in prior_rows if r["name"] == d["device_name"] or r["barcode"] and db.split_codes(r["barcode"]) == db.split_codes(d["expected_barcode"])]
                row["entity_id"] = previous[0].get("entity_id") if len(previous) == 1 and previous[0].get("entity_id") else uuid4().hex
            rows.append(row)
        old_baseline = conn.execute("SELECT rows_json FROM resource_baselines WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        old_rows = json.loads(old_baseline["rows_json"]) if old_baseline else [source_row("综资", r["expected_barcode"], r["device_name"], task["room_code"], r["expected_model"], task, r["id"], rack_from_extra(r.get("device_extra"))) for r in originals]
        track_sources(conn, resource_values(old_rows), resource_values(rows))
        cur = conn.execute("INSERT INTO resource_baselines(task_id,rows_json,actor_id,created_at) VALUES(?,?,?,?)",
                           (task_id, encode(rows), actor, datetime.now().isoformat()))
        event(conn, "resources.baseline.update", actor, dict(task_id=task_id, revision_id=cur.lastrowid, rows=rows))
        conn.commit()
    finally:
        conn.close()


def _resource_record_context(conn, record_id):
    record = conn.execute("SELECT * FROM scan_records WHERE id=? AND is_void=0", (record_id,)).fetchone()
    if not record:
        raise ValueError("设备记录不存在或已作废")
    record = dict(record)
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (record["task_id"],)).fetchone()
    if not task:
        raise ValueError("任务不存在或已作废")
    baseline = conn.execute("SELECT id,rows_json FROM resource_baselines WHERE task_id=? ORDER BY id DESC LIMIT 1", (task["id"],)).fetchone()
    rows = json.loads(baseline["rows_json"]) if baseline else [
        source_row("综资", r["expected_barcode"], r["device_name"], task["room_code"], r["expected_model"], task, r["id"], rack_from_extra(r["device_extra"]))
        for r in conn.execute("SELECT * FROM scan_records WHERE task_id=? AND is_void=0 AND record_type!='surplus' ORDER BY id", (task["id"],))]
    indexes = [index for index, row in enumerate(rows) if row.get("record_id") == record_id]
    current = rows[indexes[0]] if len(indexes) == 1 else None
    reason = ""
    if task["status"] != "done":
        reason = "请待现场完成本任务后修改综资数据"
    elif record["record_type"] == "surplus":
        reason = "现场新增设备没有对应综资记录，请通过完整综资清单更新"
    elif not indexes:
        reason = "最新综资基线未关联此设备，请通过完整综资清单更新"
    elif len(indexes) != 1:
        reason = "最新综资基线存在重复关联，请先核实完整综资清单"
    current = current or {}
    view = dict(record_id=record_id, task_id=task["id"], room_name=task["room_name"],
                device_name=current.get("name") or record["device_name"],
                expected_model=text(current.get("model")), expected_barcode=text(current.get("barcode")),
                expected_rack_no=text(current.get("rack_no")), editable=not reason, reason=reason,
                fingerprint=fingerprint(dict(record_id=record_id, revision_id=baseline["id"] if baseline else 0, row=current)))
    return view, rows, indexes


def resource_record(record_id):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN")
        return _resource_record_context(conn, record_id)[0]
    finally:
        conn.close()


def update_resource_record(record_id, values, expected, actor, note=""):
    fields = {"expected_model": ("model", 200), "expected_barcode": ("barcode", 2000), "expected_rack_no": ("rack_no", 200)}
    normalized = {target: text(values.get(field)) for field, (target, _) in fields.items()}
    if any(len(normalized[target]) > limit for target, limit in fields.values()) or len(text(note)) > 500:
        raise ValueError("综资修改内容超出允许长度")
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current, rows, indexes = _resource_record_context(conn, record_id)
        if not current["editable"]:
            raise ValueError(current["reason"])
        if not expected or expected != current["fingerprint"]:
            raise ValueError("综资基线已更新，请重新打开修改窗口后再保存")
        index = indexes[0]
        before = rows[index]
        after = {**before, **normalized}
        if after != before:
            updated = [after if i == index else row for i, row in enumerate(rows)]
            track_sources(conn, resource_values(rows), resource_values(updated))
            revision = conn.execute("INSERT INTO resource_baselines(task_id,rows_json,actor_id,created_at) VALUES(?,?,?,?)",
                                    (current["task_id"], encode(updated), actor, datetime.now().isoformat())).lastrowid
            event(conn, "resources.record.update", actor, dict(task_id=current["task_id"], record_id=record_id,
                  revision_id=revision, before=before, after=after, note=text(note)))
        conn.commit()
        return dict(success=True, message="综资数据已更新", record_id=record_id, task_id=current["task_id"])
    finally:
        conn.close()


def create_case(task_ids, group_key, expected, actor, confirmation=None):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        g = find_group(conn, task_ids, group_key, expected)
        if g.get("inventory_pairing"):
            raise ValueError("该设备已有拼销记录，请先撤销拼销并核实，再单独转整改")
        if g.get("case") and g["case"]["status"] != "done":
            raise ValueError(f"本条资产已有整改单 #{g['case']['id']}，请继续处理现有工单")
        if not g["can_create_adjustment"] and not confirmation:
            raise ValueError("请先完成人工判断并确认实际问题，再建单")
        if confirmation:
            g["problems"].append(f"人工确认：{confirmation['problem_type']}；依据：{confirmation['basis']}；要求：{confirmation['suggestion']}")
            g["consistent"] = False
        for row in conn.execute("SELECT id,status,comparison_json FROM ai_adjustment_tasks WHERE comparison_json!='' AND status!='rejected'"):
            m = json.loads(row["comparison_json"])
            if m["group_key"] == group_key and row["status"] != "done":
                raise ValueError(f"本条资产已有整改单 #{row['id']}，请继续处理现有工单")
        meta = dict(group_key=group_key, task_ids=task_ids, snapshot=g, needs_field=None, needs_admin=None,
                    field_done=False, admin_done=False, admin_note="")
        cur = conn.execute("""INSERT INTO ai_adjustment_tasks(source_type,source_key,source_task_ids_json,problem_type,room_name,
              device_name,area_name,risk_level,basis,suggestion,priority,created_by,created_at,comparison_json)
              VALUES(?,?,?,?,?,?,?, '中',?, '管理员审核责任后处理，更新数据并重新比对验收', 'P2',?,?,?)""",
            ("manual" if confirmation else "rule", group_key, encode(task_ids), "；".join(g["problems"]), g["room_name"], g["device_name"], g["area_name"],
             "；".join(g["problems"]), actor, datetime.now().isoformat(), encode(meta)))
        event(conn, "case.create", actor, g, cur.lastrowid)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def case_detail(case_id):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT a.*,u.name AS assigned_name FROM ai_adjustment_tasks a LEFT JOIN users u ON u.id=a.assigned_to WHERE a.id=?", (case_id,)).fetchone()
        if not row:
            raise ValueError("整改单不存在")
        item = db._adjustment_from_row(row)
        meta = item["comparison"]
        item["history"] = [{**dict(e), "data": json.loads(e["data_json"])} for e in conn.execute("SELECT e.*,u.name AS actor_name FROM comparison_events e LEFT JOIN users u ON u.id=e.actor_id WHERE case_id=? ORDER BY e.id", (case_id,))]
        if meta:
            try:
                result = build(conn, meta["task_ids"])
                linked = current_case_groups(result, meta)
                item["current"] = linked[0] if linked else None
                item["current_groups"] = linked
                item["warnings"] = result["warnings"]
            except ValueError as exc:
                item.update(current=None, current_groups=[], warnings=[str(exc)])
        return item
    finally:
        conn.close()


def case_action(case_id, action, actor, payload):
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM ai_adjustment_tasks WHERE id=?", (case_id,)).fetchone()
        if not row or not row["comparison_json"]:
            raise ValueError("仅三方比对工单使用此操作，历史工单请单独复核")
        meta = json.loads(row["comparison_json"])
        status = row["status"]
        note = text(payload.get("note"))
        now = datetime.now().isoformat()
        if not note:
            raise ValueError("请填写本次操作说明")
        if action == "approve":
            if status != "pending_review":
                raise ValueError("仅待审核工单可审核")
            nf, na = bool(payload.get("needs_field")), bool(payload.get("needs_admin"))
            if not (nf or na):
                raise ValueError("请确认现场或管理员处理责任")
            assigned = payload.get("assigned_to") if nf else None
            if nf:
                worker = conn.execute("SELECT * FROM users WHERE id=? AND role='worker' AND is_active=1 AND COALESCE(is_deleted,0)=0", (assigned,)).fetchone()
                if not worker or not db.user_can_access_area(worker["area_name"], row["area_name"]):
                    raise ValueError("请选择本片区有效现场人员")
                field_task_ids = {r["task_id"] for system in SYSTEMS for r in source_records(meta["snapshot"], system) if r.get("record_id")}
                for task_id in field_task_ids:
                    task_area = conn.execute("SELECT area_name FROM tasks WHERE id=?", (task_id,)).fetchone()
                    if task_area and not db.user_can_access_area(worker["area_name"], task_area["area_name"]):
                        raise ValueError("该问题涉及多个片区，请先由管理员核实重复关联，不能跨片区派给同一现场人员")
            meta.update(needs_field=nf, needs_admin=na, field_done=not nf, admin_done=not na)
            conn.execute("UPDATE ai_adjustment_tasks SET status='dispatched',assigned_to=?,reviewed_by=?,review_note=?,reviewed_at=?,dispatched_at=? WHERE id=?",
                         (assigned, actor, note, now, now, case_id))
        elif action == "admin_complete":
            if status not in {"dispatched", "pending_verify"} or not meta["needs_admin"] or meta["admin_done"]:
                raise ValueError("该单当前不需要管理员提交处理结果")
            meta.update(admin_done=True, admin_note=note)
            if meta["field_done"]:
                conn.execute("UPDATE ai_adjustment_tasks SET status='pending_verify',completed_at=? WHERE id=?", (now, case_id))
        elif action == "field_complete":
            if status != "dispatched" or row["assigned_to"] != actor or not meta["needs_field"] or meta["field_done"]:
                raise ValueError("只能提交下发给自己的未完成现场整改")
            worker = conn.execute("SELECT * FROM users WHERE id=? AND role='worker' AND is_active=1 AND COALESCE(is_deleted,0)=0", (actor,)).fetchone()
            if not worker or worker["area_name"] != row["area_name"]:
                raise ValueError("现场账号已停用或片区已变化，请管理员重新核实处理责任")
            allowed = {r["record_id"] for s in SYSTEMS for r in source_records(meta["snapshot"], s) if r.get("record_id")} | set(meta.get("field_record_ids", []))
            feedback = payload.get("new_record")
            if feedback:
                if allowed:
                    raise ValueError("本单已有设备记录，请修改该设备反馈，不能重复新增")
                target = conn.execute("SELECT * FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (feedback.get("task_id"),)).fetchone()
                if not target or target["id"] not in meta["snapshot"]["task_ids"] or target["area_name"] != row["area_name"]:
                    raise ValueError("新增反馈必须属于本单对应机房和片区")
                for field in ("device_name", "actual_model", "asset_barcodes", "rack_no"):
                    if not text(feedback.get(field)):
                        raise ValueError("新增现场反馈必须填写设备名称、实际型号、资产条码和机架号")
                if not db.split_codes(feedback["asset_barcodes"]):
                    raise ValueError("实际资产条码不能为空")
                if not feedback.get("cabinet_no_barcode") and not db.split_codes(text(feedback.get("cabinet_barcodes"))):
                    raise ValueError("请填写机柜条码或选择无机柜条码")
                entity_id = next((r.get("entity_id") for r in meta["snapshot"]["sources"]["综资"] if r.get("entity_id")), None)
                extra = dict(comparison_case_id=case_id, comparison_entity=entity_id)
                cur = conn.execute("""INSERT INTO scan_records(task_id,device_name,device_extra,expected_model,expected_barcode,
                    actual_model,asset_barcodes,cabinet_barcodes,cabinet_no_barcode,rack_no,notes,record_type,field_status,match_status,scanned_at,created_by)
                    VALUES(?,?,?,'','',?,?,?,?,?,?,'surplus','现场新增','不一致',?,?)""",
                    (target["id"], text(feedback["device_name"]), encode(extra), text(feedback["actual_model"]),
                     ",".join(sorted(db.split_codes(feedback["asset_barcodes"]))), text(feedback.get("cabinet_barcodes")) if not feedback.get("cabinet_no_barcode") else "",
                     int(bool(feedback.get("cabinet_no_barcode"))), text(feedback["rack_no"]), note, now, actor))
                meta["field_record_ids"] = [cur.lastrowid]
            for_update_allowed = allowed

            for update in payload.get("records", []):
                record_id = update.get("record_id")
                if record_id not in for_update_allowed:
                    raise ValueError("只能修改本整改单涉及的设备反馈")
                raw = conn.execute("SELECT * FROM scan_records WHERE id=?", (record_id,)).fetchone()
                effective = db.apply_latest_record_correction(conn, dict(raw))
                values = {f: effective.get(f) for f in db.CORRECTABLE_RECORD_FIELDS}
                for field in ("actual_model", "asset_barcodes", "record_type", "missing_reason", "cabinet_barcodes", "cabinet_no_barcode", "rack_no"):
                    if field in update and update[field] is not None:
                        values[field] = update[field]
                if update.get("asset_barcodes"):
                    values["asset_no_barcode"] = False
                    if values["record_type"] == "missing":
                        values["record_type"] = "normal"
                db.create_record_correction(record_id, values, "现场整改反馈", note, actor, connection=conn)
            meta["field_done"] = True
            conn.execute("UPDATE ai_adjustment_tasks SET status=?,completed_by=?,completion_note=?,completed_at=? WHERE id=?",
                         ("pending_verify" if meta["admin_done"] else "dispatched", actor, note, now, case_id))
        elif action == "reject_review":
            if status != "pending_review":
                raise ValueError("仅待审核工单可驳回")
            conn.execute("UPDATE ai_adjustment_tasks SET status='rejected',reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?", (actor, note, now, case_id))
        elif action == "return":
            if status != "pending_verify":
                raise ValueError("仅待验收工单可退回整改")
            return_field = bool(payload.get("return_field")) and meta["needs_field"]
            return_admin = bool(payload.get("return_admin")) and meta["needs_admin"]
            if not (return_field or return_admin):
                raise ValueError("请勾选需要继续处理的责任方")
            if return_field:
                meta["field_done"] = False
            if return_admin:
                meta["admin_done"] = False
            conn.execute("UPDATE ai_adjustment_tasks SET status='dispatched',verification_note=?,verified_by=?,verified_at=? WHERE id=?", (note, actor, now, case_id))
        elif action == "verify":
            if status != "pending_verify" or not meta["field_done"] or not meta["admin_done"]:
                raise ValueError("所有责任方提交处理结果后才可验收")
            result = build(conn, meta["task_ids"])
            if result["warnings"]:
                raise ValueError("；".join(result["warnings"]))
            linked = current_case_groups(result, meta)
            if not linked:
                raise ValueError("原比对记录已变化，无法确定整改后的设备关联")
            if not all(g["consistent"] for g in linked):
                raise ValueError("最新三方比对仍有问题：" + "；".join(p for g in linked for p in g["problems"]))
            meta["accepted_snapshot"] = linked[0]
            meta["accepted_snapshots"] = linked
            conn.execute("UPDATE ai_adjustment_tasks SET status='done',verified_by=?,verification_note=?,verified_at=? WHERE id=?", (actor, note, now, case_id))
        else:
            raise ValueError("操作无效")
        conn.execute("UPDATE ai_adjustment_tasks SET comparison_json=? WHERE id=?", (encode(meta), case_id))
        event(conn, f"case.{action}", actor, dict(note=note, payload=payload, comparison=meta), case_id)
        conn.commit()
        return case_detail(case_id)
    finally:
        conn.close()


def is_comparison_case(case_id):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT comparison_json FROM ai_adjustment_tasks WHERE id=?", (case_id,)).fetchone()
        return bool(row and row["comparison_json"])
    finally:
        conn.close()


def agent_rows(result):
    """AI 获得和页面相同的原值、人工结论及问题类别；不授予写入能力。"""
    rows = []
    for g in result["groups"]:
        sources = g["sources"]
        def value(system, field):
            if g["manual"] and g["manual_decision"]:
                return g["manual_decision"]["values"][system][field]
            return "；".join(r[field] for r in source_records(g, system))
        rows.append({
            "任务ID": g["task_ids"][0] if g["task_ids"] else "", "导入批次": g["group_key"].split(":")[0],
            "机房名称": g["room_name"], "片区": g["area_name"], "设备名称": value("综资", "name"),
            "资产资源名称": value("资产", "name"), "综资网元名称": value("综资", "name"), "现场网元名称": value("现场", "name"),
            "资产信息条码": value("资产", "barcode"), "清单资产条码": value("综资", "barcode"), "现场资产条码": value("现场", "barcode"),
            "资产地点编码": value("资产", "location_code"), "综资地点编码": value("综资", "location_code"), "现场地点编码": value("现场", "location_code"),
            "资产规格型号": value("资产", "model"), "清单设备型号": value("综资", "model"), "现场设备型号": value("现场", "model"),
            "综资设备机架号": value("综资", "rack_no"), "现场设备机架号": value("现场", "rack_no"),
            "比对模式": "多条码人工模式B" if g["manual"] else "单条码规则比对",
            "综资现场网元关联与条码核对": g["stage_one_status"],
            "综资现场机架号核对": g["checks"]["综资现场"]["机架号"],
            "综资现场型号人工判断": g["field_model_status"],
            "综资资产条码核对": g["checks"]["综资资产"]["资产条码"],
            "综资资产地点核对": g["checks"]["综资资产"]["地点编码"],
            "综资资产型号人工判断": g["asset_model_status"],
            "现场资产条码核对": g["checks"]["现场资产"]["资产条码"],
            "现场资产地点核对": g["checks"]["现场资产"]["地点编码"],
            "名称比对结论": g["name_status"], "地点比对结论": g["location_status"], "型号人工判断": g["model_status"],
            "资产记录数": len(sources["资产"]), "综资记录数": len(sources["综资"]), "现场记录数": len(sources["现场"]),
            "规则问题类型": "；".join(p.split("：")[0] for p in g["problems"]),
            "条码关联状态": "按所属网元关联，条码待补录" if g["pending_association"] else "按网元/资源名称关联，条码只用于比对",
            "现场反馈数": len(source_records(g, "现场")),
            "现场状态": "已有反馈，条码待补录关联" if g["pending_sources"]["现场"] else "已盘点" if sources["现场"] else "现场条码缺失，待管理员核实", "任务状态": "done",
        })
    return rows


def worker_case(item):
    meta = item.get("comparison")
    if not meta:
        return item
    ids = {r["record_id"] for s in SYSTEMS for r in source_records(meta["snapshot"], s) if r.get("record_id")} | set(meta.get("field_record_ids", []))
    item["field_records"] = [r for task_id in meta["task_ids"] for r in db.get_report(task_id) if r["id"] in ids]
    item["field_target_tasks"] = [dict(id=t["id"], room_name=t["room_name"]) for task_id in meta["snapshot"]["task_ids"] if (t := db.get_task_by_id(task_id)) and t["area_name"] == item["area_name"]]
    # 现场只读取其处理设备、审核说明；不暴露完整资产库或其他系统的审核快照。
    item["comparison"] = {k: meta[k] for k in ("needs_field", "field_done", "needs_admin", "admin_done")}
    for field in ("current", "current_groups", "history", "warnings"):
        item.pop(field, None)
    return item


def current_case_groups(result, meta):
    current = next((g for g in result["groups"] if g["group_key"] == meta["group_key"]), None)
    if current:
        return [current]
    # 更正误扫条码后，原错误条码可能退出本机房范围；以同一现场记录追溯整改对象。
    ids = {r["record_id"] for s in SYSTEMS for r in source_records(meta["snapshot"], s) if r.get("record_id")} | set(meta.get("field_record_ids", []))
    if ids:
        return [g for g in result["groups"] if any(r.get("record_id") in ids for s in SYSTEMS for r in source_records(g, s))]
    asset_codes = {r["barcode"] for r in source_records(meta["snapshot"], "资产")}
    return [g for g in result["groups"] if any(r["barcode"] in asset_codes for r in source_records(g, "资产"))]


def source_entity(system, row):
    if system == "资产":
        return "asset:" + db.normalize_barcode(row["barcode"])
    if system == "现场":
        return "field:" + str(row.get("record_id"))
    return f"resources:{row['task_id']}:{row.get('entity_id') or row.get('record_id') or text(row['name'])}"


def resource_values(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[source_entity("综资", row)].append({k: row.get(k, "") for k in ("barcode", "name", "location_code", "model", "rack_no")})
    return {entity: dict(model=sorted(r["model"] for r in values), rows=values) for entity, values in grouped.items()}


def track_sources(conn, before, after):
    """只记录相关内容变化；恢复旧值也是新变化，不能复活过期人工结论。"""
    for entity in before.keys() | after.keys():
        old, new = before.get(entity), after.get(entity)
        if old == new:
            continue
        kinds = ["full"]
        if (old or {}).get("model") != (new or {}).get("model"):
            kinds.append("model")
        for kind in kinds:
            conn.execute("INSERT INTO comparison_source_changes(entity,kind,created_at) VALUES(?,?,?)", (entity,kind,datetime.now().isoformat()))


def asset_values(conn, batch_id):
    if batch_id is None:
        return {}
    status = conn.execute("SELECT barcode_column FROM asset_info_imports WHERE id=?", (batch_id,)).fetchone()
    result = {}
    for row in conn.execute("SELECT * FROM asset_info_records WHERE batch_id=?", (batch_id,)):
        fields = json.loads(row["fields_json"])
        result["asset:"+row["barcode_norm"]] = dict(barcode=db.normalize_barcode(fields.get(status["barcode_column"])), name=text(fields.get("资源名称")), location_code=text(fields.get("地点编码")), model=text(fields.get("规格型号")))
    return result
