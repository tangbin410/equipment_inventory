<template>
  <el-dialog
    :model-value="modelValue"
    :title="pairing ? '拼销记录' : '盘亏盘盈拼销'"
    width="min(1120px, 96vw)"
    top="5vh"
    :close-on-click-modal="!saving"
    :close-on-press-escape="!saving"
    :show-close="!saving"
    destroy-on-close
    @close="closeDialog"
  >
    <div v-loading="loading" class="pairing-body">
      <div v-if="loadError" class="pairing-load-error">
        <el-alert :title="loadError" type="error" :closable="false" show-icon />
        <el-button :disabled="saving" @click="loadCandidates(page, false, recommendationMode)">重新查询</el-button>
      </div>
      <template v-if="source && !loadError">
        <el-descriptions :column="2" border size="small" class="pairing-context">
          <el-descriptions-item label="当前机房">{{ source.room_name }}</el-descriptions-item>
          <el-descriptions-item label="当前设备">{{ source.device_name }}</el-descriptions-item>
          <el-descriptions-item label="设备类别">{{ recordDeviceCategory(source) || '未填写' }}</el-descriptions-item>
          <el-descriptions-item label="设备资产条码">{{ barcode(source) }}</el-descriptions-item>
        </el-descriptions>

        <template v-if="pairing">
          <el-alert :title="pairing.role === 'stale' ? '拼销来源已变化，请核实后撤销并重新拼销。' : pairing.cross_room ? '已确认同一设备，跨机房调拨和资料更新仍需处理。' : '已拼销为同一设备，原始盘点记录保留。'" type="warning" :closable="false" show-icon />
          <el-descriptions :column="2" border size="small" class="pairing-context">
            <el-descriptions-item label="原机房">{{ pairing.source_room_name }}</el-descriptions-item>
            <el-descriptions-item label="实际所在机房">{{ pairing.target_room_name }}</el-descriptions-item>
            <el-descriptions-item label="确认人">{{ pairing.actor_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="确认时间">{{ formatTime(pairing.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="确认依据" :span="2">{{ pairing.note }}</el-descriptions-item>
          </el-descriptions>
          <el-table v-if="pairSides.length" :data="pairSides" border class="pairing-table" :header-cell-style="{textAlign:'center'}">
            <el-table-column label="记录" width="80"><template #default="{row}">{{ row.record_type === 'missing' ? '盘亏' : '盘盈' }}</template></el-table-column>
            <el-table-column prop="room_name" label="机房" min-width="190" />
            <el-table-column prop="device_name" label="网元／资源名称" min-width="180" />
            <el-table-column label="设备资产条码" min-width="170"><template #default="{row}">{{ barcode(row) }}</template></el-table-column>
            <el-table-column label="型号" min-width="150"><template #default="{row}">{{ model(row) }}</template></el-table-column>
          </el-table>
          <el-form label-position="top" class="pairing-note" :disabled="saving">
            <el-form-item label="撤销原因">
              <el-input v-model="cancelNote" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="填写原因；撤销后恢复双方未拼销的展示" />
            </el-form-item>
          </el-form>
        </template>

        <template v-else>
          <el-alert title="候选仅供参考，请核实确为同一台设备后再拼销。机柜条码不能作为设备身份依据。" type="info" :closable="false" show-icon />
          <el-alert v-if="source.can_pair === false" :title="source.pairing_unavailable_reason || '当前记录暂不能拼销，请核实任务和记录状态。'" type="warning" :closable="false" class="pairing-notice" />
          <el-form class="pairing-filters" label-position="top" :disabled="saving || loading" @submit.prevent="loadCandidates()">
            <el-form-item label="机房名称"><el-input v-model="filters.room_name" clearable placeholder="全部机房，可模糊查询" /></el-form-item>
            <el-form-item label="设备类别"><el-input v-model="filters.device_category" clearable placeholder="精确类别，如 PTN" /></el-form-item>
            <el-form-item label="关键字"><el-input v-model="filters.keyword" clearable placeholder="名称、设备资产条码、型号" /></el-form-item>
            <div class="pairing-filter-actions"><el-checkbox v-model="filters.same_room">仅本机房</el-checkbox><el-button type="primary" :loading="loading" @click="loadCandidates()">查询</el-button></div>
          </el-form>
          <p class="pairing-hint">{{ source.record_type === 'missing' ? '查找盘盈' : '查找盘亏' }} · {{ recommendationMode ? '优先推荐最多 15 条；没有合适记录时，请输入条件查询。' : '查询结果仅包含各机房最近一次已完成、尚未拼销的记录。' }}</p>
          <el-table :data="candidates" border highlight-current-row row-key="id" class="pairing-table" :max-height="310" :header-cell-style="{textAlign:'center'}" :empty-text="recommendationMode ? '暂无条码或名称匹配的推荐，请输入条件查询' : '没有符合查询条件的候选，请调整条件后查询'" @row-click="selectCandidate">
            <el-table-column label="选择" width="62" align="center"><template #default="{row}"><el-radio :model-value="selected?.id" :value="row.id" :disabled="loading || saving || source.can_pair === false" @change="selectCandidate(row)"><span class="pairing-radio-label">选择</span></el-radio></template></el-table-column>
            <el-table-column prop="room_name" label="机房" min-width="190" show-overflow-tooltip />
            <el-table-column label="设备类别" width="105"><template #default="{row}">{{ recordDeviceCategory(row) || '-' }}</template></el-table-column>
            <el-table-column prop="device_name" label="网元／资源名称" min-width="190" show-overflow-tooltip />
            <el-table-column label="设备资产条码" min-width="170"><template #default="{row}">{{ barcode(row) }}</template></el-table-column>
            <el-table-column label="型号" min-width="150" show-overflow-tooltip><template #default="{row}">{{ model(row) }}</template></el-table-column>
            <el-table-column label="机柜位" width="100"><template #default="{row}">{{ row.record_type === 'missing' ? row.expected_rack_no || '-' : row.rack_no || '-' }}</template></el-table-column>
            <el-table-column prop="match_reason" label="匹配提示" min-width="170" />
          </el-table>
          <div v-if="!recommendationMode && total" class="pairing-pagination"><span>共 {{ total }} 条</span><el-pagination :current-page="page" :page-size="pageSize" :total="total" :disabled="loading || saving" layout="prev, pager, next" @current-change="loadCandidates($event, false)" /></div>
          <template v-if="selected">
            <el-alert :title="selectionNotice" :type="crossRoom ? 'warning' : 'info'" :closable="false" show-icon class="pairing-notice" />
            <el-descriptions :column="2" border size="small" class="pairing-context">
              <el-descriptions-item label="盘亏设备">{{ missing?.device_name }}</el-descriptions-item>
              <el-descriptions-item label="盘盈设备">{{ surplus?.device_name }}</el-descriptions-item>
              <el-descriptions-item label="盘亏设备条码">{{ missing ? barcode(missing) : '-' }}</el-descriptions-item>
              <el-descriptions-item label="盘盈设备条码">{{ surplus ? barcode(surplus) : '-' }}</el-descriptions-item>
            </el-descriptions>
          </template>
          <el-form label-position="top" class="pairing-note" :disabled="saving || loading || source.can_pair === false">
            <el-form-item label="确认依据（必填）">
              <el-input v-model="note" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="说明如何确认是同一设备，例如网管身份、序列号或现场照片；多条码请核对整组归属" />
            </el-form-item>
          </el-form>
        </template>
      </template>
    </div>
    <template #footer>
      <el-button :disabled="saving" @click="closeDialog">关闭</el-button>
      <el-button v-if="pairing" type="danger" plain :loading="saving" :disabled="loading || loadError || !cancelNote.trim()" @click="cancelPairing">撤销拼销</el-button>
      <el-button v-else type="primary" :loading="saving" :disabled="loading || loadError || !missing?.fingerprint || !surplus?.fingerprint || !note.trim() || source?.can_pair === false" @click="confirmPairing">确认同一设备并拼销</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import {computed, onBeforeUnmount, reactive, ref, shallowRef, watch} from 'vue';
import {ElMessage} from 'element-plus';
import {apiGet, apiPost} from '../api';
import {recordDeviceCategory} from '../inventoryPairing';
import {formatTime} from '../utils';
import type {InventoryPairing, InventoryPairingRecord, TaskRecord} from '../types';

const props = defineProps<{modelValue:boolean; record:TaskRecord|null}>();
const emit = defineEmits<{'update:modelValue':[value:boolean];saved:[]}>();
const loading=ref(false), saving=ref(false), loadError=ref('');
const source=shallowRef<InventoryPairingRecord|null>(null), selected=shallowRef<InventoryPairingRecord|null>(null);
const candidates=shallowRef<InventoryPairingRecord[]>([]), pairing=shallowRef<InventoryPairing|null>(null);
const filters=reactive({room_name:'',device_category:'',keyword:'',same_room:false});
const page=ref(1), total=ref(0), pageSize=15, recommendationMode=ref(true);
let appliedFilters={...filters};
const note=ref(''), cancelNote=ref('');
let requestSequence=0;
const missing=computed(()=>source.value?.record_type==='missing' ? source.value : selected.value?.record_type==='missing' ? selected.value : null);
const surplus=computed(()=>source.value?.record_type==='surplus' ? source.value : selected.value?.record_type==='surplus' ? selected.value : null);
const crossRoom=computed(()=>Boolean(missing.value && surplus.value && missing.value.task_id!==surplus.value.task_id));
const selectionNotice=computed(()=>crossRoom.value
  ? `跨机房拼销：${missing.value?.room_name} → ${surplus.value?.room_name}。合并记录归实际找到设备的机房；原机房保留迁出提示，综资调整与资产调拨仍需完成。`
  : '同机房拼销：合并为一条当前有效记录，两条原始盘点记录保留；拼销不代表三方资料已一致。');
const pairSides=computed(()=>[pairing.value?.missing,pairing.value?.surplus].filter((row):row is InventoryPairingRecord=>Boolean(row)));

function barcode(row:InventoryPairingRecord) {
  return row.record_type==='missing' ? row.expected_barcode || '未填写' : row.asset_no_barcode ? '无条码' : row.asset_barcodes || '未填写';
}
function model(row:InventoryPairingRecord) {return (row.record_type==='missing' ? row.expected_model : row.actual_model) || '未填写';}
function selectCandidate(row:InventoryPairingRecord) {
  if (loading.value || saving.value || loadError.value || source.value?.can_pair===false || !candidates.value.some(candidate=>candidate.id===row.id)) return;
  selected.value=row;
}
function reset() {
  source.value=null; selected.value=null; candidates.value=[]; pairing.value=null;
  note.value=''; cancelNote.value=''; loadError.value=''; loading.value=false;
  page.value=1; total.value=0; recommendationMode.value=true;
  Object.assign(filters,{room_name:'',device_category:'',keyword:'',same_room:false});
  appliedFilters={...filters};
}
watch(()=>[props.modelValue,props.record?.id] as const,([visible])=>{
  requestSequence+=1; reset();
  if (visible && props.record) void loadCandidates(1, true, true);
},{immediate:true});
onBeforeUnmount(()=>{requestSequence+=1;});
async function loadCandidates(requestedPage=1, applyFilters=true, recommendedOnly=false) {
  if (!props.modelValue || !props.record || saving.value) return;
  const queryFilters=applyFilters ? {...filters} : appliedFilters;
  if (!recommendedOnly && !queryFilters.room_name.trim() && !queryFilters.device_category.trim() && !queryFilters.keyword.trim() && !queryFilters.same_room) {
    ElMessage.info('请先输入机房、设备类别、关键字，或选择“仅本机房”后查询');
    return;
  }
  appliedFilters=queryFilters;
  page.value=requestedPage; recommendationMode.value=recommendedOnly;
  const sequence=++requestSequence, id=props.record.id;
  loading.value=true; loadError.value=''; selected.value=null; candidates.value=[];
  try {
    const response=await apiGet<{source:InventoryPairingRecord;candidates:InventoryPairingRecord[];pairing?:InventoryPairing|null;total:number;page:number;recommended_only:boolean}>('/admin/inventory-pairs/candidates',{params:{record_id:id,...appliedFilters,recommended_only:recommendedOnly,page:requestedPage,page_size:pageSize}});
    if (sequence!==requestSequence || !props.modelValue || props.record?.id!==id) return;
    source.value=response.source; candidates.value=response.candidates;
    total.value=response.total; page.value=response.page; recommendationMode.value=response.recommended_only;
    pairing.value=response.pairing || response.source.pairing || null;
  } catch {
    if (sequence!==requestSequence) return;
    source.value=null; pairing.value=null; total.value=0; loadError.value='拼销资料读取失败，请重新查询后操作。';
  } finally {if (sequence===requestSequence) loading.value=false;}
}
async function confirmPairing() {
  if (saving.value || loading.value || loadError.value || !missing.value?.fingerprint || !surplus.value?.fingerprint || !note.value.trim() || source.value?.can_pair===false) return;
  saving.value=true;
  const sequence=requestSequence;
  try {
    await apiPost('/admin/inventory-pairs',{
      missing_record_id:missing.value.id,surplus_record_id:surplus.value.id,
      missing_fingerprint:missing.value.fingerprint,surplus_fingerprint:surplus.value.fingerprint,note:note.value.trim(),
    });
    if (sequence!==requestSequence) return;
    ElMessage.success('拼销已保存'); emit('saved'); emit('update:modelValue',false);
  } catch {
    if (sequence!==requestSequence) return;
    selected.value=null; candidates.value=[];
    loadError.value='拼销未保存，请重新查询资料后再操作。';
  } finally {saving.value=false;}
}
async function cancelPairing() {
  if (saving.value || loading.value || loadError.value || !pairing.value || !cancelNote.value.trim()) return;
  saving.value=true;
  const sequence=requestSequence;
  try {
    await apiPost(`/admin/inventory-pairs/${pairing.value.id}/cancel`,{note:cancelNote.value.trim()});
    if (sequence!==requestSequence) return;
    ElMessage.success('拼销已撤销'); emit('saved'); emit('update:modelValue',false);
  } catch {
    if (sequence!==requestSequence) return;
    loadError.value='撤销未完成，请重新查询拼销状态后再操作。';
  } finally {saving.value=false;}
}
function closeDialog() {
  if (saving.value) return;
  requestSequence+=1; emit('update:modelValue',false);
}
</script>

<style scoped>
.pairing-body {min-height:100px;max-height:calc(88vh - 120px);overflow-y:auto;}
.pairing-context {margin:14px 0;}
.pairing-filters {display:grid;grid-template-columns:1fr 1fr 2fr auto;gap:14px;align-items:end;margin-top:16px;}
.pairing-filters :deep(.el-form-item) {margin-bottom:0;}
.pairing-filter-actions {display:flex;align-items:center;gap:10px;min-height:32px;}
.pairing-table {margin:12px 0;}
.pairing-pagination {display:flex;align-items:center;justify-content:flex-end;gap:12px;margin:12px 0;font-size:13px;color:var(--muted);}
.pairing-hint {font-size:13px;color:var(--muted);line-height:1.7;margin:12px 0;}
.pairing-notice,.pairing-note {margin-top:14px;}
.pairing-load-error {display:flex;gap:12px;align-items:center;}
.pairing-radio-label {position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);}
@media (max-width:760px) {.pairing-filters {grid-template-columns:1fr 1fr;}.pairing-load-error {flex-direction:column;align-items:stretch;}}
</style>
