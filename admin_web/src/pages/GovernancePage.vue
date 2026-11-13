<template>
  <PageHeader :icon="CircleCheck" title="整改台账" desc="确认三方比对问题，审核责任、处理、复核数据并验收闭环" />
  <div class="metric-grid"><div v-for="m in metrics" :key="m.label" class="metric-card"><div class="metric-label">{{m.label}}</div><div class="metric-value">{{m.value}}</div></div></div>
  <el-tabs v-model="tab">
    <el-tab-pane label="三方问题待确认" name="candidates">
      <div class="toolbar">
        <el-form-item class="span-4" label="盘点批次"><el-select v-model="batch" @change="loadCandidates"><el-option v-for="b in batches" :key="b" :label="b" :value="b" /></el-select></el-form-item>
        <el-form-item class="span-5" label="关键字"><el-input v-model="candidateKeyword" clearable placeholder="条码、机房、网元名称" @input="candidatePage=1" /></el-form-item>
        <div class="span-3 toolbar-actions"><el-button :loading="loading" @click="refresh">刷新问题和台账</el-button></div>
      </div>
      <el-alert v-for="warning in candidates?.warnings || []" :key="warning" :title="warning" type="warning" :closable="false" class="notice" />
      <div class="table-wrap"><el-table :data="candidateRows.slice((candidatePage-1)*30,candidatePage*30)" border stripe>
        <el-table-column prop="barcode" label="资产条码" min-width="170" /><el-table-column prop="room_name" label="机房" min-width="180" /><el-table-column prop="device_name" label="网元 / 资源名称" min-width="220" show-overflow-tooltip />
        <el-table-column label="待确认问题" min-width="300"><template #default="{row}"><div v-for="p in row.problems" :key="p" class="problem">{{p}}</div></template></el-table-column>
        <el-table-column label="操作" width="180"><template #default="{row}"><el-button link type="primary" @click="viewCandidate(row)">查看三方 / 判断</el-button><el-button v-if="row.case && row.case.status!=='done'" link type="primary" @click="openCase(row.case.id)">工单 #{{row.case.id}}</el-button><el-button v-else-if="permissions.writeGovernance && row.can_create_adjustment" link type="warning" @click="createCase(row)">确认并建单</el-button><span v-else-if="permissions.writeGovernance" class="muted">先完成人工判断</span></template></el-table-column>
      </el-table></div>
      <el-pagination v-model:current-page="candidatePage" :total="candidateRows.length" :page-size="30" layout="total, prev, pager, next" class="pagination" />
    </el-tab-pane>
    <el-tab-pane label="整改工单" name="cases">
      <div class="toolbar"><el-form-item class="span-3" label="状态"><el-select v-model="status" clearable placeholder="全部"><el-option v-for="(label,key) in statusLabels" :key="key" :label="label" :value="key" /></el-select></el-form-item><el-form-item class="span-5" label="关键字"><el-input v-model="keyword" clearable placeholder="条码、机房、设备或问题" /></el-form-item><div class="span-4 toolbar-actions"><el-button :loading="loading" @click="refresh">刷新</el-button></div></div>
      <div class="table-wrap"><el-table :data="caseRows.slice((casePage-1)*30,casePage*30)" border stripe>
        <el-table-column prop="id" label="单号" width="80" /><el-table-column label="资产条码" min-width="155"><template #default="{row}">{{row.comparison.snapshot.barcode || '待补条码'}}</template></el-table-column><el-table-column prop="room_name" label="机房" min-width="180" /><el-table-column prop="problem_type" label="问题" min-width="260" show-overflow-tooltip />
        <el-table-column label="处理责任" min-width="160"><template #default="{row}">{{responsibility(row)}}</template></el-table-column><el-table-column label="状态" width="120"><template #default="{row}"><el-tag :type="caseTag(row.status)">{{statusLabels[row.status]}}</el-tag></template></el-table-column><el-table-column prop="assigned_name" label="现场处理人" min-width="110" /><el-table-column prop="created_at" label="建单时间" min-width="170" /><el-table-column label="操作" width="110"><template #default="{row}"><el-button link type="primary" @click="openCase(row.id)">{{permissions.writeGovernance?'查看 / 处理':'查看'}}</el-button></template></el-table-column>
      </el-table></div>
      <el-pagination v-model:current-page="casePage" :total="caseRows.length" :page-size="30" layout="total, prev, pager, next" class="pagination" />
    </el-tab-pane>
    <el-tab-pane :label="`历史工单（${legacyCases.length}）`" name="legacy">
      <el-alert title="历史工单保留原有记录，不计入新的三方一致闭环。未完成的问题请在三方比对中重新确认并建单。" type="info" :closable="false" class="notice" />
      <div class="table-wrap"><el-table :data="legacyCases.slice((legacyPage-1)*30,legacyPage*30)" border stripe><el-table-column prop="id" label="单号" width="80" /><el-table-column prop="room_name" label="机房" min-width="170" /><el-table-column prop="problem_type" label="问题" min-width="250" /><el-table-column label="原状态" width="120"><template #default="{row}">{{statusLabels[row.status]}}</template></el-table-column><el-table-column label="操作" width="100"><template #default="{row}"><el-button link @click="openCase(row.id)">查看</el-button></template></el-table-column></el-table></div>
      <el-pagination v-model:current-page="legacyPage" :total="legacyCases.length" :page-size="30" layout="total, prev, pager, next" class="pagination" />
    </el-tab-pane>
  </el-tabs>
  <el-dialog v-model="candidateOpen" title="确认三方比对问题" width="min(1150px,96vw)"><template v-if="activeGroup"><ThreeWayTable :groups="[activeGroup]" /><p v-for="p in activeGroup.problems" :key="p">{{p}}</p><el-button type="primary" @click="decisionOpen=true">{{activeGroup.manual?'完整人工比对':'判断规格型号'}}</el-button><el-button v-if="permissions.writeGovernance" :disabled="!activeGroup.can_create_adjustment || !!activeGroup.case && activeGroup.case.status!=='done'" @click="createCase(activeGroup)">确认问题并建单</el-button></template></el-dialog>
  <el-dialog v-model="caseOpen" :title="`整改单 #${detail?.id || ''}`" width="min(1200px,96vw)">
    <template v-if="detail">
      <el-descriptions :column="3" border><el-descriptions-item label="机房">{{detail.room_name}}</el-descriptions-item><el-descriptions-item label="当前状态">{{statusLabels[detail.status]}}</el-descriptions-item><el-descriptions-item label="处理责任">{{responsibility(detail)}}</el-descriptions-item><el-descriptions-item label="审核说明" :span="3">{{detail.review_note || '待审核'}}</el-descriptions-item></el-descriptions>
      <template v-if="detail.comparison">
        <SectionTitle :icon="Tickets" title="最新三方比对" muted="验收时以最新数据重新校验" />
        <el-alert v-for="w in detail.warnings || []" :key="w" :title="w" type="warning" :closable="false" class="notice" />
        <ThreeWayTable v-if="detail.current" :groups="detail.current_groups || [detail.current]" :max-height="350" /><el-alert v-else title="原比对条目已变化，无法直接验收，请核查来源和条码关联。" type="warning" :closable="false" />
        <p v-for="p in detail.current?.problems || []" :key="p" class="problem">{{p}}</p>
        <el-button v-if="detail.current && !['done','rejected'].includes(detail.status)" @click="judgeCurrent">{{detail.current.manual?'人工比对':'更新型号判断'}}</el-button>
        <el-collapse class="history"><el-collapse-item title="建单时的问题快照" name="snapshot"><ThreeWayTable :groups="[detail.comparison.snapshot]" :max-height="340" /><p v-for="p in detail.comparison.snapshot.problems" :key="p">{{p}}</p></el-collapse-item><el-collapse-item v-if="detail.comparison.accepted_snapshot" title="验收时的数据快照" name="accepted"><ThreeWayTable :groups="[detail.comparison.accepted_snapshot]" :max-height="340" /></el-collapse-item></el-collapse>
        <el-form v-if="permissions.writeGovernance && detail.status==='pending_review'" label-position="top" class="review-form"><el-form-item label="审核后需要处理的责任方"><el-checkbox v-model="needsField">现场整改</el-checkbox><el-checkbox v-model="needsAdmin">管理员修改资产 / 综资原系统并更新数据</el-checkbox></el-form-item><el-form-item v-if="needsField" label="现场处理人"><el-select v-model="assignedTo" filterable placeholder="选择同片区现场人员"><el-option v-for="u in eligibleWorkers" :key="u.id" :label="`${u.name} · ${u.area_name}`" :value="u.id" /></el-select></el-form-item></el-form>
        <div v-if="detail.status!=='pending_review'" class="handling-grid"><div><strong>现场处理</strong><p>{{detail.comparison.needs_field ? detail.comparison.field_done?'已提交':'待处理':'无需派单'}}</p><p>{{detail.completion_note}}</p></div><div><strong>管理员处理</strong><p>{{detail.comparison.needs_admin ? detail.comparison.admin_done?'已提交':'待处理':'无需处理'}}</p><p>{{detail.comparison.admin_note}}</p></div></div>
        <el-form v-if="permissions.writeGovernance && !['done','rejected'].includes(detail.status)" label-position="top"><el-form-item label="本次操作说明"><el-input v-model="actionNote" type="textarea" :rows="3" maxlength="1500" show-word-limit placeholder="填写审核依据、原系统处理情况或验收结论" /></el-form-item><div v-if="detail.status==='pending_verify'"><el-checkbox v-model="returnField" :disabled="!detail.comparison.needs_field">退回现场继续整改</el-checkbox><el-checkbox v-model="returnAdmin" :disabled="!detail.comparison.needs_admin">退回管理员继续处理</el-checkbox></div></el-form>
        <div v-if="permissions.writeGovernance" class="case-actions"><template v-if="detail.status==='pending_review'"><el-button :loading="saving" @click="act('reject_review')">审核驳回</el-button><el-button type="primary" :loading="saving" @click="act('approve')">审核通过并开始处理</el-button></template><el-button v-if="['dispatched','pending_verify'].includes(detail.status) && detail.comparison.needs_admin && !detail.comparison.admin_done" type="primary" :loading="saving" @click="act('admin_complete')">提交管理员处理结果</el-button><template v-if="detail.status==='pending_verify'"><el-button type="warning" :loading="saving" @click="act('return')">验收不通过，继续整改</el-button><el-button type="success" :loading="saving" :disabled="!canVerify" @click="act('verify')">三方一致，验收闭环</el-button></template></div>
        <p v-if="detail.status==='pending_verify' && !detail.current?.consistent" class="muted">请更新资产信息表或综资基线、修正现场反馈并完成型号判断，最新三方一致后才能验收。</p>
        <SectionTitle :icon="Tickets" title="处理记录" /><el-timeline><el-timeline-item v-for="h in detail.history" :key="h.id" :timestamp="h.created_at">{{actionLabels[h.action] || h.action}} · {{h.actor_name || `用户 #${h.actor_id}`}}<p>{{h.data.note || '已保存三方比对快照'}}</p></el-timeline-item></el-timeline>
      </template>
      <template v-else><p>{{detail.problem_type}}</p><p>{{detail.basis}}</p><p>{{detail.suggestion}}</p><p>现场反馈：{{detail.completion_note || '暂无'}}</p><p>验收说明：{{detail.verification_note || '暂无'}}</p></template>
    </template>
  </el-dialog>
  <ComparisonDecisionDialog v-model="decisionOpen" :group="activeGroup" @saved="afterDecision" />
