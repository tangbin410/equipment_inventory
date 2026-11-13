<template>
  <PageHeader :icon="OfficeBuilding" title="机房建模" desc="选择最近一次已完成盘点的机房，按现场柜位与机架编号生成模型" />

  <div class="room-query">
    <el-form-item label="查询字段">
      <el-select v-model="queryField" @change="queryText = ''">
        <el-option label="全部已完成机房" value="all" />
        <el-option label="机房名称" value="room_name" />
        <el-option label="领取人" value="claimed_name" />
      </el-select>
    </el-form-item>
    <el-form-item class="query-content" label="查询内容">
      <el-input v-model="queryText" clearable :disabled="queryField === 'all'" :placeholder="queryField === 'all' ? '全部查询无需输入' : queryField === 'room_name' ? '请输入机房名称' : '请输入领取人姓名'" @keyup.enter="queryRooms" />
    </el-form-item>
    <el-button type="primary" :loading="queryLoading" @click="queryRooms">查询机房</el-button>
  </div>

  <div class="room-selection">
    <div>
      <strong>{{ selectedTask?.room_name || '未选择机房' }}</strong>
      <span v-if="selectedTask">地点编码：{{ selectedTask.room_code || '未填写' }} · 领取人：{{ claimant(selectedTask) || '未记录' }} · 完成时间：{{ formatTime(taskTime(selectedTask)) }}</span>
      <span v-else>查询后单选一个已完成机房，每个机房仅使用最近一次已完成记录。</span>
    </div>
    <el-tag v-if="modelData" type="success" effect="plain">模型已生成</el-tag>
  </div>

  <div class="model-sources">
    <strong>建模数据</strong>
    <el-checkbox-group v-model="sourceKinds" @change="onSourcesChange">
      <el-checkbox value="equipment">传输综合柜</el-checkbox>
      <el-checkbox value="odf">ODF</el-checkbox>
      <el-checkbox value="iodf">IODF</el-checkbox>
    </el-checkbox-group>
    <el-button type="primary" :loading="loading" :disabled="!selectedTask || !sourceKinds.length" @click="generateModel">{{ modelData ? '重新生成模型' : '生成模型' }}</el-button>
    <el-button class="model-export" :disabled="!modelData?.cabinets.length" @click="exportHtml"><el-icon><Download /></el-icon>导出 HTML</el-button>
    <span v-if="!sourceKinds.length" class="source-warning">请至少选择一类建模数据。</span>
    <el-popover placement="bottom-end" :width="420" trigger="click">
      <template #reference><el-button class="model-help" link>建模说明</el-button></template>
      <p>模型按现场排位编号展示，不代表机房实测布局。</p>
      <p>尺寸统一使用 mm（宽×深×高）：半深柜 600×300×2200，全深柜 600×600×2200；传输综合柜安装容量按 47U（1U＝44.45 mm）。</p>
      <p>设备 U 位未采集，柜内设备高度与排列顺序仅供示意。ODF／IODF 沿用同一排位规则，以不同外观区分。俯视时每排上半格是 A 面，下半格是 B 面，所有机房与各排顺序相同。</p>
    </el-popover>
  </div>

  <el-alert v-if="errorMessage" class="model-note" type="error" :closable="false" :title="errorMessage" />

  <template v-if="modelData">
    <div class="metric-grid model-metrics">
      <div v-for="item in metrics" :key="item.label" class="metric-card"><div class="metric-label">{{ item.label }}</div><div class="metric-value">{{ item.value }}</div></div>
    </div>

    <SectionTitle :icon="View" title="机房模型" muted="拖动旋转，滚轮缩放；点击对象查看资料" />
    <div ref="modelViewerRef" class="model-viewer" :class="{ 'model-expanded': fullscreenFallback }">
    <div class="model-controls">
      <el-radio-group v-model="view" @change="applyView">
        <el-radio-button value="overview">总览</el-radio-button>
        <el-radio-button value="top">俯视</el-radio-button>
        <el-radio-button value="front">正面</el-radio-button>
        <el-radio-button value="back">背面</el-radio-button>
      </el-radio-group>
      <el-select v-model="rowFilter" class="row-filter" placeholder="全部排" clearable :teleported="false" @change="applyRow">
        <el-option v-for="row in modelData.rows" :key="row" :label="`${row} 排`" :value="row" />
      </el-select>
      <el-checkbox v-model="showLabels" @change="applyLabels">显示编号</el-checkbox>
      <el-checkbox v-model="showDevices" @change="applyDevices">显示设备示意</el-checkbox>
      <el-button @click="resetView">重置视图</el-button>
      <el-button class="fullscreen-toggle" :class="{ 'is-fullscreen': isFullscreen }" type="primary" @click="toggleFullscreen"><el-icon><component :is="isFullscreen ? Close : FullScreen" /></el-icon>{{ isFullscreen ? '退出全屏' : '全屏查看' }}</el-button>
    </div>

    <div class="model-layout">
      <div class="model-stage">
        <canvas v-if="modelData.cabinets.length" ref="canvasRef" aria-label="机房机柜三维模型，使用下方对象列表也可查看资料" />
        <el-empty v-else description="当前所选数据没有可定位的机柜 / 机架，请核对盘点排位编号。" />
        <div v-if="modelData.cabinets.length" class="model-legend"><span class="equipment-dot" />传输综合柜 <span class="odf-dot" />ODF <span class="iodf-dot" />IODF <span class="issue-dot" />异常 / 待核实</div>
      </div>
      <aside class="object-detail">
        <div class="detail-title">对象资料</div>
        <template v-if="selectedObject">
          <div class="object-heading"><strong>{{ selectedObject.code }}</strong><el-tag size="small" effect="plain">{{ kindLabel(selectedObject.kind) }}</el-tag></div>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="排位">{{ selectedObject.row }} 排 · {{ selectedObject.index }} 位</el-descriptions-item>
            <el-descriptions-item label="面向">{{ faceLabel(selectedObject.face) }}</el-descriptions-item>
            <el-descriptions-item label="尺寸（MM）">{{ selectedObject.face === 'full' ? '600×600×2200' : '600×300×2200' }}</el-descriptions-item>
            <el-descriptions-item label="机柜 / 机架条码"><span class="barcode-text">{{ selectedObject.barcodes.join('、') || (selectedObject.devices.some(device => device.cabinetBarcodes.length) ? '见下方设备关联条码' : '未采集') }}</span></el-descriptions-item>
            <el-descriptions-item v-if="selectedObject.kind === 'equipment'" label="设备数">{{ selectedObject.devices.length }}</el-descriptions-item>
            <el-descriptions-item v-else label="用途">{{ selectedObject.kind === 'odf' ? '光缆配线' : '光纤成端' }}</el-descriptions-item>
          </el-descriptions>
          <el-alert v-if="selectedObject.pendingOverlap" class="object-notice" title="该位置存在占位冲突，需现场核实。" type="warning" :closable="false" />
          <p v-for="issue in selectedObject.issues" :key="issue" class="object-issue">{{ issue }}</p>
          <p v-if="selectedObject.kind === 'equipment'" class="detail-hint">柜内设备按 47U 示意排列，未采集实际 U 位。</p>
          <div v-if="selectedObject.devices.length" class="device-list">
            <div v-for="device in selectedObject.devices" :key="device.id" class="device-item" :class="{ 'active-device': activeDeviceId === String(device.id) }">
              <strong>{{ device.name || '未命名设备' }}</strong>
              <span>{{ device.category || '未填写类别' }} · {{ device.model || '未填写型号' }}</span>
              <span class="barcode-text">资产条码：{{ device.barcode || '未采集' }}</span>
              <span>所属机柜位：{{ device.positions.join('、') || '未采集' }}</span>
              <span v-if="device.positions.length > 1 && device.cabinetBarcodes.length" class="barcode-text">该设备关联的机柜条码：{{ device.cabinetBarcodes.join('、') }}</span>
            </div>
          </div>
          <p v-else class="detail-hint">{{ selectedObject.kind === 'equipment' ? '此机柜没有关联的设备记录。' : '机架按独立盘点明细建模，配线面板为示意。' }}</p>
        </template>
        <el-empty v-else :image-size="64" description="点击模型对象或下方列表查看资料" />
      </aside>
    </div>
    </div>

    <SectionTitle :icon="Search" title="对象与设备查询" :muted="`查询范围：${selectedTask?.room_name || ''}`" />
    <div class="object-search"><el-input v-model="keyword" clearable placeholder="模糊查询柜位、机架编号、设备名称、型号或条码" :prefix-icon="Search" @input="objectPage = 1" /><span>仅查询本次已生成模型的数据</span></div>
    <div class="table-wrap">
      <el-table :data="objectPageRows" row-key="key" border stripe :max-height="330" empty-text="没有符合条件的对象或设备" @row-click="selectSearchResult">
        <el-table-column prop="type" label="类型" width="120" />
        <el-table-column prop="name" label="对象 / 设备名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="position" label="机架位置" min-width="130" show-overflow-tooltip />
        <el-table-column prop="barcode" label="资产条码" min-width="190" show-overflow-tooltip />
        <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" @click.stop="selectSearchResult(row)">定位查看</el-button></template></el-table-column>
      </el-table>
    </div>
    <el-pagination v-if="objectMatches.length" v-model:current-page="objectPage" :page-size="20" :total="objectMatches.length" layout="total, prev, pager, next" class="model-pagination" />

    <SectionTitle :icon="Warning" title="模型异常" muted="异常仅提示数据或排位问题，请结合现场盘点结果核实" />
    <div v-if="modelData.issues.length" class="issue-list">
      <div v-for="issue in issuePageRows" :key="issue.id" class="issue-item">
        <strong>{{ issue.title }}</strong><p>{{ issue.detail }}</p>
        <div v-if="issue.objectIds.length" class="issue-object-links"><el-button v-for="id in issue.objectIds" :key="id" link type="primary" @click="selectObject(id)">{{ objectCode(id) }}</el-button></div>
      </div>
      <el-pagination v-model:current-page="issuePage" :page-size="10" :total="modelData.issues.length" layout="total, prev, pager, next" class="model-pagination" />
    </div>
    <el-empty v-else :image-size="48" description="当前模型未发现排位异常" />

  </template>
  <el-empty v-else :description="loading ? '正在读取所选机房数据并生成模型…' : emptyMessage" />

  <el-dialog v-model="pickerOpen" title="选择已完成盘点的机房" width="min(1080px, 96vw)">
    <p class="picker-note">查询到 {{ candidates.length }} 个机房，每个机房仅保留最近一次已完成记录；每次选择一个机房。</p>
    <el-table v-loading="queryLoading" :data="candidatePageRows" row-key="id" border stripe height="380" highlight-current-row empty-text="没有查到符合条件的已完成机房" @row-click="pickTask">
      <el-table-column label="选择" width="70" align="center"><template #default="{ row }"><el-radio v-model="pickedId" :value="row.id" :aria-label="`选择${row.room_name}`"><span /></el-radio></template></el-table-column>
      <el-table-column prop="room_name" label="机房名称" min-width="260" show-overflow-tooltip />
      <el-table-column label="领取人" width="115"><template #default="{ row }">{{ claimant(row) || '-' }}</template></el-table-column>
      <el-table-column label="盘点完成时间" width="170"><template #default="{ row }">{{ formatTime(taskTime(row)) }}</template></el-table-column>
      <el-table-column prop="area_name" label="片区" width="110" />
      <el-table-column prop="room_code" label="地点编码" min-width="170" show-overflow-tooltip />
    </el-table>
    <el-pagination v-model:current-page="candidatePage" :page-size="20" :total="candidates.length" layout="total, prev, pager, next" class="model-pagination" />
    <template #footer><el-button @click="pickerOpen = false">取消</el-button><el-button type="primary" :disabled="pickedId === null || queryLoading" @click="confirmRoom">确定机房</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue';
