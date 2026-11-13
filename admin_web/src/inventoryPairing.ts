import type {TaskRecord} from './types';

export function recordDeviceCategory(record: TaskRecord): string {
  if (record.device_category?.trim()) return record.device_category.trim();
  let extra: Record<string, unknown> = {};
  try {
    extra = typeof record.device_extra === 'string' ? JSON.parse(record.device_extra) : record.device_extra || {};
  } catch { return ''; }
  return String(extra?.['设备类别'] || extra?.['网元类型'] || '').trim();
}

export function isPairingRecord(record: TaskRecord): boolean {
  return Boolean(record.pairing) || record.record_type === 'missing' || record.record_type === 'surplus';
}

export function pairingResult(record: TaskRecord): string {
  const pairing = record.pairing;
  if (!pairing) return '';
  if (pairing.role === 'stale') return '拼销待复核';
  if (pairing.role === 'outbound') return '已拼销迁出';
  return pairing.cross_room ? '已拼销·待调拨' : '已拼销';
}

export function pairingNote(record: TaskRecord): string {
  const pairing = record.pairing;
  if (!pairing) return record.notes || '';
  const route = pairing.cross_room ? `${pairing.source_room_name} → ${pairing.target_room_name}` : pairing.target_room_name;
  return [record.notes, `${pairingResult(record)}：${route}`, pairing.note].filter(Boolean).join('；');
}
