<template>
  <PageHeader :icon="DataAnalysis" title="资产盘点数据" desc="查询并导出清单与现场原始数据" />

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
      <el-table border table-layout="auto" :data="rows.slice(0, 500)" height="420">
        <el-table-column v-for="col in columns" :key="col" :prop="col" :label="col" :min-width="columnMinWidth(col)" show-overflow-tooltip />
      </el-table>
    </div>
  </template>
  <el-empty v-else description="请先查询任务，并在弹窗中勾选需要导出的范围。" />

  <el-dialog v-model="resultDialogVisible" title="查询结果" width="980px" class="result-dialog">
    <el-empty v-if="!candidates.length" description="没有查到相关任务。" />
    <template v-else>
      <div class="dialog-tip">查询到 {{ candidates.length }} 个任务，请勾选需要导出的数据范围。</div>
      <div class="table-wrap compact">
        <el-table border table-layout="auto" :data="candidates" height="360" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="48" />
          <el-table-column prop="room_name" label="机房" min-width="190" show-overflow-tooltip />
          <el-table-column prop="area_name" label="片区" min-width="100" />
          <el-table-column prop="task_name" label="任务名称" min-width="210" show-overflow-tooltip />
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
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { DataAnalysis, DocumentChecked, Download } from "@element-plus/icons-vue";
import PageHeader from "../components/PageHeader.vue";
import SectionTitle from "../components/SectionTitle.vue";
import { apiGet } from "../api";
import type { Task, TaskRecord } from "../types";
import { downloadWorkbook, inventoryRows, taskStage } from "../utils";

const summary = ref<Task[]>([]);
const resultDialogVisible = ref(false);
const queryField = ref("__all__");
const queryText = ref("");
const candidates = ref<Task[]>([]);
const selectedTasks = ref<Task[]>([]);

const queryOptions = [
  { label: "全部查询", value: "__all__" },
  { label: "机房名称", value: "room_name" },
  { label: "任务名称", value: "task_name" },
  { label: "片区", value: "area_name" },
  { label: "领取人", value: "assigned_name" },
  { label: "现场状态", value: "field_stage" },
];
const stageOptions = ["待领取", "盘点中", "现场完成"];

const queryLabel = computed(() => queryOptions.find((item) => item.value === queryField.value)?.label || "查询内容");
const areaOptions = ["汇聚及核心", "南区", "北区", "西区"];
const rows = computed(() => inventoryRows(selectedTasks.value));
const columns = computed(() => Object.keys(rows.value[0] || {}));
const inventoryRecords = computed(() => selectedTasks.value.flatMap((task) => task.records || []).filter((record) => (record.record_type || "normal") !== "surplus"));
const totalCount = computed(() => inventoryRecords.value.length);
const scannedCount = computed(() => inventoryRecords.value.filter((record: TaskRecord) => record.match_status !== "未扫").length);
const pendingCount = computed(() => inventoryRecords.value.filter((record: TaskRecord) => record.match_status === "未扫").length);

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

async function loadSummary() {
  summary.value = await apiGet<Task[]>("/summary");
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

function downloadData() {
  downloadWorkbook(`资产盘点数据_${new Date().toISOString().slice(0, 10)}.xls`, { 资产盘点数据: rows.value });
}

function columnMinWidth(col: string) {
  if (col.includes("机房") || col.includes("任务")) return 220;
  if (col.includes("时间")) return 170;
  if (col.includes("状态") || col.includes("片区")) return 110;
  if (col.includes("条码") || col.includes("编码")) return 150;
  return 135;
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

@media (max-width: 760px) {
  .asset-selection-strip {
    display: flex;
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }
}
</style>