import { Close, Download, FullScreen, OfficeBuilding, Search, View, Warning } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import PageHeader from '../components/PageHeader.vue';
import SectionTitle from '../components/SectionTitle.vue';
import { apiGet } from '../api';
import type { Task } from '../types';
import { formatTime } from '../utils';
import { buildRoomModel, type ModelKind, type RoomModelData, type RoomModelReport } from '../roomModel';
import RoomRenderer from '../roomModelRenderer';
import { buildRoomModelHtml } from '../roomModelExport';

type ModelView = 'overview' | 'top' | 'front' | 'back';
type SearchResult = { key: string; objectId: string; deviceId: string | null; type: string; name: string; position: string; barcode: string; searchText: string };

const queryField = ref('all'), queryText = ref(''), queryLoading = ref(false), pickerOpen = ref(false);
const candidates = shallowRef<Task[]>([]), candidatePage = ref(1), pickedId = ref<number | null>(null), selectedTask = shallowRef<Task | null>(null);
const candidatePageRows = computed(() => candidates.value.slice((candidatePage.value - 1) * 20, candidatePage.value * 20));
const sourceKinds = ref<ModelKind[]>(['equipment', 'odf', 'iodf']);
const modelData = shallowRef<RoomModelData | null>(null), loading = ref(false), errorMessage = ref('');
const emptyMessage = ref('请先查询并选择一个已完成盘点的机房，再生成模型。');
const canvasRef = ref<HTMLCanvasElement | null>(null), view = ref<ModelView>('overview'), rowFilter = ref('');
const modelViewerRef = ref<HTMLDivElement | null>(null), isFullscreen = ref(false), fullscreenFallback = ref(false);
const showLabels = ref(true), showDevices = ref(true), selectedObjectId = ref<string | null>(null), activeDeviceId = ref<string | null>(null);
const keyword = ref(''), objectPage = ref(1), issuePage = ref(1);
let renderer: RoomRenderer | null = null;
let querySequence = 0, generationSequence = 0;

