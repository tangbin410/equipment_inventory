<template>
  <div class="table-wrap">
    <el-table :data="rows" border :span-method="span" :row-class-name="rowClass" empty-text="暂无比对数据，请选择已完成的机房" :max-height="maxHeight">
      <el-table-column v-if="showIndex" column-key="group-index" label="序号" width="64" align="center" fixed="left"><template #default="{$index}">{{indexOffset + Math.floor($index / systems.length) + 1}}</template></el-table-column>
      <el-table-column column-key="group-key" label="关联键：网元 / 资源名称" min-width="230"><template #default="{row}"><div class="raw-value">{{ keyName(row.group) }}</div></template></el-table-column>
      <el-table-column label="系统" width="110"><template #default="{row}">{{row.system}}</template></el-table-column>
      <el-table-column label="资产条码" min-width="170"><template #default="{row}"><div class="raw-value" :class="{'different': isDifferent(row, 'barcode')}">{{ value(row, 'barcode') }}</div></template></el-table-column>
      <el-table-column label="设备机柜位" min-width="130"><template #default="{row}"><div class="raw-value" :class="{'different': isDifferent(row, 'rack_no')}">{{row.system==='资产'?'不参与':value(row, 'rack_no')}}</div></template></el-table-column>
      <el-table-column label="地点编码" min-width="145"><template #default="{row}"><div class="raw-value" :class="{'different': isDifferent(row, 'location_code')}">{{ value(row, 'location_code') }}</div></template></el-table-column>
      <el-table-column label="规格型号" min-width="180"><template #default="{row}"><div class="raw-value" :class="{'different': isDifferent(row, 'model')}">{{ value(row, 'model') }}</div></template></el-table-column>
      <el-table-column column-key="result" label="差异与判断" min-width="275"><template #default="{row}">
        <div class="result-stack">
          <span>{{row.group.room_name}}</span>
          <div><el-tag :type="row.group.consistent ? 'success' : hasDifference(row.group) ? 'danger' : 'warning'">{{row.group.consistent ? '三方一致' : hasDifference(row.group) ? '存在差异' : '待处理 / 待判断'}}</el-tag></div>
          <el-tag v-if="row.group.manual" type="info">多条码 · 人工比对</el-tag>
          <span v-for="note in associationNotes(row.group)" :key="note" class="association-note">{{note}}</span>
          <div v-for="pair in differences(row.group)" :key="pair.name" class="pair-result">
            <b>{{pair.name}}</b>
            <div class="check-list"><span v-for="check in pair.checks" :key="check.label">{{check.label}} <el-tag size="small" :type="statusTag(check.status)">{{check.status}}</el-tag></span></div>
          </div>
        </div>
      </template></el-table-column>
      <el-table-column v-if="$slots.actions" column-key="actions" label="处理" width="150"><template #default="{row}"><slot name="actions" :group="row.group" /></template></el-table-column>
    </el-table>
  </div>
</template>
<script setup lang="ts">
import {computed} from "vue";
import {systems, statusTag, sourceRecords} from "../threeWay";
import type {ComparisonGroup, SourceRow} from "../threeWay";
const props = withDefaults(defineProps<{groups: ComparisonGroup[]; maxHeight?: number; raw?: boolean; showIndex?: boolean; indexOffset?: number}>(), {maxHeight: 660, raw: false, showIndex: false, indexOffset: 0});
type TableRow = {system: string; group: ComparisonGroup};
const pairs = [
  {key: '综资现场', name: '综资 ↔ 现场', systems: ['综资', '现场']},
  {key: '综资资产', name: '综资 ↔ 资产', systems: ['综资', '资产']},
  {key: '现场资产', name: '现场 ↔ 资产', systems: ['现场', '资产']},
];
const rows = computed(() => props.groups.flatMap(group => systems.map(system => ({system, group}))));
function span({rowIndex, column}: {rowIndex: number; column: {columnKey: string}}) {
  if (['group-index', 'group-key', 'result', 'actions'].includes(column.columnKey)) return rowIndex % systems.length === 0 ? [systems.length, 1] : [0, 0];
  return [1, 1];
}
function rowClass({rowIndex}: {rowIndex: number}) {return Math.floor(rowIndex / systems.length) % 2 ? 'comparison-group-alt' : 'comparison-group';}
function keyName(group: ComparisonGroup) {
  for (const system of systems) {
    const manual = !props.raw && group.manual_decision?.values[system];
    const name = manual && 'name' in manual ? manual.name : sourceRecords(group, system)[0]?.name;
    if (name) return name;
  }
  return "未填写";
}
function value(row: TableRow, field: keyof SourceRow) {
  const manual = !props.raw && row.group.manual_decision?.values[row.system];
  if (manual && field in manual) return manual[field] || "未填写";
  const source = sourceRecords(row.group, row.system);
  if (!source.length) return row.system === '综资' ? '无对应设备' : row.system === '现场' ? (row.group.stage_one_status === '现场登记盘亏' ? '登记盘亏' : '未反馈') : '缺失';
  return source.map(r => field === 'barcode' && !r.barcode ? r.barcode_state || '未填写（待补录）' : String(r[field] || "未填写")).join("\n");
}
function hasDifference(group: ComparisonGroup) {return Object.values(group.checks).some(checks=>Object.values(checks).includes('不一致'));}
function differences(group: ComparisonGroup) {
  return pairs.map(pair=>({name: pair.name, checks: Object.entries(group.checks[pair.key]).filter(([,status])=>status!=='一致').map(([label,status])=>({label: label==='机架号'?'设备机柜位':label, status}))})).filter(pair=>pair.checks.length);
}
function associationNotes(group: ComparisonGroup) {return group.problems.filter(note=>! /^(综资现场|综资资产|现场资产)/.test(note) && note!=='多条码设备待人工核对');}
function isDifferent(row: TableRow, field: string) {
  if (field==='rack_no' && row.system==='资产') return false;
  return pairs.some(pair=>pair.systems.includes(row.system) && Object.entries(row.group.checks[pair.key]).some(([label,status])=>{
    const key = label==='资产条码'?'barcode':label==='机架号'?'rack_no':label==='地点编码'?'location_code':label==='设备型号' || label==='规格型号'?'model':'name';
    return key===field && status==='不一致';
  }));
}
</script>
<style scoped>
.raw-value {white-space:pre-wrap;overflow-wrap:anywhere;padding:3px 0;}
.different {color:#c34545;background:#fff3f1;border-radius:3px;padding:4px;}
.result-stack {display:flex;flex-direction:column;align-items:flex-start;gap:9px;font-size:12px;}
.association-note {color:#b7791f;line-height:1.6;}
.pair-result {width:100%;}
.pair-result b {font-size:12px;color:var(--muted);}
.check-list {display:flex;flex-wrap:wrap;gap:5px 10px;margin-top:4px;}
:deep(.el-table__body tr.comparison-group > td.el-table__cell) {background:#fff;}
:deep(.el-table__body tr.comparison-group-alt > td.el-table__cell) {background:#f0f8ff;}
</style>
