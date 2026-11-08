<template>
  <section class="task-chart-grid">
    <div class="chart-panel">
      <div class="chart-head">
        <el-icon><TrendCharts /></el-icon>
        <span>盘点进度</span>
      </div>
      <div class="progress-chart">
        <div class="donut" :style="donutStyle">
          <div class="donut-inner">
            <strong>{{ deviceProgressPercent }}%</strong>
            <span>已盘</span>
          </div>
        </div>
        <div class="progress-meta">
          <div><span>设备总数</span><strong>{{ totalDevices }}</strong></div>
          <div><span>已盘设备</span><strong>{{ scannedDevices }}</strong></div>
          <div><span>未盘设备</span><strong>{{ pendingDevices }}</strong></div>
        </div>
      </div>
      <div class="area-progress">
        <div v-for="item in areaProgressItems" :key="item.area" class="area-progress-row">
          <div class="area-progress-top">
            <span>{{ item.area }}</span>
            <strong>{{ item.percent }}%</strong>
          </div>
          <div class="area-progress-track">
            <div class="area-progress-fill" :style="{ width: `${item.percent}%` }"></div>
          </div>
          <div class="area-progress-foot">
            <span>{{ item.scanned }}/{{ item.total }} 已盘</span>
            <span>{{ item.taskCount }} 个任务</span>
          </div>
        </div>
      </div>
    </div>
    <div class="chart-panel">
      <div class="chart-head">
        <el-icon><DataLine /></el-icon>
        <span>任务状态分布</span>
      </div>
      <div class="status-overview">
        <div>
          <span>任务总数</span>
          <strong>{{ tasks.length }}</strong>
        </div>
        <div>
          <span>现场完成</span>
          <strong>{{ countByStatus("done") }}/{{ tasks.length }}</strong>
        </div>
        <div class="status-overview-progress">
          <div class="status-overview-text">
            <span>总完成率</span>
            <strong>{{ taskCompletionPercent }}%</strong>
          </div>
          <div class="status-overview-track">
            <div :style="{ width: `${taskCompletionPercent}%` }"></div>
          </div>
        </div>
      </div>
      <div class="status-bars">
        <div v-for="item in statusChartItems" :key="item.label" class="status-bar-row">
          <div class="status-bar-text">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
          <div class="status-bar-track">
            <div class="status-bar-fill" :class="item.className" :style="{ width: `${item.percent}%` }"></div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <SectionTitle :icon="Tickets" title="任务列表" />
  <div class="table-wrap">
    <el-table border table-layout="auto" :data="taskRows" height="330">
      <el-table-column prop="机房" label="机房" min-width="190" show-overflow-tooltip />
      <el-table-column prop="片区" label="片区" min-width="90" />
      <el-table-column prop="批次" label="导入批次" min-width="215" show-overflow-tooltip />
      <el-table-column prop="任务名称" label="任务名称" min-width="210" show-overflow-tooltip />
      <el-table-column prop="领取人" label="领取人" min-width="100" />
      <el-table-column label="现场状态" min-width="112">
        <template #default="{ row }"><el-tag :type="statusTag(row.现场状态)" effect="plain">{{ row.现场状态 }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="任务更新" label="任务更新说明" min-width="260" show-overflow-tooltip />
      <el-table-column prop="进度" label="进度" min-width="90" />
      <el-table-column prop="完成率" label="完成率" min-width="90" />
      <el-table-column prop="领取时间" label="领取时间" min-width="160" />
      <el-table-column label="是否超时" min-width="95">
        <template #default="{ row }"><el-tag :type="row.是否超时 === '是' ? 'danger' : 'success'" effect="plain">{{ row.是否超时 }}</el-tag></template>
      </el-table-column>
    </el-table>
  </div>

  <template v-if="permissions.manageTasks">
  <SectionTitle :icon="Delete" title="作废任务" muted="查询并勾选任务后填写作废原因" />
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
      <el-input v-else v-model="queryText" :placeholder="`请输入${queryLabel}`" />
    </el-form-item>
    <div class="span-2">
      <el-button type="primary" class="full-btn" @click="runQuery">查询</el-button>
    </div>
  </div>

  <el-dialog v-model="resultDialogVisible" title="查询结果" width="980px" class="result-dialog">
    <el-empty v-if="!deleteCandidates.length" description="没有查到相关任务。" />
    <template v-else>
      <div class="dialog-tip">查询到 {{ deleteCandidates.length }} 个任务，请勾选需要作废的任务。</div>
      <div class="table-wrap compact">
        <el-table border table-layout="auto" :data="deleteCandidates" height="320" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="48" />
          <el-table-column prop="room_name" label="机房" min-width="190" show-overflow-tooltip />
          <el-table-column prop="area_name" label="片区" min-width="100" />
          <el-table-column prop="task_name" label="任务名称" min-width="210" show-overflow-tooltip />
          <el-table-column label="领取人" min-width="100">
            <template #default="{ row }">{{ row.claimed_name || "-" }}</template>
          </el-table-column>
          <el-table-column label="现场状态" min-width="112">
            <template #default="{ row }"><el-tag :type="statusTag(taskStage(row))" effect="plain">{{ taskStage(row) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="进度" min-width="90">
            <template #default="{ row }">{{ row.scanned || 0 }}/{{ row.total || 0 }}</template>
          </el-table-column>
        </el-table>
      </div>
      <el-form-item class="delete-reason" label="作废原因">
        <el-input v-model="deleteReason" placeholder="必填，例如：清单重复导入、机房暂不纳入本次试用、任务导入错误" />
      </el-form-item>
    </template>
    <template #footer>
      <span class="dialog-footer-note">已选 {{ selectedDeleteTask ? 1 : 0 }} 个任务</span>
      <el-button @click="resultDialogVisible = false">关闭</el-button>
      <el-button type="danger" :disabled="!selectedDeleteTask || !deleteReason.trim()" @click="deleteTask">作废选中任务</el-button>
    </template>
  </el-dialog>
  </template>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { DataLine, Delete, Tickets, TrendCharts } from "@element-plus/icons-vue";