const selectedObject = computed(() => modelData.value?.cabinets.find(object => object.id === selectedObjectId.value) || null);
const metrics = computed(() => {
  const stats = modelData.value?.stats;
  return [
    { label: '柜位 / 机架编号', value: stats?.objectCount || 0 },
    { label: '设备记录', value: stats?.deviceCount || 0 },
    { label: '已关联设备', value: stats?.mappedDeviceCount || 0 },
    { label: '未定位记录', value: stats?.unplacedCount || 0 },
    { label: '模型异常', value: stats?.issueCount || 0 },
  ];
});
const objectMatches = computed<SearchResult[]>(() => {
  const query = keyword.value.trim().toLowerCase();
  return (modelData.value?.cabinets || []).flatMap(object => {
    const position = `${object.code} · ${object.row} 排 ${object.index} 位 · ${faceLabel(object.face)}`;
    const rows: SearchResult[] = [{ key: `object:${object.id}`, objectId: object.id, deviceId: null, type: kindLabel(object.kind), name: object.code, position, barcode: object.barcodes.join('、'), searchText: [position, kindLabel(object.kind), ...object.barcodes].join(' ') }];
    for (const device of object.devices) rows.push({ key: `device:${object.id}:${device.id}`, objectId: object.id, deviceId: String(device.id), type: '设备', name: device.name, position, barcode: device.barcode, searchText: [position, kindLabel(object.kind), device.name, device.category, device.model, device.barcode, ...device.positions, ...device.cabinetBarcodes].join(' ') });
    return rows.filter(row => !query || row.searchText.toLowerCase().includes(query));
  });
});
const objectPageRows = computed(() => objectMatches.value.slice((objectPage.value - 1) * 20, objectPage.value * 20));
const issuePageRows = computed(() => modelData.value?.issues.slice((issuePage.value - 1) * 10, issuePage.value * 10) || []);

