import {sourceRecords, systems} from './threeWay.ts';
import type {ComparisonGroup, SourceRow} from './threeWay';

type Field = 'barcode' | 'rack_no' | 'location_code' | 'name' | 'model';
export interface ComparisonIssue {title: string; detail: string}

/** 问题弹窗按原因归并展示，原始比对结果和人工判断保持不变。 */
export function comparisonIssues(group: ComparisonGroup): ComparisonIssue[] {
  if (group.consistent) return [];
  const issues: ComparisonIssue[] = [];
  const add = (title: string, detail = '') => {
    if (!issues.some(issue => issue.title === title && issue.detail === detail)) issues.push({title, detail});
  };
  const rows = Object.fromEntries(systems.map(system => {
    const source = sourceRecords(group, system);
    const manual = group.manual_decision?.values[system];
    return [system, manual ? [{...source[0], rack_no: [...new Set(source.map(row => row.rack_no).filter(Boolean))].join(' / '), ...manual}] : source];
  })) as Record<string, SourceRow[]>;
  const missing = {综资: '综资未找到对应网元', 现场: group.stage_one_status === '现场登记盘亏' ? '现场登记盘亏' : '现场未找到对应网元反馈', 资产: '资产表未找到对应资源名称'};
  for (const system of systems) {
    if (!rows[system].length && !(system === '现场' && group.problems.some(p => p.includes('关联冲突')))) add(missing[system]);
  }
  for (const problem of group.problems) {
    // 缺少来源或字段的连带提示由下方统一生成，不重复罗列三组待处理。
    if (/^(综资现场|综资资产|现场资产)(资源名称|资产条码|机架号|设备型号|规格型号|地点编码)/.test(problem)
      || /^(综资|现场)已有记录，但/.test(problem)
      || Object.values(missing).includes(problem)
      || problem === '现场未找到对应网元反馈'
      || problem === '资产表资源名称在综资中未找到对应网元'
      || problem === '已有设备反馈缺少资产条码，待补录后关联') continue;
    add(problem);
  }
  const fields: Array<{field: Field; label: string; systems: readonly string[]}> = [
    {field: 'barcode', label: '资产条码', systems},
    {field: 'rack_no', label: '机柜位', systems: ['综资', '现场']},
    {field: 'location_code', label: '地点编码', systems},
    {field: 'model', label: '型号', systems},
  ];
  for (const {field, label, systems: owners} of fields) {
    for (const system of owners) {
      if (!rows[system].length || rows[system].some(row => String(row[field] || '').trim())) continue;
      add(field === 'barcode' && system === '现场' && rows[system].some(row => row.barcode_state === '现场无条码')
        ? '现场无资产条码' : `${system === '资产' ? '资产表' : system}${label}未填写`);
    }
  }
  if (group.manual && !group.manual_decision) return issues;

  const pairs = [
    {key: '综资现场', systems: ['综资', '现场']},
    {key: '综资资产', systems: ['综资', '资产']},
    {key: '现场资产', systems: ['现场', '资产']},
  ];
  const fieldMap: Record<string, Field> = {资产条码: 'barcode', 机架号: 'rack_no', 设备机柜位: 'rack_no', 地点编码: 'location_code', 资源名称: 'name', 设备型号: 'model', 规格型号: 'model'};
  const grouped = new Map<string, {field: Field; label: string; status: string; pairs: Set<string>; systems: Set<string>}>();
  for (const pair of pairs) {
    for (const [label, status] of Object.entries(group.checks[pair.key] || {})) {
      const field = fieldMap[label];
      if (!field || status === '一致' || pair.systems.some(system => rows[system].length !== 1 || !String(rows[system][0][field] || '').trim())) continue;
      const key = `${field}:${status}${field === 'model' ? `:${pair.key}` : ''}`;
      const item = grouped.get(key) || {field, label, status, pairs: new Set<string>(), systems: new Set<string>()};
      item.pairs.add(pair.key);
      pair.systems.forEach(system => item.systems.add(system));
      grouped.set(key, item);
    }
  }
  for (const item of grouped.values()) {
    let subject: string;
    if (item.field === 'rack_no') subject = '现场机柜位';
    else if (item.field === 'model') subject = item.pairs.has('综资现场') ? '现场设备型号' : '资产表规格型号';
    else {
      const prefix = item.pairs.has('综资现场') && !item.pairs.has('综资资产') ? '现场'
        : item.pairs.has('综资资产') && !item.pairs.has('综资现场') ? '资产表'
        : item.pairs.has('现场资产') && item.pairs.size === 1 ? '现场与资产' : '';
      subject = `${prefix}${prefix === '现场与资产' && item.field === 'barcode' ? '条码' : item.label}`;
    }
    const detail = systems.filter(system => item.systems.has(system)).map(system => `${system}：${rows[system][0][item.field]}`).join('；');
    add(`${subject}${item.status}`, detail);
  }
  return issues;
}
