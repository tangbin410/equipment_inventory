<template>
  <PageHeader :icon="DataAnalysis" title="资产盘点数据" desc="查询现场原始反馈、管理员修正值并导出当前有效数据" />

  <div class="toolbar">
    <el-form-item class="span-2" label="查询字段">
      <el-select v-model="queryField" @change="onQueryFieldChange">
        <el-option v-for="item in queryOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
    </el-form-item>
    <el-form-item class="span-6" label="查询内容">
      <el-select v-if="queryField === 'area_name'" v-model="queryText" class="full-btn" placeholder="请选择片区">
        <el-option v-for="item in areaOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-else-if="queryField === 'field_stage'" v-model="queryText" class="full-btn" placeholder="请选择现场状态">
        <el-option v-for="item in stageOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-input v-else v-model="queryText" :disabled="queryField === '__all__'" :placeholder="queryField === '__all__' ? '全部查询无需输入' : `请输入${queryLabel}`" />
    </el-form-item>
    <div class="span-2">
      <el-button type="primary" class="full-btn" @click="runQuery">查询</el-button>
    </div>
    <div class="span-2">
      <el-button class="full-btn" :disabled="!selectedTasks.length" @click="downloadData">
        <el-icon><Download /></el-icon>下载数据
      </el-button>
    </div>
  </div>

  <div class="asset-selection-strip">
    <span>{{ selectedTasks.length ? `已选 ${selectedTasks.length} 个任务` : "未选择任务" }}</span>
    <em>{{ selectedTasks.length ? `设备 ${totalCount} 个 / 已盘 ${scannedCount} 个 / 未盘 ${pendingCount} 个` : "查询后在弹窗中勾选需要导出的任务" }}</em>
  </div>

  <template v-if="selectedTasks.length">
    <div class="metric-grid four">
      <div class="metric-card"><div class="metric-label">设备总数</div><div class="metric-value">{{ totalCount }}</div></div>
      <div class="metric-card"><div class="metric-label">已盘</div><div class="metric-value">{{ scannedCount }}</div></div>
      <div class="metric-card"><div class="metric-label">未盘</div><div class="metric-value">{{ pendingCount }}</div></div>
      <div class="metric-card"><div class="metric-label">进度</div><div class="metric-value">{{ scannedCount }}/{{ totalCount }}</div></div>
    </div>

    <SectionTitle :icon="DocumentChecked" title="资产盘点数据" />
    <div class="table-wrap">
      <el-table class="report-table report-device-table" border table-layout="fixed" :header-cell-style="{ textAlign: 'center' }" :data="rows.slice(0, 500)" height="420">
        <el-table-column prop="机房名称" label="机房" width="200">
          <template #default="{ row }">
            <el-tooltip :content="String(row['机房名称'] || '')" placement="top" :show-after="300">
              <span class="report-name">{{ row['机房名称'] }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="设备名称" label="设备" width="240">
          <template #default="{ row }">
            <el-tooltip :content="String(row['设备名称'] || '')" placement="top" :show-after="300">
              <span class="report-name">{{ row['设备名称'] }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="现场实物" align="center">
          <el-table-column v-for="col in displayColumns.filter((item) => ['现场设备型号', '现场资产条码', '现场机架号', '现场机柜条码'].includes(item.prop))" :key="col.prop" v-bind="col" :show-overflow-tooltip="col.prop.includes('机架号')">
            <template #default="{ row }">
              <el-tooltip v-if="col.prop.includes('条码')" :content="String(row[col.prop] || '')" placement="top" :show-after="300">
                <el-button v-if="isAssetBarcodeColumn(col.prop) && hasQueryableBarcode(row[col.prop])" class="report-barcode" link type="primary" @click="queryAssetBarcode(row[col.prop])">{{ row[col.prop] }}</el-button>
                <span v-else class="report-barcode">{{ row[col.prop] || "" }}</span>
              </el-tooltip>
              <el-tooltip v-else-if="col.prop.includes('设备型号')" :content="String(row[col.prop] || '')" placement="top" :show-after="300">
                <span class="report-name">{{ row[col.prop] || "" }}</span>
              </el-tooltip>
              <span v-else>{{ row[col.prop] || "" }}</span>
            </template>
          </el-table-column>
        </el-table-column>
        <el-table-column label="综资基准" align="center">
          <el-table-column v-for="col in displayColumns.filter((item) => ['综资设备型号', '综资资产条码', '综资机架号'].includes(item.prop))" :key="col.prop" v-bind="col" :show-overflow-tooltip="col.prop.includes('机架号')">
            <template #default="{ row }">
              <el-tooltip v-if="col.prop.includes('条码')" :content="String(row[col.prop] || '')" placement="top" :show-after="300">
                <el-button v-if="isAssetBarcodeColumn(col.prop) && hasQueryableBarcode(row[col.prop])" class="report-barcode" link type="primary" @click="queryAssetBarcode(row[col.prop])">{{ row[col.prop] }}</el-button>
                <span v-else class="report-barcode">{{ row[col.prop] || "" }}</span>
              </el-tooltip>
              <el-tooltip v-else-if="col.prop.includes('设备型号')" :content="String(row[col.prop] || '')" placement="top" :show-after="300">
                <span class="report-name">{{ row[col.prop] || "" }}</span>
              </el-tooltip>
              <span v-else>{{ row[col.prop] || "" }}</span>
            </template>
          </el-table-column>
        </el-table-column>
        <el-table-column label="盘点结果" align="center">
          <el-table-column v-for="col in displayColumns.filter((item) => ['盘点结论', '盘点时间', '盘亏原因', '现场备注'].includes(item.prop))" :key="col.prop" v-bind="col" show-overflow-tooltip>
            <template v-if="col.prop === '盘点结论'" #default="{ row }">
              <el-tag :type="recordResultTag(row['盘点结论'])" effect="plain">{{ row['盘点结论'] }}</el-tag>
            </template>
          </el-table-column>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="132">
          <template #default="{ row }">
            <div class="report-row-actions">
              <el-button
                size="small"
                type="primary"
                :disabled="row.__record.match_status === '未扫'"
                @click="openCorrection(row.__record)"
              >修正现场反馈</el-button>
              <el-tooltip v-if="permissions.updateBaseline" :content="resourceEditHint(row.__record)" placement="left" :show-after="300">
                <span class="report-action-trigger">
                  <el-button size="small" :disabled="!canEditResource(row.__record)" @click="openResourceCorrection(row.__record)">修改综资</el-button>
                </span>
              </el-tooltip>
              <el-tooltip :content="canCreateAdjustment(row.__record) ? '人工确认问题后加入整改台账，后续审核、下发' : '请待现场确认整个任务完成后再转整改'" placement="left" :show-after="300">
                <span class="report-action-trigger">
            <el-button v-if="permissions.writeGovernance" size="small" type="warning" :disabled="!canCreateAdjustment(row.__record)" @click="openAdjustment(row.__record)">转整改</el-button>
                </span>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <SectionTitle :icon="DocumentChecked" title="传输综合柜（随设备盘点）" />
    <div v-if="equipmentCabinetRows.length" class="table-wrap">
      <el-table class="report-table" border table-layout="fixed" :data="equipmentCabinetRows.slice(0, 500)" height="300">
        <el-table-column prop="机房名称" label="机房" min-width="220" show-overflow-tooltip />
        <el-table-column prop="机柜条码" label="机柜资产条码" width="182" class-name="report-barcode-cell" show-overflow-tooltip />
        <el-table-column prop="机架编号" label="设备机柜位" width="110" show-overflow-tooltip />
        <el-table-column prop="登记人" label="登记人" width="100" show-overflow-tooltip />
        <el-table-column prop="登记时间" label="登记时间" width="154" />
        <el-table-column prop="备注" label="备注" min-width="180" show-overflow-tooltip />
      </el-table>
    </div>
    <el-empty v-else :image-size="56" description="当前任务暂无可汇总的传输综合柜；汇总以设备盘点最新有效反馈为准。" />

    <div class="section-actions">
      <SectionTitle :icon="DocumentChecked" title="ODF / IODF 机架（单独盘点）" />
      <el-button type="primary" plain @click="openRackCorrection(null)">补录机架</el-button>
    </div>
    <div v-if="rackRows.length" class="table-wrap">
      <el-table class="report-table" border table-layout="fixed" :data="rackRows.slice(0, 500)" height="300">
        <el-table-column prop="机房名称" label="机房" min-width="220" show-overflow-tooltip />
        <el-table-column prop="机柜类型" label="机架类型" width="90" />
        <el-table-column prop="机架编号" label="资源名称 / 机架编号" min-width="190" show-overflow-tooltip />
        <el-table-column prop="机柜条码" label="机架资产条码" width="182" class-name="report-barcode-cell" show-overflow-tooltip />
        <el-table-column prop="登记人" label="登记人" width="100" show-overflow-tooltip />
        <el-table-column prop="登记时间" label="登记时间" width="154" />
        <el-table-column prop="备注" label="备注" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" fixed="right" width="145">
          <template #default="{ row }">
            <el-button size="small" type="primary" :disabled="voidingRackId !== null" @click="openRackCorrection(row.__rack)">更正</el-button>
            <el-button size="small" type="danger" plain :loading="voidingRackId === row.__rack.id" :disabled="voidingRackId !== null && voidingRackId !== row.__rack.id" @click="voidRack(row.__rack)">作废</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-empty v-else :image-size="56" description="当前任务尚未登记 ODF / IODF 机架。" />
  </template>
  <el-empty v-else description="请先查询任务，并在弹窗中勾选需要导出的范围。" />

  <el-dialog v-model="resultDialogVisible" title="查询结果" width="980px" class="result-dialog">
    <el-empty v-if="!candidates.length" description="没有查到相关任务。" />
    <template v-else>
      <div class="dialog-tip">查询到 {{ candidates.length }} 个任务，请勾选需要导出的数据范围。</div>
      <div class="table-wrap compact">
        <el-table border table-layout="auto" :data="candidates" height="360" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="48" />
          <el-table-column prop="room_name" label="机房" min-width="280" show-overflow-tooltip />
          <el-table-column prop="import_batch_id" label="导入批次" min-width="215" show-overflow-tooltip />
          <el-table-column prop="area_name" label="片区" min-width="100" />
          <el-table-column label="领取人" min-width="100">
            <template #default="{ row }">{{ row.assigned_name || "-" }}</template>
          </el-table-column>
          <el-table-column label="现场状态" min-width="112">
            <template #default="{ row }"><el-tag :type="statusTag(taskStage(row))" effect="plain">{{ taskStage(row) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="进度" min-width="90">
            <template #default="{ row }">{{ row.scanned || 0 }}/{{ row.total || 0 }}</template>
          </el-table-column>
        </el-table>
      </div>
    </template>
    <template #footer>
      <span class="dialog-footer-note">已选 {{ selectedTasks.length }} 个任务</span>
      <el-button @click="resultDialogVisible = false">关闭</el-button>
      <el-button type="primary" :disabled="!selectedTasks.length" @click="resultDialogVisible = false">确定</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="assetInfoDialog.open" title="资产信息" width="620px">
    <div v-if="assetInfoDialog.loading" class="asset-info-loading">正在查询资产信息...</div>
    <el-empty v-else-if="!assetInfoDialog.results.length" description="资产信息查询暂不可用，请检查后台连接后重试" />
    <div v-else class="asset-info-results">
      <div v-for="result in assetInfoDialog.results" :key="result.barcode" class="asset-info-result">
        <div class="asset-info-result-title">资产条码：{{ result.barcode }}</div>
        <el-empty v-if="!result.found" :image-size="48" description="当前资产信息表未找到该条码" />
        <el-descriptions v-else :column="1" border size="small">
          <el-descriptions-item v-for="(value, key) in result.fields" :key="key" :label="key">{{ value || "" }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </div>
  </el-dialog>

  <RecordCorrectionDialog
    v-model="correctionDialogVisible"
    :record="correctionRecord"
    @saved="handleCorrectionSaved"
  />
  <ResourceCorrectionDialog v-if="permissions.updateBaseline" v-model="resourceDialogVisible" :record="resourceRecord" @saved="refreshSelectedTasks" />
  <RecordAdjustmentDialog v-if="permissions.writeGovernance" v-model="adjustmentDialogVisible" :record="adjustmentRecord" />
  <RackCorrectionDialog v-model="rackDialogVisible" :rack="rackToCorrect" :tasks="selectedTasks" @saved="refreshSelectedTasks" />
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { DataAnalysis, DocumentChecked, Download } from "@element-plus/icons-vue";
import PageHeader from "../components/PageHeader.vue";
import RecordCorrectionDialog from "../components/RecordCorrectionDialog.vue";
import ResourceCorrectionDialog from "../components/ResourceCorrectionDialog.vue";
import RecordAdjustmentDialog from "../components/RecordAdjustmentDialog.vue";
import RackCorrectionDialog from "../components/RackCorrectionDialog.vue";
import SectionTitle from "../components/SectionTitle.vue";
import { apiDelete, apiGet, downloadBlob } from "../api";
import type { AssetInfoLookupItem, AssetInfoQueryResult, CabinetRecord, Task, TaskRecord } from "../types";
import { formatTime, inventoryDisplayColumns, inventoryRows, taskStage } from "../utils";
import { permissions } from "../permissions";

const summary = ref<Task[]>([]);
const resultDialogVisible = ref(false);
const queryField = ref("__all__");
const queryText = ref("");
const candidates = ref<Task[]>([]);
const selectedTasks = ref<Task[]>([]);
const correctionDialogVisible = ref(false);
const correctionRecord = ref<TaskRecord | null>(null);
const resourceDialogVisible = ref(false);
const resourceRecord = ref<TaskRecord | null>(null);
const adjustmentDialogVisible = ref(false);
const adjustmentRecord = ref<TaskRecord | null>(null);
const rackDialogVisible = ref(false);
const rackToCorrect = ref<CabinetRecord | null>(null);
const voidingRackId = ref<CabinetRecord["id"] | null>(null);
const assetInfoDialog = ref({ open: false, loading: false, results: [] as AssetInfoLookupItem[] });

const queryOptions = [
  { label: "全部查询", value: "__all__" },
  { label: "导入批次", value: "import_batch_id" },
  { label: "机房名称", value: "room_name" },
  { label: "任务名称", value: "task_name" },
  { label: "片区", value: "area_name" },
  { label: "领取人", value: "assigned_name" },
  { label: "现场状态", value: "field_stage" },
];
const stageOptions = ["待领取", "盘点中", "现场完成"];

const queryLabel = computed(() => queryOptions.find((item) => item.value === queryField.value)?.label || "查询内容");
const areaOptions = ["汇聚及核心", "南区", "北区", "西区"];
const rows = computed(() => {
  const result: Array<Record<string, unknown> & { __record: TaskRecord }> = [];
  selectedTasks.value.forEach((task) => {
    const taskRows = inventoryRows([task]);
    (task.records || []).forEach((record, index) => {
      result.push({ ...(taskRows[index] || {}), __record: record });
    });
  });
  return result;
});
const reportColumnWidths: Record<string, number> = {
  综资设备型号: 150, 现场设备型号: 150,
  综资资产条码: 176, 现场资产条码: 176, 现场机柜条码: 176,
  综资机架号: 94, 现场机架号: 94,
  盘点结论: 104, 盘点时间: 164, 盘亏原因: 130, 现场备注: 150,
};
const reportColumnLabels: Record<string, string> = {
  现场设备型号: "设备型号", 现场资产条码: "设备条码", 现场机架号: "机柜位", 现场机柜条码: "机柜条码",
  综资设备型号: "设备型号", 综资资产条码: "资产条码", 综资机架号: "机柜位",
};
const displayColumns = inventoryDisplayColumns.map(({ minWidth, ...column }) => ({
  ...column,
  label: reportColumnLabels[column.prop] || column.label,
  width: reportColumnWidths[column.prop] || minWidth,
}));
const inventoryRecords = computed(() => selectedTasks.value.flatMap((task) => task.records || []).filter((record) => (record.record_type || "normal") !== "surplus"));
const totalCount = computed(() => inventoryRecords.value.length);
const scannedCount = computed(() => inventoryRecords.value.filter((record: TaskRecord) => record.match_status !== "未扫").length);
const pendingCount = computed(() => inventoryRecords.value.filter((record: TaskRecord) => record.match_status === "未扫").length);
const cabinetRows = computed(() => {
  const seen = new Set<CabinetRecord["id"]>();
  return selectedTasks.value.flatMap((task) => (task.cabinets || [])
    .filter((cabinet: CabinetRecord) => {
      if (seen.has(cabinet.id)) return false;
      seen.add(cabinet.id);
      return true;
    })
    .map((cabinet: CabinetRecord) => ({
      __rack: cabinet,
      __kind: cabinet.cabinet_type,
      机房名称: task.room_name || cabinet.room_name || "",
      机柜类型: cabinet.cabinet_type_label || cabinet.cabinet_type || "",
      机柜条码: cabinet.cabinet_barcode || (cabinet.no_barcode ? "无条码" : ""),
      机架编号: cabinet.rack_no || cabinet.cabinet_name || "",
      登记来源: cabinet.source_label || cabinet.source || "",
      登记人: cabinet.created_by_name || "-",
      登记时间: formatTime(cabinet.scanned_at),
      备注: cabinet.notes || "",
    })));
});
const equipmentCabinetRows = computed(() => cabinetRows.value.filter((row) => row.__kind === "equipment"));
const rackRows = computed(() => cabinetRows.value.filter((row) => row.__kind === "odf" || row.__kind === "iodf"));

function taskValue(task: Task, field: string) {
  if (field === "__all__") return "";
  if (field === "field_stage") return taskStage(task);
  if (field === "area_name") return task.area_name || "汇聚及核心";
  if (field === "assigned_name") return task.assigned_name || "-";
  return String((task as unknown as Record<string, unknown>)[field] || "");
}

function onQueryFieldChange() {
  queryText.value = "";
}

function statusTag(status: string) {
  if (status.includes("完成")) return "success";
  if (status.includes("盘点")) return "primary";
  if (status.includes("待")) return "warning";
  return "info";
}

function recordResultTag(result: string) {
  if (result === "正常盘到") return "success";
  if (result === "现场未找到") return "danger";
  if (result === "现场新增") return "warning";
  return "info";
}

function isAssetBarcodeColumn(prop: string) {
  return prop === "现场资产条码" || prop === "综资资产条码";
}

function barcodeParts(value: unknown) {
  return String(value || "")
    .split(/[,，;；\n]+/)
    .map((item) => item.trim())
    .filter((item) => item && item !== "无条码");
}

function hasQueryableBarcode(value: unknown) {
  return barcodeParts(value).length > 0;
}

async function queryAssetBarcode(value: unknown) {
  const barcodes = barcodeParts(value);
  if (!barcodes.length) return;
  assetInfoDialog.value = { open: true, loading: true, results: [] };
  const results: AssetInfoLookupItem[] = [];
  for (const barcode of barcodes) {
    try {
      const result = await apiGet<AssetInfoQueryResult & { found: boolean }>("/asset-info/query", { params: { barcode } });
      results.push({ barcode, found: result.found, fields: result.fields || {} });
    } catch {
      results.push({ barcode, found: false, fields: {} });
    }
  }
  assetInfoDialog.value = { open: true, loading: false, results };
}

async function loadSummary() {
  summary.value = await apiGet<Task[]>("/summary");
}

function openCorrection(record: TaskRecord) {
  correctionRecord.value = record;
  correctionDialogVisible.value = true;
}

function canCreateAdjustment(record: TaskRecord) {
  return selectedTasks.value.some((task) => task.status === "done" && (task.records || []).some((item) => item.id === record.id));
}

function canEditResource(record: TaskRecord) {
  return permissions.value.updateBaseline && record.record_type !== "surplus" && record.resource_editable !== false && canCreateAdjustment(record);
}

function resourceEditHint(record: TaskRecord) {
  if (record.record_type === "surplus") return "现场新增设备暂无对应综资，请通过机房综资基线更新补充。";
  if (!canCreateAdjustment(record)) return "请待该机房完成盘点后修改综资。";
  if (record.resource_editable === false) return record.resource_unavailable_reason || "当前设备暂无可直接修改的综资记录，请核对最新综资基线。";
  return "修改后用于本任务的三方比对和导出。";
}

function openResourceCorrection(record: TaskRecord) {
  if (!canEditResource(record)) return;
  resourceRecord.value = record;
  resourceDialogVisible.value = true;
}

function openAdjustment(record: TaskRecord) {
  adjustmentRecord.value = record;
  adjustmentDialogVisible.value = true;
}

async function handleCorrectionSaved(payload: { record: TaskRecord; relatedAdjustmentIds: number[] }) {
  await refreshSelectedTasks();
  if (payload.relatedAdjustmentIds.length) {
    ElMessage.warning(`该记录关联整改任务 #${payload.relatedAdjustmentIds.join("、#")}，请到整改闭环台账重新复核。`);
  }
}

async function refreshSelectedTasks() {
  const selectedIds = new Set(selectedTasks.value.map((task) => task.id));
  await loadSummary();
  selectedTasks.value = summary.value.filter((task) => selectedIds.has(task.id));
}

function openRackCorrection(rack: CabinetRecord | null) {
  rackToCorrect.value = rack;
  rackDialogVisible.value = true;
}

async function voidRack(rack: CabinetRecord) {
  let reason: string;
  try {
    const result = await ElMessageBox.prompt(
      `作废 ${rack.cabinet_type_label || rack.cabinet_type}“${rack.rack_no || rack.cabinet_name || ""}”后，该机架不再参与本次盘点展示、导出和核对，历史记录保留。请填写作废原因。`,
      "作废误录机架",
      { confirmButtonText: "确认作废", cancelButtonText: "取消", inputType: "textarea", inputValidator: (value) => Boolean(value?.trim()) || "请填写作废原因" },
    );
    reason = result.value.trim();
  } catch {
    return;
  }
  voidingRackId.value = rack.id;
  try {
    const result = await apiDelete<{ message: string }>(`/admin/tasks/${rack.task_id}/racks/${rack.id}`, { data: { reason } });
    ElMessage.success(result.message);
    await refreshSelectedTasks();
  } finally {
    voidingRackId.value = null;
  }
}

function runQuery() {
  selectedTasks.value = [];
  if (queryField.value === "__all__") {
    candidates.value = summary.value;
    resultDialogVisible.value = true;
    return;
  }
  const needle = queryText.value.trim().toLowerCase();
  if (!needle) {
    ElMessage.warning("请输入查询内容后再查询");
    candidates.value = [];
    resultDialogVisible.value = false;
    return;
  }
  candidates.value = summary.value.filter((task) => taskValue(task, queryField.value).toLowerCase().includes(needle));
  resultDialogVisible.value = true;
}

function onSelectionChange(rows: Task[]) {
  selectedTasks.value = rows;
}

async function downloadData() {
  const params = new URLSearchParams();
  selectedTasks.value.forEach((task) => params.append("task_ids", String(task.id)));
  await downloadBlob(`/ai/export/inventory?${params}`, `资产盘点数据_${formatTime(new Date().toISOString()).slice(0, 10)}.xlsx`);
}

onMounted(loadSummary);
</script>

<style scoped>
.metric-grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 18px 0; }

.asset-selection-strip {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-height: 34px;
  margin: -4px 0 12px;
  padding: 0 4px;
  color: var(--muted);
  font-size: 14px;
}

.asset-selection-strip span {
  color: #31536f;
  font-weight: 680;
}

.asset-selection-strip em {
  font-style: normal;
}

.dialog-tip {
  margin-bottom: 12px;
  color: var(--muted);
  font-size: 14px;
}

.dialog-footer-note {
  float: left;
  color: var(--muted);
  line-height: 32px;
}

.table-wrap.compact {
  margin-bottom: 0;
}

.section-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }

.report-table :deep(.cell) {
  padding-right: 10px;
  padding-left: 10px;
  line-height: 22px;
}

.report-device-table :deep(.el-table__body .cell) {
  font-size: 14px;
}

.report-name {
  display: -webkit-box;
  overflow: hidden;
  white-space: normal;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.report-barcode,
.report-table :deep(.report-barcode-cell .cell) {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
}

.report-table :deep(.el-button.report-barcode) {
  display: inline-flex;
  justify-content: flex-start;
  width: 100%;
  padding: 0;
  font-weight: 600;
  background: transparent;
  border: 0;
  box-shadow: none;
  transform: none;
}

.report-table :deep(.el-button.report-barcode > span) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}

.report-row-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.report-action-trigger { display: block; }

.report-row-actions :deep(.el-button) {
  width: 100%;
  min-height: 28px;
  margin: 0;
  padding: 5px 8px;
  font-size: 13px;
}

@media (max-width: 760px) {
  .asset-selection-strip {
    display: flex;
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }
}
</style>