function taskTime(task: Task): string { return task.field_completed_at || task.completed_at || task.created_at || ''; }
function claimant(task: Task): string { return task.claimed_name || task.assigned_name || ''; }
function kindLabel(kind: string): string { return kind === 'equipment' ? '传输综合柜' : kind === 'odf' ? 'ODF' : kind === 'iodf' ? 'IODF' : kind; }
function faceLabel(face: string): string { return face === 'full' ? '全深' : `${face} 面（半深）`; }
function objectCode(id: string): string { return modelData.value?.cabinets.find(object => object.id === id)?.code || id; }

async function queryRooms() {
  const sequence = ++querySequence;
  const field = queryField.value, query = queryText.value.trim().toLowerCase();
  queryLoading.value = true;
  candidates.value = [];
  candidatePage.value = 1;
  pickedId.value = null;
  pickerOpen.value = true;
  try {
    const tasks = await apiGet<Task[]>('/tasks');
    if (sequence !== querySequence) return;
    const completed = tasks.filter(task => task.status === 'done').sort((a, b) => (Date.parse(taskTime(b)) || 0) - (Date.parse(taskTime(a)) || 0) || b.id - a.id);
    const codesByName = new Map<string, Set<string>>();
    for (const task of completed) {
      const name = task.room_name?.trim().toLowerCase() || '', code = task.room_code?.trim().toLowerCase() || '';
      if (!name || !code) continue;
      if (!codesByName.has(name)) codesByName.set(name, new Set());
      codesByName.get(name)!.add(code);
    }
    const latest = new Map<string, Task>();
    for (const task of completed) {
      const name = task.room_name?.trim().toLowerCase() || '', knownCodes = codesByName.get(name);
      const code = task.room_code?.trim().toLowerCase() || (knownCodes?.size === 1 ? [...knownCodes][0] : '');
      const roomKey = code ? `code:${code}` : name ? `name:${name}` : `task:${task.id}`;
      if (!latest.has(roomKey)) latest.set(roomKey, task);
    }
    candidates.value = [...latest.values()].filter(task => field === 'all' || String(field === 'claimed_name' ? claimant(task) : task.room_name).toLowerCase().includes(query));
    if (selectedTask.value && candidates.value.some(task => task.id === selectedTask.value?.id)) pickedId.value = selectedTask.value.id;
  } catch {
    if (sequence === querySequence) candidates.value = [];
  } finally {
    if (sequence === querySequence) queryLoading.value = false;
  }
}