</template>
<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue';
import {CircleCheck,Tickets} from '@element-plus/icons-vue';
import {ElMessage,ElMessageBox} from 'element-plus';
import {apiGet,apiPost} from '../api';
import {permissions} from '../permissions';
import type {AdminUser} from '../types';
import type {ComparisonCase,ComparisonGroup,ComparisonResult,ComparisonTask} from '../threeWay';
import PageHeader from '../components/PageHeader.vue';
import SectionTitle from '../components/SectionTitle.vue';
import ThreeWayTable from '../components/ThreeWayTable.vue';
import ComparisonDecisionDialog from '../components/ComparisonDecisionDialog.vue';
const tab=ref('candidates'),batch=ref(''),tasks=ref<ComparisonTask[]>([]),candidates=ref<ComparisonResult|null>(null),cases=ref<ComparisonCase[]>([]),workers=ref<AdminUser[]>([]);
const candidateKeyword=ref(''),keyword=ref(''),status=ref(''),loading=ref(false),saving=ref(false),candidatePage=ref(1),casePage=ref(1),legacyPage=ref(1);
const candidateOpen=ref(false),caseOpen=ref(false),decisionOpen=ref(false),activeGroup=ref<ComparisonGroup|null>(null),detail=ref<ComparisonCase|null>(null);
const needsField=ref(false),needsAdmin=ref(true),assignedTo=ref<number>(),actionNote=ref(''),returnField=ref(false),returnAdmin=ref(false);
let candidateSequence=0,refreshSequence=0;
const statusLabels:Record<string,string>={pending_review:'待审核',dispatched:'处理中',pending_verify:'待验收',done:'已闭环',rejected:'已驳回'};
const actionLabels:Record<string,string>={'case.create':'确认问题并建单','case.approve':'审核通过','case.admin_complete':'管理员提交结果','case.field_complete':'现场提交结果','case.reject_review':'审核驳回','case.return':'验收不通过，继续整改','case.verify':'三方一致，验收闭环'};
const batches=computed(()=>[...new Set(tasks.value.filter(t=>t.status==='done').map(t=>t.import_batch_id))]);
const activeCases=computed(()=>cases.value.filter(c=>c.comparison));
const legacyCases=computed(()=>cases.value.filter(c=>!c.comparison));
const candidateRows=computed(()=>(candidates.value?.groups || []).filter(g=>!g.consistent && (!candidateKeyword.value.trim() || [g.barcode,g.room_name,g.device_name].join(' ').toLowerCase().includes(candidateKeyword.value.trim().toLowerCase()))));
const caseRows=computed(()=>activeCases.value.filter(c=>(!status.value || c.status===status.value) && (!keyword.value.trim() || [c.room_name,c.device_name,c.problem_type,c.comparison?.snapshot.barcode].join(' ').toLowerCase().includes(keyword.value.trim().toLowerCase()))));
const canVerify=computed(()=>!!detail.value?.current_groups?.length && !detail.value?.warnings?.length && detail.value.current_groups.every(g=>g.consistent));
const eligibleWorkers=computed(()=>workers.value.filter(w=>w.is_active && w.area_name===detail.value?.area_name));
const metrics=computed(()=>[{label:'本批次待确认',value:candidateRows.value.filter(g=>!g.case || g.case.status==='rejected').length},...['pending_review','dispatched','pending_verify','done'].map(s=>({label:statusLabels[s],value:activeCases.value.filter(c=>c.status===s).length}))]);
watch([keyword,status],()=>casePage.value=1);
function caseTag(s:string):'info'|'warning'|'primary'|'success'|'danger' {return s==='done'?'success':s==='rejected'?'info':s==='pending_verify'?'warning':'primary';}
function responsibility(c:ComparisonCase) {return c.comparison ? c.comparison.needs_field===null?'待审核确认':[c.comparison.needs_field?'现场':'',c.comparison.needs_admin?'管理员':''].filter(Boolean).join('、'):'历史工单';}
async function loadCandidates() {
  const sequence=++candidateSequence,selectedBatch=batch.value;
  const ids=tasks.value.filter(t=>t.import_batch_id===selectedBatch && t.status==='done').map(t=>t.id);
  candidates.value=null;candidatePage.value=1;
  if(!ids.length) return;
  const result=await apiGet<ComparisonResult>(`/reconciliation?${ids.map(id=>`task_ids=${id}`).join('&')}`);
  if(sequence===candidateSequence && batch.value===selectedBatch) candidates.value=result;
}
async function refresh() {
  const sequence=++refreshSequence;
  candidateSequence+=1;candidates.value=null;loading.value=true;
  try {
    const data=await Promise.all([apiGet<ComparisonTask[]>('/tasks'),apiGet<ComparisonCase[]>('/ai/adjustments'),permissions.value.writeGovernance?apiGet<AdminUser[]>('/workers'):Promise.resolve([])]);
    if(sequence!==refreshSequence) return;
    tasks.value=data[0];cases.value=data[1];workers.value=data[2];
    if(!batches.value.includes(batch.value))batch.value=batches.value[0] || '';
    await loadCandidates();
  } finally {if(sequence===refreshSequence)loading.value=false;}
}
function viewCandidate(g:ComparisonGroup) {activeGroup.value=g;candidateOpen.value=true;}
async function createCase(g:ComparisonGroup) {try {await ElMessageBox.confirm(g.problems.join('；'),'确认实际问题并建单',{confirmButtonText:'确认建单'});}catch{return;}const r=await apiPost<{item:ComparisonCase}>('/reconciliation/adjustments',{task_ids:g.scope_task_ids,group_key:g.group_key,fingerprint:g.fingerprint});candidateOpen.value=false;await refresh();tab.value='cases';await openCase(r.item.id);}
async function openCase(id:number) {detail.value=await apiGet<ComparisonCase>(`/reconciliation/cases/${id}`);actionNote.value='';needsField.value=false;needsAdmin.value=true;assignedTo.value=undefined;returnField.value=!!detail.value.comparison?.needs_field;returnAdmin.value=!!detail.value.comparison?.needs_admin;caseOpen.value=true;}
function judgeCurrent(){activeGroup.value=detail.value?.current || null;decisionOpen.value=true;}
async function afterDecision(){await refresh();if(caseOpen.value && detail.value){detail.value=await apiGet<ComparisonCase>(`/reconciliation/cases/${detail.value.id}`);}if(candidateOpen.value && activeGroup.value){activeGroup.value=candidates.value?.groups.find(g=>g.group_key===activeGroup.value?.group_key)||null;}}
async function act(action:string) {
  if(!detail.value || !actionNote.value.trim()){ElMessage.warning('请填写本次操作说明');return;}
  if(action==='approve' && needsField.value && !assignedTo.value){ElMessage.warning('请选择现场处理人');return;}
  saving.value=true;
  try {const r=await apiPost<{item:ComparisonCase}>(`/reconciliation/cases/${detail.value.id}/${action}`,{note:actionNote.value,needs_field:needsField.value,needs_admin:needsAdmin.value,assigned_to:assignedTo.value,return_field:returnField.value,return_admin:returnAdmin.value});detail.value=r.item;actionNote.value='';ElMessage.success('操作已保存');await refresh();}finally{saving.value=false;}
}
onMounted(refresh);
onBeforeUnmount(()=>{candidateSequence+=1;refreshSequence+=1;});
</script>
<style scoped>
.notice {margin-bottom:14px;}
.toolbar-actions {display:flex;align-items:center;justify-content:flex-end;gap:8px;}
.problem {font-size:13px;line-height:1.7;overflow-wrap:anywhere;margin:4px 0;}
.muted {font-size:12px;color:var(--muted);line-height:1.8;}
.pagination {justify-content:flex-end;margin-top:16px;}
.history,.review-form {margin-top:20px;}
.handling-grid {display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0;}
.handling-grid>div {padding:16px;background:#f6faff;border:1px solid var(--line);border-radius:8px;}
.handling-grid p {white-space:pre-wrap;overflow-wrap:anywhere;}
.case-actions {display:flex;justify-content:flex-end;flex-wrap:wrap;gap:8px;margin:16px 0;}
</style>