import SectionTitle from "../components/SectionTitle.vue";
import { apiDelete, apiGet } from "../api";
import type { Task } from "../types";
import { formatTime, isOverdue, taskStage } from "../utils";
import { permissions } from "../permissions";

const tasks = ref<Task[]>([]);
const queried = ref(false);
const queryField = ref("room_name");
const queryText = ref("");
const deleteCandidates = ref<Task[]>([]);
const selectedDeleteTask = ref<Task | null>(null);
const deleteReason = ref("");
const resultDialogVisible = ref(false);

const queryOptions = [
  { label: "导入批次", value: "import_batch_id" },
  { label: "机房名称", value: "room_name" },
  { label: "任务名称", value: "task_name" },
  { label: "片区", value: "area_name" },
  { label: "领取人", value: "claimed_name" },
  { label: "现场状态", value: "field_stage" },
];
const stageOptions = ["待领取", "盘点中", "现场完成", "待释放旧任务", "等待旧任务释放"];

const queryLabel = computed(() => queryOptions.find((item) => item.value === queryField.value)?.label || "查询内容");
const areaOptions = ["汇聚及核心", "南区", "北区", "西区"];
const overdueCount = computed(() => tasks.value.filter((task) => isOverdue(task)).length);
const progressTasks = computed(() => tasks.value.filter((task) => !task.superseded_by_task_id));
const totalDevices = computed(() => progressTasks.value.reduce((sum, task) => sum + (task.total || 0), 0));
const scannedDevices = computed(() => progressTasks.value.reduce((sum, task) => sum + (task.scanned || 0), 0));
const pendingDevices = computed(() => Math.max(totalDevices.value - scannedDevices.value, 0));
const deviceProgressPercent = computed(() => totalDevices.value ? Math.round((scannedDevices.value / totalDevices.value) * 100) : 0);
const donutStyle = computed(() => ({ "--progress": deviceProgressPercent.value } as Record<string, string | number>));
const taskCompletionPercent = computed(() => tasks.value.length ? Math.round((countByStatus("done") / tasks.value.length) * 100) : 0);
const areaProgressItems = computed(() => {
  const areaNames = ["汇聚及核心", "南区", "北区", "西区"];
  return areaNames.map((area) => {
    const areaTasks = progressTasks.value.filter((task) => (task.area_name || "").trim() === area);
    const total = areaTasks.reduce((sum, task) => sum + (task.total || 0), 0);
    const scanned = areaTasks.reduce((sum, task) => sum + (task.scanned || 0), 0);
    return {
      area,
      total,
      scanned,
      taskCount: areaTasks.length,
      percent: total ? Math.round((scanned / total) * 100) : 0,
    };
  });
});
const statusChartItems = computed(() => {
  const total = Math.max(tasks.value.length, 1);
  return [
    { label: "待领取", value: tasks.value.filter((task) => taskStage(task) === "待领取").length, className: "pending" },
    { label: "盘点中", value: tasks.value.filter((task) => taskStage(task) === "盘点中").length, className: "doing" },
    { label: "等待旧任务释放", value: tasks.value.filter((task) => !!task.waiting_for_task_id).length, className: "pending" },
    { label: "待释放旧任务", value: tasks.value.filter((task) => !!task.superseded_by_task_id).length, className: "pending" },
    { label: "现场完成", value: countByStatus("done"), className: "done" },
    { label: "超时任务", value: overdueCount.value, className: "overdue" },
  ].map((item) => ({ ...item, percent: Math.round((item.value / total) * 100) }));
});
const taskRows = computed(() => tasks.value.map((task) => ({
  机房: task.room_name,
  批次: task.import_batch_id || "",
  片区: task.area_name || "汇聚及核心",
  任务名称: task.task_name,
  领取人: task.claimed_name || "-",
  现场状态: taskStage(task),
  任务更新: task.replacement_notice || "",
  进度: `${task.scanned || 0}/${task.total || 0}`,
  完成率: `${Math.round(((task.scanned || 0) / (task.total || 1)) * 100)}%`,
  领取时间: formatTime(task.claimed_at),
  是否超时: isOverdue(task) ? "是" : "否",
})));