function pickTask(task: Task) { pickedId.value = task.id; }
function confirmRoom() {
  const task = candidates.value.find(item => item.id === pickedId.value);
  if (!task) return;
  selectedTask.value = task;
  clearModel('已选择机房，请点击“生成模型”。');
  pickerOpen.value = false;
}
function destroyRenderer() { renderer?.destroy(); renderer = null; }
function syncFullscreen() { isFullscreen.value = fullscreenFallback.value || Boolean(modelViewerRef.value && document.fullscreenElement === modelViewerRef.value); }
async function toggleFullscreen() {
  const viewer = modelViewerRef.value;
  if (!viewer) return;
  if (document.fullscreenElement === viewer) {
    await document.exitFullscreen().catch(() => {});
  } else if (fullscreenFallback.value) {
    fullscreenFallback.value = false;
  } else {
    try { await viewer.requestFullscreen(); }
    catch { fullscreenFallback.value = true; }
  }
  syncFullscreen();
}
function onFullscreenKey(event: KeyboardEvent) {
  if (event.key === 'Escape' && fullscreenFallback.value) {
    fullscreenFallback.value = false;
    syncFullscreen();
  }
}
function clearModel(message: string) {
  generationSequence += 1;
  loading.value = false;
  if (modelViewerRef.value && document.fullscreenElement === modelViewerRef.value) void document.exitFullscreen().catch(() => {});
  fullscreenFallback.value = false;
  isFullscreen.value = false;
  destroyRenderer();
  modelData.value = null;
  errorMessage.value = '';
  selectedObjectId.value = null;
  activeDeviceId.value = null;
  keyword.value = '';
  objectPage.value = 1;
  issuePage.value = 1;
  rowFilter.value = '';
  emptyMessage.value = message;
}
function onSourcesChange() { clearModel(selectedTask.value ? '建模数据已变更，请重新生成模型。' : '请先查询并选择一个已完成盘点的机房，再生成模型。'); }

function exportHtml() {
  if (!modelData.value?.cabinets.length || !selectedTask.value) return;
  try {
    const html = buildRoomModelHtml(modelData.value, {
      roomName: selectedTask.value.room_name,
      roomCode: selectedTask.value.room_code,
      completedAt: taskTime(selectedTask.value),
    });
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
    const link = document.createElement('a');
    const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    link.href = url;
    link.download = `${selectedTask.value.room_name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_')}-机房模型-${day}.html`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    ElMessage.success('HTML 已导出，可独立打开查看模型');
  } catch {
    ElMessage.error('模型导出失败，请重新生成模型后重试');
  }
}

