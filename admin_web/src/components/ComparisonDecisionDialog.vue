<template>
  <el-dialog :model-value="modelValue" :title="group?.manual ? '多条码设备人工比对' : '两处设备型号判断'" width="min(1100px, 94vw)" @close="emit('update:modelValue', false)">
    <template v-if="group">
      <ThreeWayTable :groups="[group]" raw :max-height="320" />
      <el-alert v-if="group.pending_association" title="已有反馈但资产条码缺失，仅按所属任务设备记录展示参考原值。请先补录真实条码并完成关联，再保存三方判断。" type="warning" :closable="false" class="notice" />
      <el-alert v-if="group.manual" title="请填写核对后的三方数据，并逐项判断三组比对。保存后，页面与导出均以本次填写值为准。" type="info" :closable="false" class="notice" />
      <el-table v-if="group.manual" :data="manualRows" border>
        <el-table-column prop="system" label="系统" width="85" />
        <el-table-column v-for="field in fields" :key="field.key" :label="field.label" min-width="160"><template #default="{row}"><el-input v-model="row[field.key]" /></template></el-table-column>
      </el-table>
      <div v-if="group.manual" class="manual-checks"><div v-for="(checks,pair) in group.checks" :key="pair"><b>{{pair}}</b><div v-for="(_,label) in checks" :key="label" class="manual-check"><span>{{label}}</span><el-select v-model="manualChecks[pair][label]"><el-option v-for="s in decisionStates" :key="s" :label="s" :value="s" /></el-select></div></div></div>
      <el-form label-position="top" class="judgment-form">
        <template v-if="!group.manual"><el-form-item label="综资与现场设备型号"><el-select v-model="fieldModelStatus"><el-option v-for="s in decisionStates" :key="s" :label="s" :value="s" /></el-select></el-form-item><el-form-item label="综资与资产规格型号"><el-select v-model="assetModelStatus"><el-option v-for="s in decisionStates" :key="s" :label="s" :value="s" /></el-select></el-form-item></template>
        <el-form-item label="判断依据" class="basis"><el-input v-model="note" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="说明核对依据；任意一方不一致，整体选择不一致" /></el-form-item>
      </el-form>
      <el-collapse v-if="group.history.length"><el-collapse-item title="历史判断记录" name="history"><p v-for="h in group.history" :key="h.id">{{h.created_at}} · {{h.actor_name || `用户 #${h.actor_id}`}} · {{h.decision.status || '人工整体判断'}} · {{h.decision.note}}</p></el-collapse-item></el-collapse>
    </template>
    <template #footer><el-button @click="emit('update:modelValue', false)">取消</el-button><el-button type="primary" :loading="saving" :disabled="group?.pending_association" @click="save">保存判断</el-button></template>
  </el-dialog>
</template>
<script setup lang="ts">
import {ref, watch} from "vue";
import {ElMessage} from "element-plus";
import {apiPost} from "../api";
import {systems, decisionStates} from "../threeWay";
import type {ComparisonGroup, DecisionState} from "../threeWay";
import ThreeWayTable from "./ThreeWayTable.vue";
const props = defineProps<{modelValue: boolean; group: ComparisonGroup | null}>();
const emit = defineEmits<{(e:'update:modelValue', value:boolean):void; (e:'saved'):void}>();
const saving=ref(false), note=ref('');
const fieldModelStatus=ref<DecisionState>('待判断'), assetModelStatus=ref<DecisionState>('待判断');
const fields = [{key:'barcode',label:'资产条码'}, {key:'name',label:'网元 / 资源名称'}, {key:'location_code',label:'地点编码'}, {key:'model',label:'规格型号'}];
const manualRows=ref<Array<Record<string,string>>>([]);
const manualChecks=ref<Record<string,Record<string,DecisionState>>>({});
watch(() => [props.modelValue, props.group] as const, () => {
  if (!props.modelValue || !props.group) return;
  const g=props.group, d=g.manual_decision;
  fieldModelStatus.value=g.field_model_status; assetModelStatus.value=g.asset_model_status;
  note.value=d?.note || '';
  manualRows.value=systems.map(system => ({system, barcode:d?.values[system]?.barcode || '',name:d?.values[system]?.name || '',location_code:d?.values[system]?.location_code || '',model:d?.values[system]?.model || ''}));
  manualChecks.value=Object.fromEntries(Object.entries(g.checks).map(([pair,checks])=>[pair,Object.fromEntries(Object.keys(checks).map(label=>[label,d?.pair_statuses?.[pair]?.[label] || '待判断']))]));
});
async function save() {
  if (!props.group || !note.value.trim()) {ElMessage.warning('请填写判断依据'); return;}
  saving.value=true;
  try {
    const g=props.group;
    const decision=g.manual ? {pair_statuses:manualChecks.value,note:note.value,values:Object.fromEntries(manualRows.value.map(r=>[r.system,r]))} : {field_status:fieldModelStatus.value,asset_status:assetModelStatus.value,note:note.value};
    await apiPost('/reconciliation/decisions',{task_ids:g.scope_task_ids, group_key:g.group_key,fingerprint:g.fingerprint,kind:g.manual?'manual':'model',decision});
    ElMessage.success('判断已保存'); emit('saved'); emit('update:modelValue',false);
  } finally {saving.value=false;}
}
</script>
<style scoped>
.notice {margin: 12px 0;}
.judgment-form {display:grid; grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px;}
.basis {grid-column:1/-1;}
.manual-checks {display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:16px;}
.manual-check {display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px;}
.manual-check .el-select {width:120px;}
</style>