function countByStatus(status: string) {
  return tasks.value.filter((task) => task.status === status).length;
}

function statusTag(status: string) {
  if (status.includes("完成")) return "success";
  if (status.includes("盘点")) return "primary";
  if (status.includes("待")) return "warning";
  return "info";
}

function taskValue(task: Task, field: string) {
  if (field === "field_stage") return taskStage(task);
  if (field === "area_name") return task.area_name || "汇聚及核心";
  if (field === "claimed_name") return task.claimed_name || "-";
  return String((task as unknown as Record<string, unknown>)[field] || "");
}

function onQueryFieldChange() {
  queryText.value = "";
}

async function loadTasks() {
  tasks.value = await apiGet<Task[]>("/tasks");
}

function runQuery() {
  const needle = queryText.value.trim().toLowerCase();
  queried.value = true;
  selectedDeleteTask.value = null;
  deleteReason.value = "";
  if (!needle) {
    ElMessage.warning("请输入查询内容后再查询");
    deleteCandidates.value = [];
    resultDialogVisible.value = false;
    return;
  }
  deleteCandidates.value = tasks.value.filter((task) => taskValue(task, queryField.value).toLowerCase().includes(needle));
  resultDialogVisible.value = true;
}

function onSelectionChange(rows: Task[]) {
  selectedDeleteTask.value = rows[rows.length - 1] || null;
}