async function generateModel() {
  if (!selectedTask.value || !sourceKinds.value.length || loading.value) return;
  const taskId = selectedTask.value.id, kinds = [...sourceKinds.value];
  clearModel('正在生成模型…');
  const sequence = generationSequence;
  loading.value = true;
  try {
    const report = await apiGet<RoomModelReport>(`/admin/room-models/${taskId}`);
    if (sequence !== generationSequence || selectedTask.value?.id !== taskId) return;
    const data = buildRoomModel(report, kinds);
    modelData.value = data;
    await nextTick();
    if (sequence !== generationSequence) return;
    if (canvasRef.value) {
      renderer = new RoomRenderer(canvasRef.value, { onSelect: (id: string | null) => { selectedObjectId.value = id; activeDeviceId.value = null; } });
      renderer.setData(data);
      renderer.setView(view.value);
      renderer.setShowLabels(showLabels.value);
      renderer.setShowDevices(showDevices.value);
    }
  } catch {
    if (sequence !== generationSequence) return;
    destroyRenderer();
    modelData.value = null;
    errorMessage.value = '机房模型生成失败，请重试；如持续失败，请检查所选机房数据。';
    emptyMessage.value = '本次未生成模型，请重新生成。';
  } finally {
    if (sequence === generationSequence) loading.value = false;
  }
}

function applyView() { renderer?.setView(view.value); }
function applyRow() {
  renderer?.setRow(rowFilter.value || null);
  if (selectedObject.value && rowFilter.value && selectedObject.value.row !== rowFilter.value) {
    selectedObjectId.value = null;
    activeDeviceId.value = null;
    renderer?.select(null);
  }
}
function applyLabels() { renderer?.setShowLabels(showLabels.value); }
function applyDevices() { renderer?.setShowDevices(showDevices.value); }
function resetView() {
  view.value = 'overview';
  rowFilter.value = '';
  selectedObjectId.value = null;
  activeDeviceId.value = null;
  renderer?.setRow(null);
  renderer?.select(null);
  renderer?.setView('overview');
  renderer?.reset();
}
function selectObject(id: string, deviceId: string | null = null) {
  const object = modelData.value?.cabinets.find(item => item.id === id);
  if (!object) return;
  if (rowFilter.value && rowFilter.value !== object.row) {
    rowFilter.value = object.row;
    renderer?.setRow(object.row);
  }
  renderer?.select(id);
  selectedObjectId.value = id;
  activeDeviceId.value = deviceId;
}
function selectSearchResult(result: SearchResult) { selectObject(result.objectId, result.deviceId); }

onMounted(() => {
  document.addEventListener('fullscreenchange', syncFullscreen);
  document.addEventListener('keydown', onFullscreenKey);
});
onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', syncFullscreen);
  document.removeEventListener('keydown', onFullscreenKey);
  if (modelViewerRef.value && document.fullscreenElement === modelViewerRef.value) void document.exitFullscreen().catch(() => {});
  querySequence += 1; generationSequence += 1; destroyRenderer();
});
</script>

