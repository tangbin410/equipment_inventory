import type { CabinetRecord, Task, TaskRecord } from './types';
import { recordDeviceCategory } from './inventoryPairing.ts';

export type ModelKind = 'equipment' | 'odf' | 'iodf';
export interface RoomModelReport { task: Task; records: TaskRecord[]; cabinet_records: CabinetRecord[] }
export interface ModelDevice {
  id: number; name: string; category: string; model: string; barcode: string; positions: string[]; cabinetBarcodes: string[];
}
export interface RoomModelObject {
  id: string; code: string; kind: ModelKind; row: string; rowIndex: number; index: number;
  face: 'A' | 'B' | 'full'; barcodes: string[]; devices: ModelDevice[]; issues: string[]; pendingOverlap: boolean;
}
export interface ModelIssue { id: string; title: string; detail: string; objectIds: string[] }
export interface RoomModelData {
  cabinets: RoomModelObject[]; rows: string[]; maxPosition: number; issues: ModelIssue[];
  unplaced: { name: string; rawPosition: string; reason: string; kind: ModelKind }[];
  stats: { objectCount: number; deviceCount: number; mappedDeviceCount: number; unplacedCount: number; issueCount: number };
}

export const CABINET_U = 47;
export const U_MM = 44.45;
export const CABINET_DIMENSIONS_MM = { half: [600, 300, 2200], full: [600, 600, 2200] } as const;

const labels: Record<ModelKind, string> = { equipment: '传输综合柜', odf: 'ODF', iodf: 'IODF' };
const text = (value: unknown) => String(value ?? '').trim();
export function splitModelPositions(value: unknown): string[] {
  return [...new Set(text(value).normalize('NFKC').toUpperCase().split(/[,，、;；\r\n]+/).map(s => s.trim()).filter(Boolean))];
}
function parsePosition(code: string) {
  const match = /^A([A-Z])(\d{2})([AB]?)$/.exec(code);
  if (!match || Number(match[2]) === 0) return null;
  return { row: 'A' + match[1], rowIndex: match[1].charCodeAt(0) - 65, index: Number(match[2]), face: (match[3] || 'full') as RoomModelObject['face'] };
}
function barcodes(value: unknown): string[] {
  return [...new Set(text(value).normalize('NFKC').toUpperCase().split(/[,，、;；\s]+/).filter(Boolean))];
}