async function deleteTask() {
  if (!selectedDeleteTask.value) return;
  await ElMessageBox.confirm(`确认作废任务：${selectedDeleteTask.value.room_name}？`, "作废任务", { type: "warning" });
  await apiDelete(`/tasks/${selectedDeleteTask.value.id}`, { params: { reason: deleteReason.value.trim() } });
  ElMessage.success("任务已作废，不再参与统计");
  await loadTasks();
  runQuery();
}

onMounted(loadTasks);
defineExpose({ refresh: loadTasks });
</script>

<style scoped>
.delete-actions { margin-top: 16px; }

.delete-reason {
  margin: 14px 0 0;
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

.task-chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
  gap: 16px;
  margin: 4px 0 18px;
}

.chart-panel {
  position: relative;
  padding: 18px;
  border: 1px solid rgba(151, 205, 248, 0.68);
  border-radius: 8px;
  background:
    linear-gradient(rgba(31, 125, 242, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(24, 197, 200, 0.035) 1px, transparent 1px),
    linear-gradient(145deg, rgba(255, 255, 255, 0.94), rgba(238, 248, 255, 0.86));
  background-size: 24px 24px, 24px 24px, auto;
  box-shadow: 0 18px 42px rgba(40, 95, 150, 0.12);
  overflow: hidden;
}

.chart-panel::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: linear-gradient(90deg, #1f7df2, #18c5c8, #28f0a2);
}

.chart-panel::after {
  content: "";
  position: absolute;
  right: -90px;
  top: -90px;
  width: 220px;
  height: 220px;
  border: 30px solid rgba(31, 125, 242, 0.04);
  border-radius: 50%;
  pointer-events: none;
}

.chart-head {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  color: #123052;
  font-size: 17px;
  font-weight: 760;
}

.chart-head .el-icon {
  color: var(--primary);
  font-size: 19px;
}

.progress-chart {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 152px minmax(0, 1fr);
  gap: 18px;
  align-items: center;
}

.area-progress {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 18px;
}

.area-progress-row {
  position: relative;
  min-height: 92px;
  padding: 12px;
  border: 1px solid rgba(31, 125, 242, 0.14);
  border-radius: 8px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(228, 246, 255, 0.78)),
    radial-gradient(circle at 90% 15%, rgba(24, 197, 200, 0.16), transparent 38%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.66);
  overflow: hidden;
}

.area-progress-row::after {
  content: "";
  position: absolute;
  right: -22px;
  bottom: -30px;
  width: 76px;
  height: 76px;
  border: 12px solid rgba(31, 125, 242, 0.06);
  border-radius: 50%;
}

.area-progress-top,
.area-progress-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.area-progress-top {
  color: #123052;
  font-size: 15px;
  font-weight: 760;
}

.area-progress-top strong {
  color: #1267bd;
  font-size: 20px;
}

.area-progress-track {
  height: 9px;
  margin: 10px 0 8px;
  border-radius: 999px;
  background: #dfeaf5;
  overflow: hidden;
}

.area-progress-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1f7df2, #18c5c8, #28f0a2);
  box-shadow: 0 0 16px rgba(24, 197, 200, 0.34);
  transition: width 0.24s ease;
}

.area-progress-foot {
  color: var(--muted);
  font-size: 13px;
}

