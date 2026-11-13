<template>
  <div class="table-wrap">
    <el-table :data="rows" border :span-method="span" :row-class-name="rowClass" :max-height="460" empty-text="所选机房暂无可核对的传输综合柜">
      <el-table-column column-key="group-index" label="序号" width="64" align="center" fixed="left"><template #default="{$index}">{{Math.floor($index / systems.length) + 1}}</template></el-table-column>
      <el-table-column column-key="key" label="机柜资源 / 柜位" min-width="210"><template #default="{row}">{{row.group.resource_name}}<div class="muted">{{row.group.room_name}}</div></template></el-table-column>
      <el-table-column prop="system" label="系统" width="90" />
      <el-table-column label="机柜资产条码" min-width="190"><template #default="{row}"><div class="value" :class="{different:row.system!=='综资' && row.group.barcode_status==='不一致'}">{{barcode(row.group,row.system)}}</div></template></el-table-column>
      <el-table-column label="机柜位" min-width="120"><template #default="{row}">{{position(row.group,row.system)}}</template></el-table-column>
      <el-table-column label="地点编码" min-width="160"><template #default="{row}">{{location(row.group,row.system)}}</template></el-table-column>
      <el-table-column column-key="result" label="核对结果" min-width="330"><template #default="{row}">
        <el-tag :type="row.group.consistent?'success':'warning'">{{row.group.status}}</el-tag>
        <p>综资 ↔ 现场：柜位{{row.group.position_status}}</p>
        <p>现场 ↔ 资产：条码{{row.group.barcode_status}}；地点{{row.group.location_status}}</p>
        <p v-for="problem in row.group.problems" :key="problem" class="problem">{{problem}}</p>
      </template></el-table-column>
    </el-table>
  </div>
</template>
<script setup lang="ts">
import {computed} from 'vue';
import {systems} from '../threeWay';
import type {CabinetComparison} from '../threeWay';
const props=defineProps<{groups:CabinetComparison[]}>();
const rows=computed(()=>props.groups.flatMap(group=>systems.map(system=>({group,system}))));
function span({rowIndex,column}:{rowIndex:number;column:{columnKey:string}}) {
  return ['group-index','key','result'].includes(column.columnKey) ? rowIndex%3===0?[3,1]:[0,0] : [1,1];
}
function rowClass({rowIndex}:{rowIndex:number}) {return Math.floor(rowIndex/3)%2?'cabinet-alt':'cabinet-base';}
function barcode(g:CabinetComparison,system:string) {
  return system==='综资'?g.resource_barcode_status:system==='现场'?g.field_barcodes:g.asset_barcodes || '未找到';
}
function position(g:CabinetComparison,system:string) {
  return system==='综资'?g.resource_position || '未找到':system==='现场'?g.field_position || '未反馈':g.asset_records.length?g.position:'未找到';
}
function location(g:CabinetComparison,system:string) {
  return system==='资产'? [...new Set(g.asset_records.map(row=>row.location_code))].join('、') || '未找到'
    : (system==='综资'?g.resource_devices.length:g.field_devices.length)?g.room_code:'未反馈';
}
</script>
<style scoped>
.muted {color:var(--muted);font-size:12px;margin-top:7px;}
.value {white-space:pre-wrap;overflow-wrap:anywhere;}
.different {color:#c34545;background:#fff3f1;padding:4px;}
p {margin:8px 0;font-size:12px;}
.problem {color:#b7791f;}
:deep(.cabinet-base > td.el-table__cell) {background:#fff;}
:deep(.cabinet-alt > td.el-table__cell) {background:#f0f8ff;}
</style>