/** Only current effective feedback enters the model; no source records or layout are changed. */
export function buildRoomModel(report: RoomModelReport, kinds: ModelKind[]): RoomModelData {
  const selected = new Set(kinds);
  const objects = new Map<string, RoomModelObject>();
  const issues: ModelIssue[] = [];
  const unplaced: RoomModelData['unplaced'] = [];
  const problem = (title: string, detail: string, affected: RoomModelObject[] = [], block = false) => {
    issues.push({ id: `issue:${issues.length + 1}`, title, detail, objectIds: affected.map(c => c.id) });
    for (const object of affected) {
      if (!object.issues.includes(detail)) object.issues.push(detail);
      if (block) object.pendingOverlap = true;
    }
  };
  const cannotPlace = (name: string, rawPosition: string, reason: string, kind: ModelKind) => {
    unplaced.push({ name, rawPosition, reason, kind });
    problem('记录暂不能定位', `${labels[kind]} ${name}：${rawPosition || '未填写位置'}；${reason}`);
  };
  for (const source of report.cabinet_records) {
    const kind = source.cabinet_type as ModelKind;
    if (!selected.has(kind) || !['equipment', 'odf', 'iodf'].includes(kind)) continue;
    const raw = text(source.rack_no || source.location || source.cabinet_name);
    const codes = splitModelPositions(raw);
    if (!codes.length) cannotPlace(text(source.cabinet_name) || '未命名', '', '缺少排位编号', kind);
    for (const code of codes) {
      const parsed = parsePosition(code);
      if (!parsed) { cannotPlace(text(source.cabinet_name) || code, code, '编号不符合 AA01A / AA01 规则，未自动改号', kind); continue; }
      const id = `${kind}:${code}`;
      let object = objects.get(id);
      if (!object) {
        object = { id, code, kind, ...parsed, barcodes: [], devices: [], issues: [], pendingOverlap: false };
        objects.set(id, object);
      }
      // A multi-position equipment summary does not specify which barcode belongs to which position.
      if (!source.no_barcode && (kind !== 'equipment' || codes.length === 1)) {
        object.barcodes = [...new Set([...object.barcodes, ...barcodes(source.cabinet_barcode)])];
      }
    }
  }
  const devices = new Map<number, ModelDevice>();
  if (selected.has('equipment')) for (const record of report.records) {
    const flags = record as TaskRecord & { is_void?: boolean; is_pairing_outbound?: boolean };
    if (!record.scanned_at || record.record_type === 'missing' || flags.is_void || flags.is_pairing_outbound || record.pairing?.role === 'outbound') continue;
    if (devices.has(record.id)) continue;
    const positions = splitModelPositions(record.rack_no);
    const device: ModelDevice = {
      id: record.id, name: record.device_name, category: recordDeviceCategory(record), model: text(record.actual_model),
      barcode: record.asset_no_barcode ? '' : text(record.asset_barcodes), positions,
      cabinetBarcodes: record.cabinet_no_barcode ? [] : barcodes(record.cabinet_barcodes),
    };
    devices.set(device.id, device);
    const matched: RoomModelObject[] = [];
    for (const code of positions) {
      const object = objects.get(`equipment:${code}`);
      if (object) {
        object.devices.push(device); matched.push(object);
        if (positions.length === 1) object.barcodes = [...new Set([...object.barcodes, ...device.cabinetBarcodes])];
      }
    }
    if (!matched.length) {
      const raw = text(record.rack_no);
      // A source cabinet already reports an invalid position; keep device identity as a separate unplaced record.
      cannotPlace(device.name, raw, '没有可关联的有效综合柜位，设备未放入模型', 'equipment');
    }
    if (record.pairing?.role === 'stale') problem('拼销待复核', `${device.name} 的拼销来源已变化；当前按有效现场反馈展示，请管理员复核。`, matched);
  }
  const cabinets = [...objects.values()].sort((a, b) => a.rowIndex - b.rowIndex || a.index - b.index || a.face.localeCompare(b.face) || a.kind.localeCompare(b.kind));
  const barcodeObjects = new Map<string, RoomModelObject[]>();
  const slots = new Map<string, RoomModelObject[]>();
  for (const object of cabinets) {
    object.barcodes.sort();
    if (object.barcodes.length > 1) problem('同一位置关联多个条码', `${labels[object.kind]} ${object.code} 关联 ${object.barcodes.length} 个条码，身份待核实。`, [object], true);
    for (const barcode of object.barcodes) barcodeObjects.set(barcode, [...(barcodeObjects.get(barcode) || []), object]);
    const slot = `${object.row}:${object.index}`;
    slots.set(slot, [...(slots.get(slot) || []), object]);
  }
  for (const [barcode, linked] of barcodeObjects) if (linked.length > 1) {
    problem('条码对应多个对象', `条码 ${barcode} 同时关联 ${linked.map(c => `${labels[c.kind]} ${c.code}`).join('、')}，不能认定为多个实体。`, linked, true);
  }
  for (const linked of slots.values()) {
    const conflicts = linked.filter(a => linked.some(b => a.id !== b.id && (a.face === b.face || a.face === 'full' || b.face === 'full')));
    if (conflicts.length) problem('同一排位占位冲突', `${conflicts.map(c => `${labels[c.kind]} ${c.code}`).join('、')} 占位重叠；仅展示待核实底框。`, conflicts, true);
  }
  const mapped = new Set(cabinets.flatMap(c => c.devices.map(d => d.id)));
  const highestRow = Math.max(-1, ...cabinets.map(c => c.rowIndex));
  return {
    cabinets, rows: Array.from({ length: highestRow + 1 }, (_, i) => 'A' + String.fromCharCode(65 + i)),
    maxPosition: Math.max(1, ...cabinets.map(c => c.index)), issues, unplaced,
    stats: { objectCount: cabinets.length, deviceCount: devices.size, mappedDeviceCount: mapped.size, unplacedCount: unplaced.length, issueCount: issues.length },
  };
}