.donut {
  width: 148px;
  height: 148px;
  position: relative;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background:
    radial-gradient(circle at center, #ffffff 0 52%, transparent 53%),
    conic-gradient(from 220deg, #1f7df2 0 calc(var(--progress) * 1%), #dfeaf5 0 100%);
  box-shadow:
    inset 0 0 0 1px rgba(31, 125, 242, 0.12),
    0 18px 34px rgba(31, 125, 242, 0.18);
}

.donut::before {
  content: "";
  position: absolute;
  inset: -10px;
  border-radius: 50%;
  border: 1px solid rgba(31, 125, 242, 0.14);
  background: transparent;
}

.donut::after {
  content: "";
  position: absolute;
  inset: 18px;
  border: 1px dashed rgba(31, 125, 242, 0.16);
  border-radius: 50%;
}

.donut-inner {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 4px;
  text-align: center;
}

.donut-inner strong {
  color: #1267bd;
  font-size: 30px;
  line-height: 1;
}

.donut-inner span {
  color: var(--muted);
  font-size: 14px;
}

.progress-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.progress-meta div {
  position: relative;
  min-height: 72px;
  padding: 12px;
  border: 1px solid rgba(31, 125, 242, 0.12);
  border-radius: 8px;
  background:
    linear-gradient(135deg, #f7fbff 0%, #eef8ff 100%),
    radial-gradient(circle at 92% 18%, rgba(24, 197, 200, 0.12), transparent 42%);
  overflow: hidden;
}

.progress-meta div::after {
  content: "";
  position: absolute;
  right: -16px;
  bottom: -22px;
  width: 58px;
  height: 58px;
  border: 10px solid rgba(31, 125, 242, 0.05);
  border-radius: 50%;
}

.progress-meta span {
  display: block;
  color: var(--muted);
  font-size: 14px;
}

.progress-meta strong {
  display: block;
  margin-top: 8px;
  color: #123052;
  font-size: 24px;
  line-height: 1;
}

.status-overview {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 0.82fr 1fr minmax(220px, 1.35fr);
  gap: 12px;
  margin-bottom: 18px;
  padding: 14px;
  border: 1px solid rgba(31, 125, 242, 0.12);
  border-radius: 8px;
  background:
    linear-gradient(135deg, rgba(232, 246, 255, 0.96), rgba(232, 255, 250, 0.82)),
    radial-gradient(circle at 95% 5%, rgba(24, 197, 200, 0.18), transparent 42%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.7);
}

.status-overview > div {
  min-width: 0;
}

.status-overview span,
.status-overview-text span {
  display: block;
  color: var(--muted);
  font-size: 13px;
}

.status-overview strong {
  display: block;
  margin-top: 7px;
  color: #1267bd;
  font-size: 26px;
  line-height: 1;
}

.status-overview-progress {
  display: grid;
  align-content: center;
}

.status-overview-text {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.status-overview-text strong {
  margin-top: 0;
  color: #0f9b75;
}

.status-overview-track {
  height: 12px;
  border-radius: 999px;
  background: rgba(209, 226, 241, 0.9);
  overflow: hidden;
}

.status-overview-track div {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1f7df2, #18c5c8, #28f0a2);
  box-shadow: 0 0 18px rgba(24, 197, 200, 0.38);
  transition: width 0.24s ease;
}

.status-bars {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 16px;
}

.status-bar-text {
  display: flex;
  justify-content: space-between;
  margin-bottom: 7px;
  color: #26435f;
  font-size: 15px;
  font-weight: 650;
}

.status-bar-text strong {
  color: #0f5fa8;
}

.status-bar-track {
  position: relative;
  height: 14px;
  border-radius: 999px;
  background: rgba(220, 234, 247, 0.92);
  overflow: hidden;
}

.status-bar-fill {
  height: 100%;
  min-width: 6px;
  border-radius: inherit;
  transition: width 0.24s ease;
  box-shadow: 0 0 16px rgba(31, 125, 242, 0.18);
}

.status-bar-fill.pending { background: linear-gradient(90deg, #f3ac3d, #ffd16b); }
.status-bar-fill.doing { background: linear-gradient(90deg, #1f7df2, #42b4ff); }
.status-bar-fill.done { background: linear-gradient(90deg, #11a66a, #18c5c8); }
.status-bar-fill.overdue { background: linear-gradient(90deg, #e05252, #f28b71); }

@media (max-width: 1180px) {
  .task-chart-grid,
  .progress-chart {
    grid-template-columns: 1fr;
  }

  .area-progress {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .donut {
    justify-self: center;
  }
}

@media (max-width: 720px) {
  .area-progress {
    grid-template-columns: 1fr;
  }
}
</style>
