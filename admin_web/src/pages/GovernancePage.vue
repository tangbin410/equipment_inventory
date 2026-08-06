<template>
  <div class="governance-page">
    <PageHeader :icon="CircleCheck" title="数据整治与闭环" desc="汇总问题数据、盘盈盘亏和整改闭环进展" />

    <section class="toolbar governance-toolbar">
      <el-form-item class="span-3" label="统计片区">
        <el-select v-model="areaFilter" placeholder="全部片区" clearable>
          <el-option v-for="area in areaOptions" :key="area" :label="area" :value="area" />
        </el-select>
      </el-form-item>
      <el-form-item class="span-6" label="数据更新时间">
        <el-input :model-value="lastUpdated || '尚未加载'" readonly />
      </el-form-item>
      <div class="refresh-action span-3">
        <el-button class="full-btn" :loading="loading" @click="loadData">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </div>
    </section>

    <section class="metric-grid six governance-metrics" aria-label="数据整治指标">
      <div class="metric-card governance-metric">
        <span class="metric-label">问题数据</span>
        <strong class="metric-value">{{ problemRows.length }}</strong>
        <small>{{ problemRoomCount }} 个机房</small>
      </div>
      <div class="metric-card governance-metric">
        <span class="metric-label">盘盈盘亏机房</span>
        <strong class="metric-value">{{ differenceRooms.length }}</strong>
        <small>盘盈 {{ surplusCount }} / 盘亏 {{ missingCount }}</small>
      </div>
      <div class="metric-card governance-metric">
        <span class="metric-label">待审核</span>
        <strong class="metric-value">{{ countAdjustmentStatus("pending_review") }}</strong>
        <small>等待管理员判断</small>
      </div>
      <div class="metric-card governance-metric">
        <span class="metric-label">整改中</span>
        <strong class="metric-value">{{ countAdjustmentStatus("dispatched") }}</strong>
        <small>已下发现场人员</small>
      </div>
      <div class="metric-card governance-metric">
        <span class="metric-label">超期整改</span>
        <strong class="metric-value">{{ overdueAdjustments.length }}</strong>
        <small>固定 7 天阈值</small>
      </div>
      <div class="metric-card governance-metric">
        <span class="metric-label">闭环率</span>
        <strong class="metric-value">{{ closureRate }}%</strong>
        <small>已闭环 {{ countAdjustmentStatus("done") }} 条</small>
      </div>
    </section>

    <section class="governance-chart-grid">
      <div class="chart-panel">
        <div class="chart-head">
          <div class="chart-title">
            <el-icon><TrendCharts /></el-icon>
            <span>问题数据分布</span>
          </div>
          <el-segmented v-model="problemDimension" :options="['异常类型', '片区']" size="small" />
        </div>
        <el-empty v-if="!problemDistributionItems.length" description="暂无问题数据" :image-size="72" />
        <div v-else class="status-bars">
          <div v-for="item in problemDistributionItems" :key="item.label" class="status-bar-row">
            <div class="status-bar-text">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </div>
            <div class="status-bar-track">
              <div class="status-bar-fill problem" :style="{ width: `${item.percent}%` }"></div>
            </div>
          </div>
        </div>
      </div>

      <div class="chart-panel">
        <div class="chart-head">
          <div class="chart-title">
            <el-icon><DataLine /></el-icon>
            <span>整改闭环状态</span>
          </div>
          <el-tag v-if="overdueAdjustments.length" type="danger" effect="plain">超期 {{ overdueAdjustments.length }}</el-tag>
        </div>
        <div class="status-overview">
          <div>
            <span>整改任务</span>
            <strong>{{ scopedAdjustments.length }}</strong>
          </div>
          <div>
            <span>已闭环</span>
            <strong>{{ countAdjustmentStatus("done") }}</strong>
          </div>
          <div class="status-overview-progress">
            <div class="status-overview-text">
              <span>闭环率</span>
              <strong>{{ closureRate }}%</strong>
            </div>
            <div class="status-overview-track">
              <div :style="{ width: `${closureRate}%` }"></div>
            </div>
          </div>
        </div>
        <div class="status-bars">
          <div v-for="item in closureStatusItems" :key="item.status" class="status-bar-row">
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

    <SectionTitle
      :icon="DataAnalysis"
      title="盘盈盘亏机房"
      :muted="`共 ${differenceRooms.length} 个机房，按差异数量排序`"
    />
    <div class="table-wrap governance-table">
      <el-table :data="differenceRooms" height="320" table-layout="fixed">
        <el-table-column prop="roomName" label="机房" min-width="190" show-overflow-tooltip />
        <el-table-column prop="areaName" label="片区" min-width="100" />
        <el-table-column prop="surplus" label="盘盈" min-width="90" />
        <el-table-column prop="missing" label="盘亏" min-width="90" />
        <el-table-column prop="total" label="差异合计" min-width="100" />
        <el-table-column label="整改状态" min-width="120">
          <template #default="{ row }">
            <el-tag :type="closureStateTag(row.closureState)" effect="plain">{{ row.closureState }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <SectionTitle :icon="Tickets" title="问题数据与整改闭环" muted="查询问题明细并完成整改审核下发" />
    <div class="table-wrap governance-workbench">
      <el-tabs v-model="activeTab">
        <el-tab-pane name="problems">
          <template #label>问题数据 <span class="tab-count">{{ filteredProblemRows.length }}</span></template>
          <div class="table-toolbar">
            <el-select v-model="issueFilter" clearable placeholder="全部异常类型">
              <el-option v-for="item in issueTypeOptions" :key="item" :label="item" :value="item" />
            </el-select>
            <el-input v-model="problemKeyword" clearable placeholder="搜索机房、设备或任务">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <span>最多展示前 500 条</span>
          </div>
          <el-empty v-if="!filteredProblemRows.length" description="当前范围没有问题数据" />
          <el-table v-else border :data="filteredProblemRows.slice(0, 500)" height="430" table-layout="auto">
            <el-table-column prop="roomName" label="机房" min-width="160" show-overflow-tooltip />
            <el-table-column prop="areaName" label="片区" min-width="92" />
            <el-table-column prop="taskName" label="来源任务" min-width="190" show-overflow-tooltip />
            <el-table-column prop="deviceName" label="设备" min-width="180" show-overflow-tooltip />
            <el-table-column label="问题类型" min-width="220">
              <template #default="{ row }">
                <div class="issue-tags">
                  <el-tag v-for="issue in row.issueTypes" :key="issue" :type="issueTag(issue)" effect="plain">{{ issue }}</el-tag>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="现场说明" min-width="210" show-overflow-tooltip />
            <el-table-column label="任务整改状态" min-width="122">
              <template #default="{ row }">
                <el-tag :type="closureStateTag(row.closureState)" effect="plain">{{ row.closureState }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="盘点时间" min-width="160">
              <template #default="{ row }">{{ formatTime(row.scannedAt) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane name="adjustments">
          <template #label>整改闭环 <span class="tab-count">{{ filteredAdjustments.length }}</span></template>
          <div class="table-toolbar adjustments-toolbar">
            <el-select v-model="adjustmentStatusFilter" clearable placeholder="全部整改状态">
              <el-option label="待审核" value="pending_review" />
              <el-option label="已下发" value="dispatched" />
              <el-option label="已闭环" value="done" />
              <el-option label="已驳回" value="rejected" />
            </el-select>
            <el-input v-model="adjustmentKeyword" clearable placeholder="搜索机房、设备或问题类型">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </div>
          <el-empty v-if="!filteredAdjustments.length" description="当前范围没有整改任务" />
          <el-table v-else border table-layout="auto" :data="filteredAdjustments" height="450">
            <el-table-column prop="id" label="草稿ID" min-width="82" />
            <el-table-column label="状态" min-width="108">
              <template #default="{ row }">
                <div class="status-cell">
                  <el-tag :type="adjustmentStatusTag(row.status)" effect="plain">{{ adjustmentStatusText(row.status) }}</el-tag>
                  <small v-if="isAdjustmentOverdue(row)">超期 {{ adjustmentAgeDays(row) }} 天</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="area_name" label="片区" min-width="98" />
            <el-table-column prop="room_name" label="涉及机房" min-width="160" show-overflow-tooltip />
            <el-table-column prop="problem_type" label="问题类型" min-width="150" show-overflow-tooltip />
            <el-table-column prop="device_name" label="涉及设备" min-width="170" show-overflow-tooltip />
            <el-table-column prop="risk_level" label="风险" min-width="78" />
            <el-table-column prop="suggestion" label="整改建议" min-width="240" show-overflow-tooltip />
            <el-table-column label="现场人员" min-width="175">
              <template #default="{ row }">
                <el-select v-if="row.status === 'pending_review'" v-model="selectedAssignees[row.id]" size="small" placeholder="选择人员">
                  <el-option
                    v-for="worker in workersForArea(row.area_name)"
                    :key="worker.id"
                    :label="`${worker.name} (#${worker.id})`"
                    :value="worker.id"
                  />
                </el-select>
                <span v-else>{{ row.assigned_name || "-" }}</span>
              </template>
            </el-table-column>
            <el-table-column label="催办" min-width="145">
              <template #default="{ row }">
                <span v-if="row.reminder_count">{{ row.reminder_count }} 次<br><small>{{ formatTime(row.reminded_at) }}</small></span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" fixed="right" min-width="180">
              <template #default="{ row }">
                <template v-if="row.status === 'pending_review'">
                  <el-button size="small" type="primary" @click="dispatchAdjustment(row)">审核下发</el-button>
                  <el-button size="small" text type="danger" @click="rejectAdjustment(row)">驳回</el-button>
                </template>
                <el-button v-else-if="row.status === 'dispatched'" size="small" type="warning" @click="remindAdjustment(row)">记录催办</el-button>
                <span v-else class="muted-action">{{ adjustmentActionText(row) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { CircleCheck, DataAnalysis, DataLine, Refresh, Search, Tickets, TrendCharts } from "@element-plus/icons-vue";
import PageHeader from "../components/PageHeader.vue";
import SectionTitle from "../components/SectionTitle.vue";
import { apiGet, apiPost } from "../api";
import type { AIAdjustmentTask, AdminUser, Task, TaskRecord } from "../types";
import { formatTime } from "../utils";

interface ProblemRow {
  key: string;
  taskId: number;
  roomName: string;
  areaName: string;
  taskName: string;
  deviceName: string;
  issueTypes: string[];
  reason: string;
  scannedAt?: string;
  closureState: string;
}

interface DifferenceRoom {
  roomName: string;
  areaName: string;
  surplus: number;
  missing: number;
  total: number;
  taskIds: Set<number>;
  closureState: string;
}

const areaOptions = ["汇聚及核心", "南区", "北区", "西区"];

const loading = ref(false);
const tasks = ref<Task[]>([]);
const adjustments = ref<AIAdjustmentTask[]>([]);
const workers = ref<AdminUser[]>([]);
const selectedAssignees = ref<Record<number, number>>({});
const lastUpdated = ref("");
const areaFilter = ref("");
const activeTab = ref("problems");
const problemDimension = ref("异常类型");
const issueFilter = ref("");
const problemKeyword = ref("");
const adjustmentStatusFilter = ref("");
const adjustmentKeyword = ref("");

const scopedTasks = computed(() => tasks.value.filter(
  (task) => !areaFilter.value || (task.area_name || "汇聚及核心") === areaFilter.value,
));
const scopedAdjustments = computed(() => adjustments.value.filter(
  (item) => !areaFilter.value || (item.area_name || "汇聚及核心") === areaFilter.value,
));

function recordIssueTypes(record: TaskRecord) {
  const result: string[] = [];
  const recordType = record.record_type || "normal";
  if (recordType === "surplus") result.push("盘盈");
  if (recordType === "missing") result.push("盘亏");
  if (Boolean(record.asset_no_barcode)) result.push("资产无条码");
  if (Boolean(record.cabinet_no_barcode)) result.push("机柜无条码");
  if (record.asset_match_status === "不一致" && recordType !== "missing" && recordType !== "surplus") result.push("资产条码不一致");
  if (record.model_match_status === "不一致" && recordType !== "missing" && recordType !== "surplus") result.push("设备型号不一致");
  if (record.match_status === "不一致" && !result.length) result.push("盘点数据不一致");
  if (record.field_status && !["正常", "盘盈", "盘亏"].includes(record.field_status)) result.push(`现场状态：${record.field_status}`);
  return Array.from(new Set(result));
}

function taskClosureState(taskId: number) {
  const related = scopedAdjustments.value.filter((item) => (item.source_task_ids || []).includes(taskId));
  const statuses = new Set(related.map((item) => item.status));
  if (statuses.has("dispatched")) return "整改中";
  if (statuses.has("pending_review")) return "待审核";
  if (statuses.has("done")) return "已闭环";
  if (statuses.has("rejected")) return "已驳回";
  return "未关联整改";
}

const problemRows = computed<ProblemRow[]>(() => scopedTasks.value.flatMap((task) =>
  (task.records || []).flatMap((record) => {
    const issueTypes = recordIssueTypes(record);
    if (!issueTypes.length) return [];
    return [{
      key: `${task.id}-${record.id}`,
      taskId: task.id,
      roomName: task.room_name,
      areaName: task.area_name || "汇聚及核心",
      taskName: task.task_name,
      deviceName: record.device_name || "-",
      issueTypes,
      reason: record.missing_reason || record.notes || "-",
      scannedAt: record.scanned_at,
      closureState: taskClosureState(task.id),
    }];
  }),
));

const issueTypeOptions = computed(() => Array.from(new Set(problemRows.value.flatMap((row) => row.issueTypes))).sort());
const filteredProblemRows = computed(() => {
  const keyword = problemKeyword.value.trim().toLowerCase();
  return problemRows.value.filter((row) => {
    if (issueFilter.value && !row.issueTypes.includes(issueFilter.value)) return false;
    if (!keyword) return true;
    return [row.roomName, row.areaName, row.taskName, row.deviceName, row.issueTypes.join(" ")]
      .some((value) => value.toLowerCase().includes(keyword));
  });
});

const differenceRooms = computed<DifferenceRoom[]>(() => {
  const roomMap = new Map<string, DifferenceRoom>();
  problemRows.value.forEach((row) => {
    const surplus = row.issueTypes.includes("盘盈") ? 1 : 0;
    const missing = row.issueTypes.includes("盘亏") ? 1 : 0;
    if (!surplus && !missing) return;
    const key = `${row.areaName}\u0000${row.roomName}`;
    const current = roomMap.get(key) || {
      roomName: row.roomName,
      areaName: row.areaName,
      surplus: 0,
      missing: 0,
      total: 0,
      taskIds: new Set<number>(),
      closureState: "未关联整改",
    };
    current.surplus += surplus;
    current.missing += missing;
    current.total += surplus + missing;
    current.taskIds.add(row.taskId);
    roomMap.set(key, current);
  });
  return Array.from(roomMap.values()).map((item) => {
    const states = Array.from(item.taskIds).map(taskClosureState);
    item.closureState = states.includes("整改中") ? "整改中"
      : states.includes("待审核") ? "待审核"
      : states.includes("已闭环") ? "已闭环"
      : states.includes("已驳回") ? "已驳回"
      : "未关联整改";
    return item;
  }).sort((a, b) => b.total - a.total || a.roomName.localeCompare(b.roomName));
});

const problemRoomCount = computed(() => new Set(problemRows.value.map((row) => `${row.areaName}\u0000${row.roomName}`)).size);
const surplusCount = computed(() => problemRows.value.filter((row) => row.issueTypes.includes("盘盈")).length);
const missingCount = computed(() => problemRows.value.filter((row) => row.issueTypes.includes("盘亏")).length);
const overdueAdjustments = computed(() => scopedAdjustments.value.filter(isAdjustmentOverdue));
const closureRate = computed(() => {
  const effective = scopedAdjustments.value.filter((item) => item.status !== "rejected");
  return effective.length ? Math.round((effective.filter((item) => item.status === "done").length / effective.length) * 100) : 0;
});

const filteredAdjustments = computed(() => {
  const keyword = adjustmentKeyword.value.trim().toLowerCase();
  return scopedAdjustments.value.filter((item) => {
    if (adjustmentStatusFilter.value && item.status !== adjustmentStatusFilter.value) return false;
    if (!keyword) return true;
    return [item.room_name, item.device_name, item.problem_type, item.suggestion]
      .some((value) => String(value || "").toLowerCase().includes(keyword));
  });
});

const problemDistributionItems = computed(() => {
  const counts = new Map<string, number>();
  if (problemDimension.value === "片区") {
    problemRows.value.forEach((row) => counts.set(row.areaName, (counts.get(row.areaName) || 0) + 1));
  } else {
    problemRows.value.forEach((row) => row.issueTypes.forEach((issue) => counts.set(issue, (counts.get(issue) || 0) + 1)));
  }
  const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const maxValue = entries[0]?.[1] || 1;
  return entries.map(([label, value]) => ({
    label,
    value,
    percent: Math.max(Math.round((value / maxValue) * 100), 4),
  }));
});

const closureStatusItems = computed(() => {
  const total = scopedAdjustments.value.length || 1;
  return [
    { label: "待审核", status: "pending_review", className: "pending" },
    { label: "整改中", status: "dispatched", className: "doing" },
    { label: "已闭环", status: "done", className: "done" },
    { label: "已驳回", status: "rejected", className: "rejected" },
  ].map((item) => {
    const value = countAdjustmentStatus(item.status);
    return { ...item, value, percent: value ? Math.max(Math.round((value / total) * 100), 4) : 0 };
  });
});

function countAdjustmentStatus(status: string) {
  return scopedAdjustments.value.filter((item) => item.status === status).length;
}

function adjustmentAgeDays(row: AIAdjustmentTask) {
  if (!row.dispatched_at) return 0;
  const timestamp = new Date(row.dispatched_at).getTime();
  return Number.isFinite(timestamp) ? Math.max(Math.floor((Date.now() - timestamp) / 86400000), 0) : 0;
}

function isAdjustmentOverdue(row: AIAdjustmentTask) {
  return row.status === "dispatched" && adjustmentAgeDays(row) >= 7;
}

function workersForArea(areaName: string) {
  return workers.value.filter((worker) => (worker.area_name || "汇聚及核心") === (areaName || "汇聚及核心"));
}

function adjustmentStatusText(status: string) {
  return { pending_review: "待审核", dispatched: "已下发", done: "已闭环", rejected: "已驳回" }[status] || status || "-";
}

function adjustmentStatusTag(status: string) {
  if (status === "pending_review") return "warning";
  if (status === "dispatched") return "primary";
  if (status === "done") return "success";
  return "info";
}

function closureStateTag(status: string) {
  if (status === "已闭环") return "success";
  if (status === "整改中") return "primary";
  if (status === "待审核") return "warning";
  if (status === "已驳回") return "info";
  return "danger";
}

function issueTag(issue: string) {
  if (issue === "盘亏" || issue.includes("不一致")) return "danger";
  if (issue === "盘盈" || issue.includes("无条码")) return "warning";
  return "info";
}

function adjustmentActionText(row: AIAdjustmentTask) {
  if (row.status === "done") return `已完成 ${formatTime(row.completed_at)}`;
  if (row.status === "rejected") return "管理员已驳回";
  return "-";
}

async function dispatchAdjustment(row: AIAdjustmentTask) {
  const assignedTo = selectedAssignees.value[row.id];
  if (!assignedTo) {
    ElMessage.warning("请先选择现场人员");
    return;
  }
  const workerName = workers.value.find((worker) => worker.id === assignedTo)?.name || "现场人员";
  await ElMessageBox.confirm(`确认将整改任务下发给 ${workerName}？`, "审核下发", { type: "warning" });
  await apiPost(`/ai/adjustments/${row.id}/dispatch`, { assigned_to: assignedTo, review_note: "管理员审核通过" });
  ElMessage.success("整改任务已下发");
  await loadAdjustments();
}

async function rejectAdjustment(row: AIAdjustmentTask) {
  await ElMessageBox.confirm(`确认驳回这条整改草稿：${row.problem_type || row.room_name}？`, "驳回整改草稿", { type: "warning" });
  await apiPost(`/ai/adjustments/${row.id}/reject`, { review_note: "管理员驳回" });
  ElMessage.success("整改草稿已驳回");
  await loadAdjustments();
}

async function remindAdjustment(row: AIAdjustmentTask) {
  await ElMessageBox.confirm(
    `确认给整改任务 #${row.id} 记录一次催办？催办将同步到现场人员小程序，任务状态保持已下发。`,
    "记录催办",
    { type: "warning" },
  );
  const result = await apiPost<{ message: string }>(`/ai/adjustments/${row.id}/remind`, {
    reminder_note: "管理员在 PC 后台记录催办",
  });
  ElMessage.success(result.message);
  await loadAdjustments();
}

async function loadAdjustments() {
  adjustments.value = await apiGet<AIAdjustmentTask[]>("/ai/adjustments");
}

async function loadData() {
  loading.value = true;
  try {
    await Promise.all([
      apiGet<Task[]>("/summary").then((rows) => { tasks.value = rows; }),
      apiGet<AIAdjustmentTask[]>("/ai/adjustments").then((rows) => { adjustments.value = rows; }),
      apiGet<AdminUser[]>("/workers").then((rows) => { workers.value = rows; }),
    ]);
    lastUpdated.value = new Date().toLocaleString("zh-CN", { hour12: false });
  } finally {
    loading.value = false;
  }
}

onMounted(loadData);
</script>

<style scoped>
.governance-page {
  min-width: 0;
}

.governance-toolbar {
  margin-bottom: 16px;
  background:
    linear-gradient(120deg, rgba(236, 247, 255, 0.96), rgba(240, 255, 251, 0.9)),
    var(--panel-soft);
}

.refresh-action { align-self: end; }

.governance-metrics {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}

.governance-metric {
  --metric-start: #1f7df2;
  --metric-end: #18c5c8;
  --metric-value: #1267bd;
  --metric-tint: rgba(31, 125, 242, 0.1);
  min-width: 0;
  isolation: isolate;
  overflow: hidden;
  background:
    linear-gradient(145deg, rgba(255, 255, 255, 0.98), var(--metric-tint)),
    #ffffff;
  animation: metricEnter 0.46s cubic-bezier(0.2, 0.75, 0.25, 1) both;
}

.governance-metric::before {
  background: linear-gradient(90deg, var(--metric-start), var(--metric-end));
}

.governance-metric::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  background: linear-gradient(112deg, transparent 22%, rgba(255, 255, 255, 0.72) 46%, transparent 68%);
  transform: translateX(-120%);
  pointer-events: none;
}

.governance-metric > * {
  position: relative;
  z-index: 1;
}

.governance-metric:hover::after {
  animation: metricSheen 0.72s ease-out;
}

.governance-metric .metric-value { color: var(--metric-value); }
.governance-metric:nth-child(1) {
  --metric-start: #e05252;
  --metric-end: #f28b71;
  --metric-value: #c83f4d;
  --metric-tint: rgba(242, 139, 113, 0.12);
}
.governance-metric:nth-child(2) {
  --metric-start: #0f9baf;
  --metric-end: #18c5c8;
  --metric-value: #087f91;
  --metric-tint: rgba(24, 197, 200, 0.12);
  animation-delay: 0.04s;
}
.governance-metric:nth-child(3) {
  --metric-start: #e39725;
  --metric-end: #ffc75a;
  --metric-value: #ba7412;
  --metric-tint: rgba(255, 199, 90, 0.16);
  animation-delay: 0.08s;
}
.governance-metric:nth-child(4) {
  --metric-start: #1f7df2;
  --metric-end: #42b4ff;
  --metric-value: #1267bd;
  --metric-tint: rgba(66, 180, 255, 0.12);
  animation-delay: 0.12s;
}
.governance-metric:nth-child(5) {
  --metric-start: #d94773;
  --metric-end: #f07891;
  --metric-value: #b83263;
  --metric-tint: rgba(240, 120, 145, 0.12);
  animation-delay: 0.16s;
}
.governance-metric:nth-child(6) {
  --metric-start: #0d9760;
  --metric-end: #28c98b;
  --metric-value: #087e50;
  --metric-tint: rgba(40, 201, 139, 0.12);
  animation-delay: 0.2s;
}

.governance-metric small {
  display: block;
  margin-top: 6px;
  color: #8090a2;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.governance-chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
  gap: 16px;
  margin: 4px 0 18px;
}

.chart-panel {
  position: relative;
  min-height: 370px;
  padding: 18px;
  border-color: rgba(151, 205, 248, 0.68);
  border-style: solid;
  border-width: 1px;
  border-radius: 8px;
  background:
    linear-gradient(rgba(31, 125, 242, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(24, 197, 200, 0.035) 1px, transparent 1px),
    linear-gradient(145deg, rgba(255, 255, 255, 0.96), rgba(231, 247, 255, 0.9), rgba(237, 255, 249, 0.84));
  background-size: 24px 24px, 24px 24px, auto;
  box-shadow: 0 18px 42px rgba(40, 95, 150, 0.12);
  overflow: hidden;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.chart-panel:nth-child(2) {
  background:
    linear-gradient(rgba(24, 197, 200, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(31, 125, 242, 0.035) 1px, transparent 1px),
    linear-gradient(145deg, rgba(255, 255, 255, 0.96), rgba(232, 250, 255, 0.9), rgba(232, 255, 244, 0.88));
  background-size: 24px 24px, 24px 24px, auto;
}

.chart-panel:hover {
  transform: translateY(-2px);
  border-color: rgba(76, 171, 246, 0.82);
  box-shadow: 0 22px 48px rgba(40, 95, 150, 0.16);
}

.chart-panel::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: linear-gradient(90deg, var(--primary), #42b4ff, var(--teal), #28c98b, var(--primary));
  background-size: 220% 100%;
  animation: accentFlow 6s linear infinite;
}

.chart-head {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.chart-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #123052;
  font-size: 17px;
  font-weight: 760;
}

.chart-title .el-icon {
  color: var(--primary);
  font-size: 19px;
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
  background: linear-gradient(120deg, rgba(229, 245, 255, 0.98), rgba(236, 252, 255, 0.92), rgba(230, 255, 242, 0.9));
  background-size: 180% 180%;
  animation: overviewFlow 8s ease-in-out infinite alternate;
}

.status-overview > div { min-width: 0; }
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

.status-overview-track,
.status-bar-track {
  border-radius: 999px;
  background: rgba(209, 226, 241, 0.9);
  overflow: hidden;
}

.status-overview-track { height: 12px; }
.status-overview-track div {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1f7df2, #18c5c8, #28f0a2);
  background-size: 180% 100%;
  transition: width 0.65s cubic-bezier(0.2, 0.75, 0.25, 1);
  animation: barColorFlow 3.2s linear infinite;
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

.status-bar-text strong { color: #0f5fa8; }
.status-bar-track { height: 14px; }
.status-bar-fill {
  position: relative;
  height: 100%;
  min-width: 0;
  border-radius: inherit;
  overflow: hidden;
  transition: width 0.65s cubic-bezier(0.2, 0.75, 0.25, 1);
}

.status-bar-fill::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(110deg, transparent 18%, rgba(255, 255, 255, 0.55) 46%, transparent 72%);
  transform: translateX(-110%);
  animation: barSheen 2.8s ease-in-out infinite;
}

.status-bar-fill.problem { background: linear-gradient(90deg, #1f7df2, #36a9ff, #18c5c8); }
.status-bar-fill.pending { background: linear-gradient(90deg, #f3ac3d, #ffd16b); }
.status-bar-fill.doing { background: linear-gradient(90deg, #1f7df2, #42b4ff, #18c5c8); }
.status-bar-fill.done { background: linear-gradient(90deg, #11a66a, #18c5c8); }
.status-bar-fill.rejected { background: linear-gradient(90deg, #8a98a8, #b6c1cc); }

.governance-table { margin-bottom: 0; }
.governance-workbench {
  position: relative;
  margin-bottom: 0;
  padding: 4px 16px 16px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(245, 251, 255, 0.96));
}

.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 20px;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 10px;
  background: #eaf5ff;
  color: #1f6fb8;
  font-size: 12px;
}

.table-toolbar {
  display: grid;
  grid-template-columns: 210px minmax(280px, 460px) 1fr;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: linear-gradient(120deg, rgba(237, 248, 255, 0.96), rgba(240, 255, 250, 0.9));
}

@keyframes metricEnter {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes metricSheen {
  from { transform: translateX(-120%); }
  to { transform: translateX(120%); }
}

@keyframes accentFlow {
  from { background-position: 0 0; }
  to { background-position: 220% 0; }
}

@keyframes overviewFlow {
  from { background-position: 0% 50%; }
  to { background-position: 100% 50%; }
}

@keyframes barColorFlow {
  from { background-position: 0 0; }
  to { background-position: 180% 0; }
}

@keyframes barSheen {
  0%, 45% { transform: translateX(-110%); }
  75%, 100% { transform: translateX(110%); }
}

.table-toolbar > span { color: var(--muted); font-size: 12px; text-align: right; }
.adjustments-toolbar { grid-template-columns: 210px minmax(300px, 520px); }
.issue-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.status-cell { display: grid; justify-items: start; gap: 5px; }
.status-cell small { color: #c94747; font-size: 11px; }
.muted-action { color: var(--muted); font-size: 13px; }

@media (max-width: 1280px) {
  .governance-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .governance-chart-grid { grid-template-columns: 1fr; }
}

@media (max-width: 1180px) and (min-width: 761px) {
  .governance-toolbar { grid-template-columns: repeat(6, minmax(0, 1fr)); }
  .governance-toolbar .span-3,
  .governance-toolbar .span-6 { grid-column: span 2; }
}

@media (max-width: 760px) {
  .governance-toolbar .span-3,
  .governance-toolbar .span-6 { grid-column: 1 / -1; }
  .governance-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .chart-head { align-items: flex-start; flex-direction: column; }
  .status-overview { grid-template-columns: 1fr; }
  .table-toolbar,
  .adjustments-toolbar { grid-template-columns: 1fr; }
  .table-toolbar > span { text-align: left; }
}

@media (prefers-reduced-motion: reduce) {
  .governance-metric,
  .governance-metric:hover::after,
  .chart-panel::before,
  .status-overview,
  .status-overview-track div,
  .status-bar-fill::after {
    animation: none;
  }

  .governance-metric,
  .chart-panel,
  .status-overview-track div,
  .status-bar-fill {
    transition: none;
  }
}
</style>
