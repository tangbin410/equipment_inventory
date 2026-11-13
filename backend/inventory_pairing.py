"""盘亏盘盈拼销：保存人工确认关系，以派生视图合并，原始盘点记录保持不变。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

from . import database as db


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory_pairs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        missing_record_id INTEGER NOT NULL REFERENCES scan_records(id),
        surplus_record_id INTEGER NOT NULL REFERENCES scan_records(id),
        missing_fingerprint TEXT NOT NULL,
        surplus_fingerprint TEXT NOT NULL,
        note TEXT NOT NULL,
        actor_id INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
        cancelled_by INTEGER REFERENCES users(id),
        cancelled_at TEXT NOT NULL DEFAULT '',
        cancel_note TEXT NOT NULL DEFAULT '',
        CHECK(missing_record_id != surplus_record_id)
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_pairs_missing ON inventory_pairs(missing_record_id) WHERE status='active'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_pairs_surplus ON inventory_pairs(surplus_record_id) WHERE status='active'")


def _extra(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _latest_tasks(conn: sqlite3.Connection) -> set[int]:
    tasks = [dict(row) for row in conn.execute("""SELECT * FROM tasks
        WHERE status='done' AND is_deleted=0 AND COALESCE(superseded_by_task_id,0)=0""")]
    tasks.sort(key=lambda t: ((t.get('field_completed_at') or t.get('completed_at') or t.get('created_at') or '').replace(' ', 'T'), t['id']), reverse=True)
    names, codes, latest = set(), set(), set()
    for task in tasks:
        name, code = str(task.get('room_name') or '').strip(), str(task.get('room_code') or '').strip()
        if name in names or code and code in codes:
            continue
        latest.add(task['id'])
        if name:
            names.add(name)
        if code:
            codes.add(code)
    return latest


def _record(conn: sqlite3.Connection, record_id: int, baseline_cache: dict | None = None) -> dict[str, Any] | None:
    raw = conn.execute('SELECT * FROM scan_records WHERE id=?', (record_id,)).fetchone()
    if raw is None:
        return None
    task = conn.execute("""SELECT t.*,u.name AS assigned_name FROM tasks t
        LEFT JOIN users u ON u.id=t.assigned_to WHERE t.id=?""", (raw['task_id'],)).fetchone()
    if task is None:
        return None
    record = db.add_compare_status(db.apply_resource_baseline(conn, db.apply_latest_record_correction(conn, dict(raw)), baseline_cache))
    if record.get('record_type') != 'surplus' and record.get('resource_device_name'):
        record['device_name'] = record['resource_device_name']
    extra = _extra(record.get('device_extra'))
    record.update(
        room_name=task['room_name'], room_code=task['room_code'], assigned_name=task['assigned_name'] or '',
        task_status=task['status'], task_is_deleted=bool(task['is_deleted']),
        task_superseded_by=task['superseded_by_task_id'],
        device_category=str(extra.get('设备类别') or extra.get('网元类型') or '').strip(),
    )
    keys = ('id', 'task_id', 'device_name', 'device_extra', 'record_type', 'is_void',
            'expected_model', 'expected_barcode', 'expected_rack_no', 'resource_device_name',
            'resource_location_code', 'resource_baseline_state', 'actual_model', 'asset_barcodes',
            'asset_no_barcode', 'cabinet_barcodes', 'cabinet_no_barcode', 'rack_no', 'notes',
            'missing_reason', 'scanned_at', 'latest_correction_id', 'room_name', 'room_code', 'task_status')
    # 改回旧值仍是新资料，不能让已经失效的人工拼销关系自行恢复。
    # 只追踪本设备，避免同机房其他设备更新导致无关配对失效。
    generation = conn.execute("""SELECT COALESCE(MAX(id),0) FROM comparison_source_changes
        WHERE kind='full' AND entity IN (?,?)""",
        (f"resources:{record['task_id']}:{record['id']}", f"field:{record['id']}")).fetchone()[0]
    payload = json.dumps({**{key: record.get(key) for key in keys}, 'source_generation': generation},
                         ensure_ascii=False, sort_keys=True)
    record['fingerprint'] = hashlib.sha256(payload.encode()).hexdigest()
    return record


def _unavailable(record: dict[str, Any] | None, latest: set[int]) -> str:
    if not record or record.get('is_void') or record.get('task_is_deleted'):
        return '盘点记录不存在或已作废'
    if record.get('task_status') != 'done':
        return '仅已完成盘点的机房可以拼销'
    if record.get('task_superseded_by') or record['task_id'] not in latest:
        return '仅每个机房最近一次已完成盘点可以拼销'
    if record.get('record_type') not in ('missing', 'surplus') or not record.get('scanned_at'):
        return '只能对已登记的盘亏和盘盈记录拼销'
    if record.get('record_type') == 'missing' and record.get('resource_baseline_state') in ('missing', 'ambiguous'):
        return '盘亏设备的当前综资记录缺失或关联不唯一，请先核实综资基线'
    return ''


def _admin(conn: sqlite3.Connection, actor_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM users WHERE id=? AND role='admin' AND is_active=1 AND is_deleted=0", (actor_id,)).fetchone()
    if row is None or row['admin_scope'] not in ('full', 'collaborator'):
        raise ValueError('仅有效管理员可以操作拼销')
    return dict(row)


def _note(value: str) -> str:
    value = str(value or '').strip()
    if not value or len(value) > 1000:
        raise ValueError('请填写不超过1000字的确认依据')
    return value


def _device_codes(record: dict[str, Any]) -> set[str]:
    value = record.get('expected_barcode') if record['record_type'] == 'missing' else '' if record.get('asset_no_barcode') else record.get('asset_barcodes')
    placeholders = {'缺失', '无条码', '现场无条码', '未填写', '未导入', '不涉及', '无', '-', '—', 'N/A', 'NULL', 'NONE'}
    return db.split_codes(value or '') - placeholders


def _open_case(conn: sqlite3.Connection, record_ids: set[int]) -> int | None:
    def contains(value: Any) -> bool:
        if isinstance(value, dict):
            record_id = value.get('record_id')
            return (isinstance(record_id, int) and record_id in record_ids) or any(contains(item) for item in value.values())
        return isinstance(value, list) and any(contains(item) for item in value)

    for row in conn.execute("SELECT id,source_type,source_key,comparison_json FROM ai_adjustment_tasks WHERE status NOT IN ('done','rejected')"):
        # 旧规则工单通过 source_key 关联记录，不能因尚无三方快照而漏掉。
        if not row['comparison_json'] and row['source_type'] in ('rule', 'manual'):
            parts = str(row['source_key'] or '').split(':')
            linked = parts[1:3] if parts[0] == 'location' else parts[:1]
            if any(part.isdigit() and int(part) in record_ids for part in linked):
                return row['id']
        try:
            if contains(json.loads(row['comparison_json'])):
                return row['id']
        except (TypeError, ValueError):
            continue
    return None


def get_active_pairs(conn: sqlite3.Connection, record_id: int | None = None) -> list[dict[str, Any]]:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventory_pairs'").fetchone() is None:
        return []
    latest = _latest_tasks(conn)
    result = []
    for raw in conn.execute("""SELECT p.*,u.name AS actor_name FROM inventory_pairs p
        LEFT JOIN users u ON u.id=p.actor_id WHERE p.status='active'
        AND (? IS NULL OR p.missing_record_id=? OR p.surplus_record_id=?) ORDER BY p.id""", (record_id, record_id, record_id)):
        pair = dict(raw)
        missing, surplus = _record(conn, pair['missing_record_id']), _record(conn, pair['surplus_record_id'])
        reason = _unavailable(missing, latest) or _unavailable(surplus, latest)
        if not reason and (missing['record_type'] != 'missing' or surplus['record_type'] != 'surplus'
                           or missing['fingerprint'] != pair['missing_fingerprint']
                           or surplus['fingerprint'] != pair['surplus_fingerprint']):
            reason = '拼销依据中的来源数据已变化，请撤销后重新核实'
        pair.update(missing=missing, surplus=surplus, valid=not reason, invalid_reason=reason,
                    cross_room=bool(missing and surplus and missing['task_id'] != surplus['task_id']))
        result.append(pair)
    return result


def _metadata(pair: dict[str, Any], record_id: int | None = None) -> dict[str, Any]:
    missing, surplus = pair.get('missing') or {}, pair.get('surplus') or {}
    role = ('stale' if not pair['valid'] else 'outbound' if pair['cross_room'] and record_id == pair['missing_record_id'] else 'merged')
    return dict(
        id=pair['id'], role=role, cross_room=pair['cross_room'],
        missing_record_id=pair['missing_record_id'], surplus_record_id=pair['surplus_record_id'],
        source_room_name=missing.get('room_name', ''), source_room_code=missing.get('room_code', ''),
        target_room_name=surplus.get('room_name', ''), target_room_code=surplus.get('room_code', ''),
        status='待重新核实' if not pair['valid'] else '待跨机房调拨' if pair['cross_room'] else '已拼销',
        note=pair['note'], created_at=pair['created_at'], actor_name=pair.get('actor_name') or '',
        invalid_reason=pair.get('invalid_reason') or '', missing=missing, surplus=surplus,
    )


def enrich_record(conn: sqlite3.Connection, record_id: int) -> dict[str, Any] | None:
    record = _record(conn, record_id)
    if record is None:
        return None
    record['pairing'] = None
    reason = _unavailable(record, _latest_tasks(conn))
    for pair in get_active_pairs(conn, record_id):
        if record_id in (pair['missing_record_id'], pair['surplus_record_id']):
            record['pairing'] = _metadata(pair, record_id)
            reason = '该记录已经拼销，可查看原记录或撤销后重新核实'
            break
    record['can_pair'] = not reason
    record['pairing_unavailable_reason'] = reason
    return record


def candidates(record_id: int, keyword: str = '', room_name: str = '', device_category: str = '', same_room: bool = False,
               recommended_only: bool = False, page: int | None = None, page_size: int = 15) -> dict[str, Any]:
    conn = db.get_conn()
    try:
        conn.execute('BEGIN')
        source = enrich_record(conn, record_id)
        if source is None:
            raise ValueError('盘点记录不存在')
        if source.get('pairing'):
            return dict(source=source, candidates=[], pairing=source['pairing'], total=0, page=1,
                        page_size=15 if recommended_only else page_size, recommended_only=recommended_only)
        if not source['can_pair']:
            raise ValueError(source['pairing_unavailable_reason'])
        if page is not None and not recommended_only and not (keyword.strip() or room_name.strip() or device_category.strip() or same_room):
            raise ValueError('请先输入查询条件，再查找拼销候选')
        latest = _latest_tasks(conn)
        paired_ids = {pair[key] for pair in conn.execute("SELECT missing_record_id,surplus_record_id FROM inventory_pairs WHERE status='active'")
                      for key in ('missing_record_id', 'surplus_record_id')}
        opposite = 'surplus' if source['record_type'] == 'missing' else 'missing'
        source_codes = _device_codes(source)
        previews = []
        # 仅取匹配所需字段；完整修正历史、指纹只为当前页设备计算。
        placeholders = ','.join('?' for _ in latest)
        rows = conn.execute(f"""SELECT sr.id,sr.task_id,sr.device_name,sr.expected_barcode,sr.expected_model,sr.device_extra,
              COALESCE(c.asset_barcodes,sr.asset_barcodes) AS asset_barcodes,
              COALESCE(c.asset_no_barcode,sr.asset_no_barcode) AS asset_no_barcode,
              COALESCE(c.actual_model,sr.actual_model) AS actual_model,
              COALESCE(c.record_type,sr.record_type) AS record_type,
              CASE WHEN COALESCE(sr.scanned_at,'')='' AND c.correction_reason='现场整改反馈' THEN c.corrected_at ELSE sr.scanned_at END AS scanned_at,
              t.room_name,t.room_code,t.status AS task_status,t.is_deleted AS task_is_deleted,t.superseded_by_task_id AS task_superseded_by
            FROM scan_records sr JOIN tasks t ON t.id=sr.task_id
            LEFT JOIN scan_record_corrections c ON c.id=(SELECT latest.id FROM scan_record_corrections latest
                WHERE latest.record_id=sr.id AND COALESCE(latest.source_scanned_at,'')=COALESCE(sr.scanned_at,'')
                ORDER BY latest.id DESC LIMIT 1)
            WHERE sr.is_void=0 AND sr.task_id IN ({placeholders})
              AND (?=0 OR sr.task_id=?)
              AND COALESCE(c.record_type,sr.record_type)=?
            ORDER BY sr.id""", (*latest, int(same_room), source['task_id'], opposite)).fetchall() if latest else []
        baseline_cache = {}
        for row in rows:
            if row['id'] in paired_ids or row['id'] == record_id:
                continue
            other = db.apply_resource_baseline(conn, dict(row), baseline_cache)
            if other['record_type'] != 'surplus' and other.get('resource_device_name'):
                other['device_name'] = other['resource_device_name']
            extra = _extra(other.get('device_extra'))
            other['device_category'] = str(extra.get('设备类别') or extra.get('网元类型') or '').strip()
            if _unavailable(other, latest):
                continue
            if same_room and other['task_id'] != source['task_id']:
                continue
            if room_name.strip().casefold() not in other['room_name'].casefold():
                continue
            if device_category.strip() and other['device_category'] != device_category.strip():
                continue
            searchable = ' '.join(str(other.get(key) or '') for key in ('device_name', 'asset_barcodes', 'expected_barcode', 'actual_model', 'expected_model'))
            if keyword.strip().casefold() not in searchable.casefold():
                continue
            codes = _device_codes(other)
            common = source_codes & codes
            same_name = bool(source['device_name'].strip()) and other['device_name'].strip().casefold() == source['device_name'].strip().casefold()
            if recommended_only and not (common or same_name):
                continue
            reason = ('设备资产条码一致' if source_codes and source_codes == codes else '部分设备资产条码相同，需核实整组' if common
                      else '网元／资源名称一致，需人工核实' if same_name else '人工核对候选')
            rank = 0 if source_codes and source_codes == codes else 1 if common else 2 if same_name else 3
            previews.append((rank, other['task_id'] != source['task_id'], other['id'], reason))
        previews.sort()
        if recommended_only:
            previews = previews[:15]
            page, page_size = 1, 15
        total = len(previews)
        if page is not None:
            page = min(page, max(1, (total + page_size - 1) // page_size))
            start = (page - 1) * page_size
            previews = previews[start:start + page_size]
        result = []
        for _, _, candidate_id, reason in previews:
            other = _record(conn, candidate_id, baseline_cache)
            other.update(match_reason=reason, can_pair=True, pairing=None, pairing_unavailable_reason='')
            result.append(other)
        return dict(source=source, candidates=result, pairing=None, total=total, page=page or 1,
                    page_size=page_size, recommended_only=recommended_only)
    finally:
        conn.close()


def create_pair(missing_record_id: int, surplus_record_id: int, missing_fingerprint: str, surplus_fingerprint: str, note: str, actor_id: int) -> dict[str, Any]:
    conn = db.get_conn()
    try:
        init_schema(conn)
        conn.execute('BEGIN IMMEDIATE')
        actor = _admin(conn, actor_id)
        note = _note(note)
        if missing_record_id == surplus_record_id:
            raise ValueError('不能将同一条记录与自己拼销')
        missing, surplus = _record(conn, missing_record_id), _record(conn, surplus_record_id)
        latest = _latest_tasks(conn)
        reason = _unavailable(missing, latest) or _unavailable(surplus, latest)
        if reason:
            raise ValueError(reason)
        if missing['record_type'] != 'missing' or surplus['record_type'] != 'surplus':
            raise ValueError('必须选择一条盘亏记录和一条盘盈记录')
        if not missing_fingerprint or not surplus_fingerprint or missing['fingerprint'] != missing_fingerprint or surplus['fingerprint'] != surplus_fingerprint:
            raise ValueError('来源数据已变化，请重新查询后核实')
        for pair in get_active_pairs(conn):
            if {missing_record_id, surplus_record_id} & {pair['missing_record_id'], pair['surplus_record_id']}:
                if pair['missing_record_id'] == missing_record_id and pair['surplus_record_id'] == surplus_record_id and pair['valid']:
                    return _metadata(pair)
                raise ValueError('所选记录已有有效拼销关系，请先撤销原拼销')
        case_id = _open_case(conn, {missing_record_id, surplus_record_id})
        if case_id is not None:
            raise ValueError(f'所选记录已有未闭环整改单 #{case_id}，请先处理现有工单')
        now = datetime.now().isoformat()
        pair_id = conn.execute("""INSERT INTO inventory_pairs
            (missing_record_id,surplus_record_id,missing_fingerprint,surplus_fingerprint,note,actor_id,created_at)
            VALUES(?,?,?,?,?,?,?)""", (missing_record_id, surplus_record_id, missing_fingerprint, surplus_fingerprint, note, actor_id, now)).lastrowid
        pair = dict(id=pair_id, missing_record_id=missing_record_id, surplus_record_id=surplus_record_id,
                    note=note, actor_id=actor_id, actor_name=actor['name'], created_at=now,
                    missing=missing, surplus=surplus, cross_room=missing['task_id'] != surplus['task_id'], valid=True, invalid_reason='')
        db._capture_audit(conn, actor_id, 'inventory.pair.create', 'inventory_pair', pair_id,
                          dict(missing=missing, surplus=surplus, note=note, cross_room=pair['cross_room']))
        conn.commit()
        return _metadata(pair)
    finally:
        conn.close()


def cancel_pair(pair_id: int, note: str, actor_id: int) -> dict[str, Any]:
    conn = db.get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        _admin(conn, actor_id)
        note = _note(note)
        pair = conn.execute('SELECT * FROM inventory_pairs WHERE id=?', (pair_id,)).fetchone()
        if pair is None:
            raise ValueError('拼销关系不存在')
        if pair['status'] == 'cancelled':
            return dict(id=pair_id, cancelled=False, status='已撤销')
        conn.execute("UPDATE inventory_pairs SET status='cancelled',cancelled_by=?,cancelled_at=?,cancel_note=? WHERE id=?", (actor_id, datetime.now().isoformat(), note, pair_id))
        db._capture_audit(conn, actor_id, 'inventory.pair.cancel', 'inventory_pair', pair_id, dict(before=dict(pair), note=note))
        conn.commit()
        return dict(id=pair_id, cancelled=True, status='已撤销')
    finally:
        conn.close()


def project_records(conn: sqlite3.Connection, task_id: int, records: list[dict[str, Any]], cache: dict | None = None) -> list[dict[str, Any]]:
    if cache is None:
        cache = {}
    if 'pairs' not in cache:
        cache['pairs'] = get_active_pairs(conn)
    pairs = {pair[key]: pair for pair in cache['pairs'] for key in ('missing_record_id', 'surplus_record_id')}
    result = []
    for record in records:
        pair = pairs.get(record['id'])
        if pair is None:
            result.append(record)
            continue
        meta = _metadata(pair, record['id'])
        if not pair['valid']:
            result.append({**record, 'pairing': meta, 'can_pair': False})
            continue
        if record['id'] == pair['missing_record_id']:
            if pair['cross_room']:
                result.append({**record, 'pairing': meta, 'is_pairing_outbound': True,
                               'field_status': '已拼销迁出', 'can_pair': False})
            continue
        missing = pair['missing']
        merged = {**record, 'device_name': missing['device_name'], 'record_type': 'normal',
                  'expected_model': missing.get('expected_model') or '',
                  'expected_barcode': missing.get('expected_barcode') or '',
                  'expected_rack_no': missing.get('expected_rack_no') or '',
                  'device_extra': missing.get('device_extra') or '{}',
                  'device_category': missing.get('device_category') or '',
                  'resource_device_name': missing.get('resource_device_name') or missing['device_name'],
                  'resource_location_code': missing.get('resource_location_code') or missing.get('room_code') or '',
                  'resource_baseline_state': 'paired_reference',
                  'missing_reason': '', 'field_status': '已拼销', 'pairing': meta,
                  'resource_editable': False, 'resource_unavailable_reason': '已拼销记录请先查看拼销关系，需修改原始资料时先撤销拼销',
                  'can_pair': False}
        merged = db.add_compare_status(merged)
        merged['match_status'] = db.calc_overall_match_status(merged['asset_match_status'], merged['model_match_status'])
        result.append(merged)
    return result