<style scoped>
.room-query, .model-sources, .model-controls, .object-search { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.room-query { padding: 18px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.room-query .el-form-item { margin: 0; }
.room-query .el-select { width: 180px; }
.query-content { flex: 1; min-width: 240px; }
.query-content :deep(.el-form-item__content) { min-width: 150px; }
.room-selection { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 4px; }
.room-selection > div { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; min-width: 0; }
.room-selection strong { color: var(--navy); font-size: 17px; }
.room-selection span, .picker-note, .detail-hint, .object-search > span { color: var(--muted); font-size: 13px; line-height: 1.7; }
.model-sources { padding: 12px 16px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.model-sources .el-checkbox-group { display: flex; gap: 22px; flex-wrap: wrap; }
.model-sources .el-checkbox { margin-right: 0; }
.model-help { margin-left: auto; color: var(--muted); }
.source-warning { color: var(--warning); font-size: 13px; }
.model-sources .model-export:not(.is-disabled) { color: #155fc0; border-color: #9fc6ef; background: #f4f9ff; }
.model-note { margin: 14px 0; }
.model-note :deep(.el-alert__description) { line-height: 1.7; }
.model-metrics { margin-top: 18px; }
.model-controls { margin-bottom: 12px; }
.model-controls .fullscreen-toggle { margin-left: auto; min-height: 38px; padding: 0 17px; border-radius: 10px; border: 1px solid #1767c3; background: #1767c3; color: #fff; box-shadow: 0 4px 12px rgba(23, 103, 195, .16); }
.model-controls .fullscreen-toggle:hover, .model-controls .fullscreen-toggle:focus-visible { background: #1157ab; border-color: #1157ab; color: #fff; }
.model-controls .fullscreen-toggle .el-icon { color: inherit; font-size: 18px; margin-right: 7px; }
.model-controls .fullscreen-toggle.is-fullscreen { background: #24475e; border-color: #24475e; }
.model-viewer:fullscreen, .model-expanded { display: flex; flex-direction: column; gap: 12px; width: 100%; height: 100%; padding: 20px; overflow: hidden; background: var(--bg); }
.model-expanded { position: fixed; inset: 0; z-index: 5000; }
.model-viewer:fullscreen .model-controls, .model-expanded .model-controls { flex: 0 0 auto; margin: 0; }
.model-viewer:fullscreen .model-layout, .model-expanded .model-layout { flex: 1; min-height: 0; grid-template-columns: minmax(0, 1fr) 330px; }
.model-viewer:fullscreen .model-stage, .model-expanded .model-stage { min-height: 0; }
.model-viewer:fullscreen .model-stage canvas, .model-expanded .model-stage canvas { min-height: 0; height: 100%; }
.model-viewer:fullscreen .object-detail, .model-expanded .object-detail { max-height: none; min-height: 0; }
.row-filter { width: 145px; }
.model-layout { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 14px; align-items: stretch; }
.model-stage { position: relative; min-width: 0; min-height: 530px; overflow: hidden; border: 1px solid var(--line); border-radius: 8px; background: #edf3f8; }
.model-stage canvas { display: block; width: 100%; height: 100%; min-height: 530px; touch-action: none; }
.model-stage > .el-empty { min-height: 530px; padding: 24px; }
.model-legend { position: absolute; left: 12px; bottom: 12px; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 8px 10px; border: 1px solid rgba(219, 232, 246, .85); border-radius: 6px; background: rgba(255, 255, 255, .92); color: #496079; font-size: 12px; pointer-events: none; }
.model-legend > span { width: 9px; height: 9px; margin-left: 7px; border-radius: 2px; }
.model-legend > span:first-child { margin-left: 0; }
.equipment-dot { background: #347ba6; }.odf-dot { background: #2f9c8d; }.iodf-dot { background: #9a73b9; }.issue-dot { background: #e5a336; }
.object-detail { min-width: 0; padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: #fff; overflow: auto; max-height: 650px; }
.detail-title { margin-bottom: 14px; color: var(--navy); font-size: 16px; font-weight: 700; }
.object-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
.object-notice { margin-top: 12px; }
.object-issue { margin: 8px 0; color: #b66b1a; font-size: 13px; line-height: 1.6; }
.barcode-text { overflow-wrap: anywhere; }
.device-list { display: grid; gap: 10px; }
.device-item { display: flex; flex-direction: column; gap: 5px; padding: 11px; border: 1px solid var(--line); border-radius: 6px; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.device-item > strong { color: var(--navy); font-size: 13px; }
.device-item > span { color: #60758b; }
.active-device { border-color: var(--primary); background: var(--primary-soft); }
.object-search { margin-bottom: 12px; }
.object-search .el-input { width: min(520px, 100%); }
.model-pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
.issue-list { border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 4px 16px 14px; }
.issue-item { padding: 13px 0; border-bottom: 1px solid var(--line); }
.issue-item:last-of-type { border-bottom: 0; }
.issue-item strong { font-size: 14px; color: #996016; }
.issue-item p { margin: 7px 0 0; color: #60758b; font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.issue-object-links { display: flex; flex-wrap: wrap; gap: 6px 15px; margin-top: 6px; }
.picker-note { margin-top: 0; }
@media (max-width: 1180px) { .model-layout { grid-template-columns: minmax(0, 1fr); }.object-detail { max-height: 430px; }.model-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 700px) { .room-query { align-items: stretch; }.room-query .el-form-item, .room-query > .el-button { width: 100%; }.room-query .el-select { width: 100%; }.query-content { min-width: 0; }.room-selection { align-items: flex-start; }.model-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }.model-stage, .model-stage canvas, .model-stage > .el-empty { min-height: 420px; }.model-controls { gap: 10px; }.model-pagination { justify-content: flex-start; overflow: auto; } }
@media (max-width: 800px) {
  .model-viewer:fullscreen, .model-expanded { padding: 12px; }
  .model-viewer:fullscreen .model-layout, .model-expanded .model-layout { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(160px, 1fr) minmax(120px, .6fr); }
}
</style>
