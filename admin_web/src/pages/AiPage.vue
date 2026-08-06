<template>
  <div class="ai-console">
    <section class="ai-hero">
      <div class="hero-copy">
        <div class="hero-kicker">
          <span class="live-dot"></span>
          本地 Agent 接入
        </div>
        <h1>AI模型分析</h1>
        <p>脱敏分析盘点数据，生成整改清单。</p>
        <div class="hero-tags">
          <span>脱敏</span>
          <span>只读</span>
          <span>推理</span>
          <span>下载</span>
        </div>
      </div>

      <div class="data-lanes" aria-hidden="true">
        <span class="lane l1"></span>
        <span class="lane l2"></span>
        <span class="lane l3"></span>
        <span class="lane l4"></span>
        <i class="pulse p1"></i>
        <i class="pulse p2"></i>
        <i class="pulse p3"></i>
      </div>

      <div class="ai-core" aria-hidden="true">
        <div class="core-ring ring-one"></div>
        <div class="core-ring ring-two"></div>
        <div class="core-ring ring-three"></div>
        <div class="core-chip"><el-icon><Cpu /></el-icon></div>
        <span class="node n1"></span>
        <span class="node n2"></span>
        <span class="node n3"></span>
        <span class="scan-line"></span>
      </div>

      <div class="engine-board">
        <div class="engine-head">
          <span>模型状态</span>
          <strong>{{ status?.enabled ? "ONLINE" : "OFFLINE" }}</strong>
        </div>
        <div class="engine-grid">
          <div :class="['engine-tile', status?.enabled ? 'ok' : 'bad']">
            <i class="tile-aura"></i>
            <div class="tile-label"><span></span>启用状态</div>
            <strong>{{ status?.enabled ? "已启用" : "已关闭" }}</strong>
            <div class="tile-wave"><b></b><b></b><b></b></div>
          </div>
          <div :class="['engine-tile', modelUsable ? 'ok' : 'bad']">
            <i class="tile-aura"></i>
            <div class="tile-label"><span></span>模型可用性</div>
            <strong>{{ availabilityText }}</strong>
            <div class="tile-wave"><b></b><b></b><b></b></div>
          </div>
          <div :class="['engine-tile', keyOk ? 'ok' : 'bad']">
            <i class="tile-aura"></i>
            <div class="tile-label"><span></span>Key / Agent</div>
            <strong>{{ keyOk ? "已配置" : "未配置" }}</strong>
            <div class="tile-wave"><b></b><b></b><b></b></div>
          </div>
          <div :class="['engine-tile', modelNameOk ? 'ok' : 'bad']">
            <i class="tile-aura"></i>
            <div class="tile-label"><span></span>模型</div>
            <strong>{{ modelNameText }}</strong>
            <div class="tile-wave"><b></b><b></b><b></b></div>
          </div>
        </div>
      </div>
    </section>

    <section class="command-strip">
      <div class="command-main">
        <div class="command-title">
          <el-icon><Search /></el-icon>
          <span>分析任务检索</span>
        </div>
        <div class="command-form">
          <el-select v-model="queryField" class="field-select" @change="onQueryFieldChange">
            <el-option v-for="item in queryOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-select v-if="queryField === 'area_name'" v-model="queryText" class="value-select" placeholder="请选择片区">
            <el-option v-for="item in areaOptions" :key="item" :label="item" :value="item" />
          </el-select>
          <el-select v-else-if="queryField === 'field_stage'" v-model="queryText" class="value-select" placeholder="请选择现场状态">
            <el-option v-for="item in stageOptions" :key="item" :label="item" :value="item" />
          </el-select>
          <el-input v-else v-model="queryText" :disabled="queryField === '__all__'" :placeholder="queryField === '__all__' ? '全部任务将纳入候选' : `请输入${queryLabel}`" />
          <el-button type="primary" class="query-btn" @click="runQuery"><el-icon><Search /></el-icon>查询</el-button>
        </div>
      </div>
      <div class="command-actions">
        <el-button :type="status?.enabled ? 'default' : 'primary'" @click="toggleModel">
          <el-icon><SwitchButton /></el-icon>{{ status?.enabled ? "关闭模型" : "启用模型" }}
        </el-button>
        <el-button type="primary" :loading="checking" @click="checkModel">
          <el-icon><Connection /></el-icon>检测可用
        </el-button>
      </div>
    </section>

    <section class="selection-panel">
      <div class="selection-stat active">
        <span>候选任务</span>
        <strong>{{ queried ? candidates.length : completedTasks.length }}</strong>
      </div>
      <div class="selection-stat">
        <span>已选任务</span>
        <strong>{{ selectedTasks.length }}</strong>
      </div>
      <div class="selection-stat">
        <span>设备总数</span>
        <strong>{{ totalCount }}</strong>
      </div>
      <div class="selection-stat">
        <span>已盘设备</span>
        <strong>{{ scannedCount }}</strong>
      </div>
      <div class="selection-stat warn">
        <span>未盘设备</span>
        <strong>{{ pendingCount }}</strong>
      </div>
      <div class="selection-progress">
        <div class="progress-label">
          <span>已选任务盘点完成率</span>
          <strong>{{ selectedProgressPercent }}%</strong>
        </div>
        <div class="progress-track"><div class="progress-fill" :style="{ width: `${selectedProgressPercent}%` }"></div></div>
      </div>
    </section>

    <section class="analysis-grid">
      <div class="task-workbench">
        <div class="workbench-head">
          <div>
            <span>已选任务范围</span>
            <strong>{{ selectedTasks.length ? `${selectedTasks.length} 个任务待分析` : "等待选择分析任务" }}</strong>
          </div>
          <el-tag effect="plain" type="success">只读脱敏</el-tag>
        </div>
        <div v-if="!selectedTasks.length" class="selected-empty">
          <div class="empty-orbit"><span></span></div>
          <strong>请先查询并勾选任务</strong>
          <p>查询结果会在弹窗中呈现，选中后再生成 AI 分析报告。</p>
        </div>
        <div v-else class="selected-list">
          <div v-for="task in selectedTasks.slice(0, 6)" :key="task.id" class="selected-task">
            <span>{{ task.area_name || "汇聚及核心" }}</span>
            <strong>{{ task.room_name }}</strong>
            <small>{{ taskStage(task) }} · {{ task.scanned || 0 }}/{{ task.total || 0 }}</small>
          </div>
          <div v-if="selectedTasks.length > 6" class="selected-more">另有 {{ selectedTasks.length - 6 }} 个任务已纳入分析范围</div>
        </div>
      </div>

      <aside class="analysis-side">
        <div class="side-head">
          <span>分析控制</span>
          <strong>{{ canAnalyze ? "READY" : "WAIT" }}</strong>
        </div>
        <div class="side-meter">
          <div class="meter-ring" :style="{ '--progress': selectedProgressPercent }">
            <strong>{{ selectedProgressPercent }}%</strong>
            <span>完成率</span>
          </div>
        </div>
        <div class="side-info">
          <div><span>任务范围</span><strong>{{ selectedTasks.length ? `${selectedTasks.length} 个任务` : "未选择" }}</strong></div>
          <div><span>分析状态</span><strong>{{ modelUsable ? "模型可用" : "待检测" }}</strong></div>
          <div><span>输出方式</span><strong>清单报告</strong></div>
        </div>
        <el-button type="primary" class="analysis-btn" :disabled="!canAnalyze" :loading="analyzing" @click="runAnalysis">
          <el-icon><MagicStick /></el-icon>生成 AI 分析
        </el-button>
        <el-button class="download-btn" @click="downloadLastReport">
          <el-icon><Download /></el-icon>下载 AI 分析报告
        </el-button>
        <p class="side-note">真实机房、设备、条码会在本地 agent 脱敏后再发送。</p>
      </aside>
    </section>

    <el-dialog v-model="resultDialogVisible" title="查询结果" width="980px" class="result-dialog">
      <el-empty v-if="!candidates.length" description="没有查到相关任务。" />
      <template v-else>
        <div class="dialog-tip">查询到 {{ candidates.length }} 个任务，请勾选需要交给 AI 分析的任务。</div>
        <div class="table-wrap compact">
          <el-table border table-layout="auto" :data="candidates" height="360" @selection-change="onSelectionChange">
            <el-table-column type="selection" width="48" />
            <el-table-column prop="room_name" label="机房" min-width="190" show-overflow-tooltip />
            <el-table-column prop="area_name" label="片区" min-width="100" />
            <el-table-column prop="task_name" label="任务名称" min-width="210" show-overflow-tooltip />
            <el-table-column label="领取人" min-width="100"><template #default="{ row }">{{ row.assigned_name || "-" }}</template></el-table-column>
            <el-table-column label="现场状态" min-width="112"><template #default="{ row }"><el-tag :type="statusTag(taskStage(row))" effect="plain">{{ taskStage(row) }}</el-tag></template></el-table-column>
            <el-table-column label="进度" min-width="90"><template #default="{ row }">{{ row.scanned || 0 }}/{{ row.total || 0 }}</template></el-table-column>
          </el-table>
        </div>
      </template>
      <template #footer>
        <span class="dialog-footer-note">已选 {{ selectedTasks.length }} 个任务</span>
        <el-button @click="resultDialogVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!selectedTasks.length" @click="resultDialogVisible = false">确定</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { Connection, Cpu, Download, MagicStick, Search, SwitchButton } from "@element-plus/icons-vue";
import { apiGet, apiPatch, apiPost, downloadBlob, getToken } from "../api";
import { readAiCheckSession, saveAiCheckSession, setAiChatTaskIds } from "../aiSession";
import type { AiAnalysisResult, AiStatus, Task, TaskRecord } from "../types";
import { downloadAiReport, taskStage } from "../utils";

const status = ref<AiStatus | null>(null);
const checkMessage = ref("");
const checkOk = ref(false);
const checking = ref(false);
const analyzing = ref(false);
const summary = ref<Task[]>([]);
const queried = ref(false);
const queryField = ref("__all__");
const queryText = ref("");
const candidates = ref<Task[]>([]);
const selectedTasks = ref<Task[]>([]);
const lastResult = ref<AiAnalysisResult | null>(null);
const resultDialogVisible = ref(false);

const queryOptions = [
  { label: "全部查询", value: "__all__" },
  { label: "机房名称", value: "room_name" },
  { label: "任务名称", value: "task_name" },
  { label: "片区", value: "area_name" },
  { label: "领取人", value: "assigned_name" },
  { label: "现场状态", value: "field_stage" },
];
const stageOptions = ["现场完成"];

const queryLabel = computed(() => queryOptions.find((item) => item.value === queryField.value)?.label || "查询内容");
const areaOptions = ["汇聚及核心", "南区", "北区", "西区"];
const keyOk = computed(() => Boolean(status.value?.api_key_configured || status.value?.external_agent_url));
const modelConfigured = computed(() => Boolean(status.value?.enabled && status.value?.configured));
const modelUsable = computed(() => Boolean(modelConfigured.value && checkOk.value));
const modelNameText = computed(() => {
  const modelName = (status.value?.model_name || "").trim();
  if (!modelName || /^https?:\/\//i.test(modelName)) return "待检测";
  return modelName;
});
const modelNameOk = computed(() => modelNameText.value !== "待检测");
const availabilityText = computed(() => {
  if (!status.value?.enabled) return "不可用";
  if (!status.value?.configured) return "未配置";
  if (!checkMessage.value) return "待检测";
  return checkOk.value ? "可用" : "不可用";
});
const inventoryRecords = computed(() => selectedTasks.value.flatMap((task) => task.records || []).filter((record) => (record.record_type || "normal") !== "surplus"));
const totalCount = computed(() => inventoryRecords.value.length);
const scannedCount = computed(() => inventoryRecords.value.filter((record: TaskRecord) => record.match_status !== "未扫").length);
const pendingCount = computed(() => inventoryRecords.value.filter((record: TaskRecord) => record.match_status === "未扫").length);
const selectedProgressPercent = computed(() => totalCount.value ? Math.round((scannedCount.value / totalCount.value) * 100) : 0);
const canAnalyze = computed(() => Boolean(selectedTasks.value.length && modelUsable.value && !analyzing.value));
const completedTasks = computed(() => summary.value.filter(
  (task) => task.status === "done" && Boolean(task.field_completed_at || task.completed_at),
));

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

async function loadStatus() {
  status.value = await apiGet<AiStatus>("/ai/status");
  restoreModelCheckSession();
}

function restoreModelCheckSession() {
  const cached = readAiCheckSession(getToken());
  if (!cached) return;
  checkOk.value = cached.ok;
  checkMessage.value = cached.message;
  if (status.value && cached.modelName) {
    status.value.model_name = cached.modelName;
  }
}

async function loadSummary() {
  summary.value = await apiGet<Task[]>("/summary");
}

async function toggleModel() {
  if (!status.value) return;
  status.value = await apiPatch<AiStatus>("/ai/status", { enabled: !status.value.enabled });
  checkMessage.value = "";
  checkOk.value = false;
  saveAiCheckSession(getToken(), { ok: false, message: "", modelName: status.value.model_name || "" });
}

async function checkModel() {
  checking.value = true;
  try {
    const result = await apiPost<{ ok: boolean; message: string; status: AiStatus }>("/ai/check");
    checkOk.value = Boolean(result.ok);
    checkMessage.value = result.message || (result.ok ? "模型可用。" : "模型不可用。");
    status.value = result.status;
    saveAiCheckSession(getToken(), {
      ok: checkOk.value,
      message: checkMessage.value,
      modelName: result.status?.model_name || "",
    });
    ElMessage({
      message: checkMessage.value,
      type: result.ok ? "success" : "error",
      duration: 3000,
      showClose: true,
    });
  } finally {
    checking.value = false;
  }
}

function runQuery() {
  queried.value = true;
  selectedTasks.value = [];
  if (queryField.value === "__all__") {
    candidates.value = completedTasks.value;
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
  candidates.value = completedTasks.value.filter((task) => taskValue(task, queryField.value).toLowerCase().includes(needle));
  resultDialogVisible.value = true;
}

function onSelectionChange(rows: Task[]) {
  selectedTasks.value = rows;
}

function statusTag(status: string) {
  if (status.includes("完成")) return "success";
  if (status.includes("盘点")) return "primary";
  if (status.includes("待")) return "warning";
  return "info";
}

async function runAnalysis() {
  analyzing.value = true;
  try {
    const result = await apiPost<AiAnalysisResult>("/ai/analyze", { task_ids: selectedTasks.value.map((task) => task.id) }, { timeout: 90000 });
    lastResult.value = result;
    if (result.status === "success") {
      ElMessage.success(`AI 分析完成，生成 ${result.adjustment_drafts?.length || 0} 条待审核整改草稿。`);
    }
    else ElMessage.error(`AI 分析失败：${result.error_message || "-"}`);
  } finally {
    analyzing.value = false;
  }
}

async function downloadLastReport() {
  if (lastResult.value) {
    downloadAiReport(lastResult.value);
    return;
  }
  await downloadBlob("/ai/results/latest/download", "AI分析报告_最近一次.xlsx");
}

watch(selectedTasks, (tasks) => {
  setAiChatTaskIds(tasks.map((task) => task.id));
}, { deep: true });

onMounted(async () => {
  await Promise.all([loadStatus(), loadSummary()]);
});
</script>

<style scoped>
.ai-console {
  display: grid;
  gap: 18px;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}

.ai-console > * {
  min-width: 0;
}

.ai-hero {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(340px, 420px);
  gap: 20px;
  align-items: stretch;
  min-height: 250px;
  padding: 22px;
  border: 1px solid rgba(107, 190, 255, 0.32);
  border-radius: 8px;
  background:
    linear-gradient(115deg, rgba(9, 54, 96, 0.96), rgba(15, 99, 155, 0.9) 48%, rgba(18, 196, 200, 0.72)),
    radial-gradient(circle at 18% 18%, rgba(72, 210, 255, 0.35), transparent 34%);
  box-shadow: 0 18px 48px rgba(26, 112, 185, 0.2);
  overflow: hidden;
}

.ai-hero::before,
.ai-hero::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.ai-hero::before {
  background:
    linear-gradient(rgba(203, 240, 255, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(203, 240, 255, 0.08) 1px, transparent 1px);
  background-size: 28px 28px;
  mask-image: linear-gradient(90deg, #000, transparent 78%);
}

.ai-hero::after {
  width: 34%;
  background: linear-gradient(90deg, transparent, rgba(148, 234, 255, 0.18), transparent);
  transform: skewX(-18deg);
  animation: heroSweep 4.8s ease-in-out infinite;
}

.hero-copy,
.ai-core,
.engine-board {
  position: relative;
  z-index: 1;
}

.hero-copy {
  align-self: center;
  min-width: 0;
}

.hero-kicker {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  padding: 0 12px;
  border: 1px solid rgba(190, 240, 255, 0.3);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  color: rgba(235, 250, 255, 0.9);
  font-size: 13px;
  font-weight: 680;
  backdrop-filter: blur(8px);
}

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #28f0a2;
  box-shadow: 0 0 0 6px rgba(40, 240, 162, 0.14), 0 0 18px rgba(40, 240, 162, 0.72);
  animation: dotPulse 1.8s ease-in-out infinite;
}

.hero-copy h1 {
  margin: 16px 0 10px;
  color: #fff;
  font-size: 34px;
  line-height: 1.05;
  font-weight: 860;
}

.hero-copy p {
  max-width: 360px;
  margin: 0;
  color: rgba(229, 247, 255, 0.72);
  font-size: 15px;
  line-height: 1.55;
}

.hero-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
}

.hero-tags span {
  min-height: 28px;
  display: inline-flex;
  align-items: center;
  padding: 0 10px;
  border: 1px solid rgba(190, 240, 255, 0.24);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.1);
  color: rgba(243, 252, 255, 0.9);
  font-size: 13px;
}

.data-lanes {
  position: absolute;
  top: 56px;
  bottom: 48px;
  left: 300px;
  right: 505px;
  z-index: 1;
  pointer-events: none;
  opacity: 0.92;
}

.data-lanes::before {
  content: "";
  position: absolute;
  inset: 14px 0;
  border: 1px solid rgba(125, 226, 255, 0.12);
  border-left: 0;
  border-right: 0;
  background:
    linear-gradient(90deg, transparent, rgba(74, 218, 255, 0.08), transparent),
    radial-gradient(circle at 72% 50%, rgba(40, 240, 162, 0.13), transparent 28%);
  transform: skewX(-13deg);
}

.lane {
  position: absolute;
  left: 0;
  right: 0;
  height: 1px;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, rgba(140, 235, 255, 0.2), rgba(44, 244, 185, 0.76), transparent);
  box-shadow: 0 0 14px rgba(72, 220, 255, 0.24);
  transform-origin: right center;
  overflow: hidden;
}

.lane::after {
  content: "";
  position: absolute;
  width: 78px;
  inset: 0 auto 0 -90px;
  border-radius: inherit;
  background: linear-gradient(90deg, transparent, rgba(242, 254, 255, 0.92), transparent);
  animation: lanePulse 2.6s ease-in-out infinite;
}

.l1 { top: 18%; transform: rotate(-10deg); }
.l2 { top: 39%; transform: rotate(-3deg); opacity: 0.72; }
.l3 { top: 61%; transform: rotate(8deg); opacity: 0.86; }
.l4 { top: 78%; transform: rotate(-6deg); opacity: 0.58; }
.l2::after { animation-delay: 0.4s; }
.l3::after { animation-delay: 0.8s; }
.l4::after { animation-delay: 1.2s; }

.pulse {
  position: absolute;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #7df6ff;
  box-shadow: 0 0 0 6px rgba(125, 246, 255, 0.11), 0 0 18px rgba(125, 246, 255, 0.62);
  animation: pulseDrift 3.4s ease-in-out infinite;
}

.p1 { left: 22%; top: 26%; }
.p2 { left: 54%; top: 58%; animation-delay: 0.7s; background: #28f0a2; }
.p3 { left: 82%; top: 36%; animation-delay: 1.3s; background: #ffd56b; }

.ai-core {
  position: absolute;
  top: 50%;
  left: calc(100% - 740px);
  width: 300px;
  height: 240px;
  min-height: 240px;
  display: grid;
  place-items: center;
  transform: translate(-50%, -50%);
  z-index: 2;
  opacity: 1;
  pointer-events: none;
  filter: drop-shadow(0 0 22px rgba(72, 220, 255, 0.38));
}

.ai-core::before,
.ai-core::after {
  content: "";
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
}

.ai-core::before {
  inset: 10px 30px;
  background:
    radial-gradient(circle, rgba(105, 240, 255, 0.18), transparent 58%),
    conic-gradient(from 90deg, transparent 0 28%, rgba(38, 242, 174, 0.42) 36%, transparent 48% 100%);
  filter: blur(2px);
  animation: coreGlowSpin 8s linear infinite;
}

.ai-core::after {
  inset: 42px 62px;
  border: 1px solid rgba(120, 236, 255, 0.18);
  box-shadow:
    0 0 28px rgba(72, 220, 255, 0.26),
    inset 0 0 30px rgba(40, 240, 162, 0.16);
  animation: corePulse 2.4s ease-in-out infinite;
}

.core-ring {
  position: absolute;
  border-radius: 50%;
  border: 1px solid rgba(174, 238, 255, 0.46);
  box-shadow:
    0 0 24px rgba(72, 220, 255, 0.14),
    inset 0 0 24px rgba(70, 210, 255, 0.14);
}

.core-ring::before {
  content: "";
  position: absolute;
  inset: -2px;
  border-radius: inherit;
  background: conic-gradient(from 0deg, transparent 0 62%, rgba(54, 244, 196, 0.98) 70%, rgba(103, 227, 255, 0.55) 78%, transparent 86% 100%);
  -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 calc(100% - 3px));
  mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 calc(100% - 3px));
  animation: orbitGlow 2.7s linear infinite;
}

.ring-one {
  width: 224px;
  height: 224px;
  animation: spin 12s linear infinite;
}

.ring-two {
  width: 166px;
  height: 166px;
  border-style: dashed;
  animation: spin 7s linear infinite reverse;
}

.ring-three {
  width: 108px;
  height: 108px;
  border-color: rgba(40, 240, 162, 0.58);
  background: radial-gradient(circle, rgba(40, 240, 162, 0.16), transparent 62%);
  animation: breathe 2.2s ease-in-out infinite;
}

.core-chip {
  position: relative;
  z-index: 2;
  width: 76px;
  height: 76px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(216, 250, 255, 0.64);
  border-radius: 22px;
  background: linear-gradient(135deg, #f7feff, #b6f4ff);
  color: #1268c7;
  font-size: 34px;
  box-shadow:
    0 0 0 10px rgba(79, 224, 255, 0.08),
    0 0 38px rgba(79, 224, 255, 0.72),
    inset 0 -10px 18px rgba(36, 152, 220, 0.16);
  animation: chipFloat 3.2s ease-in-out infinite;
}

.core-chip::before,
.core-chip::after {
  content: "";
  position: absolute;
  inset: -16px;
  border-radius: 28px;
  border: 1px solid rgba(177, 247, 255, 0.24);
  animation: chipEcho 2.8s ease-in-out infinite;
}

.core-chip::after {
  inset: -26px;
  opacity: 0.52;
  animation-delay: 0.7s;
}

.node {
  position: absolute;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #28f0a2;
  box-shadow: 0 0 16px rgba(40, 240, 162, 0.8);
}

.n1 { top: 38px; right: 66px; animation: floatNode 3s ease-in-out infinite; }
.n2 { left: 46px; bottom: 58px; animation: floatNode 3.4s ease-in-out infinite 0.4s; }
.n3 { right: 44px; bottom: 48px; background: #ffd56b; box-shadow: 0 0 16px rgba(255, 213, 107, 0.8); animation: floatNode 3.1s ease-in-out infinite 0.8s; }

.scan-line {
  position: absolute;
  left: 8%;
  right: 8%;
  height: 2px;
  border-radius: 99px;
  background: linear-gradient(90deg, transparent, rgba(183, 248, 255, 0.98), rgba(40, 240, 162, 0.72), transparent);
  box-shadow:
    0 0 18px rgba(108, 231, 255, 0.82),
    0 0 34px rgba(40, 240, 162, 0.28);
  transform: rotate(-16deg);
  animation: scanMove 2.4s ease-in-out infinite;
}

.engine-board {
  align-self: stretch;
  min-width: 0;
  max-width: 420px;
  justify-self: end;
  padding: 18px;
  border: 1px solid rgba(197, 242, 255, 0.38);
  border-radius: 8px;
  background:
    linear-gradient(145deg, rgba(6, 38, 70, 0.62), rgba(20, 104, 137, 0.52)),
    rgba(7, 42, 76, 0.52);
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.06),
    0 0 34px rgba(72, 210, 255, 0.18);
  backdrop-filter: blur(14px);
}

.engine-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  color: rgba(235, 250, 255, 0.84);
  font-size: 14px;
}

.engine-head strong {
  color: #28f0a2;
  font-size: 13px;
  letter-spacing: 0.08em;
  text-shadow: 0 0 14px rgba(40, 240, 162, 0.55);
}

.engine-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
}

.engine-tile {
  position: relative;
  min-width: 0;
  min-height: 88px;
  padding: 14px 12px 12px;
  border: 1px solid rgba(130, 226, 255, 0.28);
  border-radius: 12px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.16), rgba(72, 210, 255, 0.08) 46%, rgba(40, 240, 162, 0.08)),
    rgba(255, 255, 255, 0.06);
  box-shadow:
    inset 0 0 18px rgba(99, 222, 255, 0.08),
    0 10px 24px rgba(0, 30, 58, 0.12);
  overflow: hidden;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.engine-tile:hover {
  transform: translateY(-2px);
  border-color: rgba(171, 246, 255, 0.48);
  box-shadow:
    inset 0 0 22px rgba(99, 222, 255, 0.12),
    0 14px 32px rgba(0, 30, 58, 0.18),
    0 0 28px rgba(72, 210, 255, 0.2);
}

.engine-tile::before {
  content: "";
  position: absolute;
  inset: 0;
  padding: 1px;
  border-radius: inherit;
  background: linear-gradient(135deg, rgba(40, 240, 162, 0.88), rgba(66, 200, 255, 0.26), rgba(255, 255, 255, 0.18));
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  animation: capsuleBreath 3.2s ease-in-out infinite;
  pointer-events: none;
}

.engine-tile::after {
  content: "";
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 9px;
  height: 1px;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, rgba(116, 242, 255, 0.92), transparent);
  transform: translateX(-35%);
  animation: railFlow 2.8s ease-in-out infinite;
}

.engine-tile.bad::before {
  background: linear-gradient(135deg, rgba(255, 107, 107, 0.86), rgba(255, 213, 107, 0.28), rgba(255, 255, 255, 0.16));
}

.tile-aura {
  position: absolute;
  right: -24px;
  top: -30px;
  width: 112px;
  height: 112px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(40, 240, 162, 0.22), transparent 68%);
  filter: blur(1px);
  animation: auraDrift 4s ease-in-out infinite;
}

.engine-tile.bad .tile-aura {
  background: radial-gradient(circle, rgba(255, 107, 107, 0.18), transparent 66%);
}

.tile-label {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 9px;
  color: rgba(229, 247, 255, 0.66);
  font-size: 13px;
  font-weight: 680;
}

.tile-label span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #28f0a2;
  box-shadow: 0 0 12px rgba(40, 240, 162, 0.82);
}

.engine-tile.bad .tile-label span {
  background: #ff7b7b;
  box-shadow: 0 0 12px rgba(255, 107, 107, 0.68);
}

.engine-tile strong {
  position: relative;
  z-index: 1;
  display: block;
  color: #f8fdff;
  max-width: 100%;
  font-size: clamp(16px, 1.1vw, 20px);
  line-height: 1.15;
  font-weight: 860;
  word-break: break-word;
  overflow-wrap: anywhere;
  text-shadow: 0 0 14px rgba(148, 234, 255, 0.26);
}

.tile-wave {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 12px;
  z-index: 1;
  display: grid;
  grid-template-columns: 1.1fr 1.7fr 1fr;
  gap: 8px;
  height: 3px;
}

.tile-wave b {
  display: block;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(40, 240, 162, 0.18), rgba(95, 232, 255, 0.72), rgba(40, 240, 162, 0.18));
  box-shadow: 0 0 10px rgba(66, 200, 255, 0.28);
  transform-origin: left center;
  animation: capsuleMeter 2.6s ease-in-out infinite;
}

.tile-wave b:nth-child(2) { animation-delay: 0.22s; }
.tile-wave b:nth-child(3) { animation-delay: 0.44s; }

.engine-tile.bad .tile-wave b {
  background: linear-gradient(90deg, rgba(255, 107, 107, 0.22), rgba(255, 213, 107, 0.92));
  box-shadow: 0 0 12px rgba(255, 167, 91, 0.34);
}

.engine-tile.ok {
  box-shadow:
    inset 0 -34px 52px rgba(40, 240, 162, 0.08),
    0 10px 24px rgba(0, 30, 58, 0.12);
}

.engine-tile.bad {
  box-shadow:
    inset 0 -34px 52px rgba(255, 107, 107, 0.08),
    0 10px 24px rgba(0, 30, 58, 0.12);
}

.command-strip {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: end;
  padding: 16px;
  border: 1px solid rgba(164, 211, 247, 0.62);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.command-strip::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: linear-gradient(90deg, #1f7df2, #18c5c8, #28f0a2);
}

.command-main,
.command-actions {
  position: relative;
  z-index: 1;
  min-width: 0;
}

.command-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  color: #153654;
  font-size: 16px;
  font-weight: 780;
}

.command-title .el-icon {
  color: var(--primary);
}

.command-form {
  display: grid;
  grid-template-columns: 140px minmax(160px, 1fr) 160px;
  gap: 10px;
}

.field-select,
.query-btn {
  width: 100%;
}

.query-btn {
  min-height: 40px;
  font-weight: 780;
}

.command-actions {
  display: grid;
  grid-template-columns: 120px 120px;
  gap: 10px;
}

.command-actions .el-button {
  width: 100%;
  min-height: 40px;
  margin-left: 0;
  font-weight: 760;
}

.selection-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
  gap: 12px;
}

.selection-stat,
.selection-progress {
  min-width: 0;
  min-height: 92px;
  padding: 14px;
  border: 1px solid rgba(167, 210, 246, 0.56);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(239, 248, 255, 0.88));
  box-shadow: var(--shadow-soft);
}

.selection-stat {
  position: relative;
  overflow: hidden;
}

.selection-stat::after {
  content: "";
  position: absolute;
  right: -18px;
  bottom: -22px;
  width: 70px;
  height: 70px;
  border: 12px solid rgba(31, 125, 242, 0.08);
  border-radius: 50%;
}

.selection-stat span {
  display: block;
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 9px;
}

.selection-stat strong {
  color: #1267bd;
  font-size: 30px;
  line-height: 1;
}

.selection-stat.warn strong {
  color: #d0731c;
}

.selection-stat.active {
  background: linear-gradient(135deg, rgba(232, 246, 255, 0.96), rgba(231, 255, 251, 0.9));
}

.selection-progress {
  display: grid;
  align-content: center;
}

.progress-label {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: #31536f;
  font-size: 14px;
  font-weight: 720;
}

.progress-label strong {
  color: #1267bd;
  font-size: 20px;
}

.progress-track {
  height: 12px;
  border-radius: 999px;
  background: #dfeaf5;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1f7df2, #18c5c8, #28f0a2);
  transition: width 0.26s ease;
}

.analysis-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 300px);
  gap: 16px;
  align-items: start;
}

.task-workbench,
.analysis-side,
.ai-empty {
  min-width: 0;
  border: 1px solid rgba(164, 211, 247, 0.62);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: var(--shadow);
}

.task-workbench {
  padding: 16px;
}

.workbench-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.workbench-head div {
  display: grid;
  gap: 4px;
}

.workbench-head span {
  color: var(--muted);
  font-size: 13px;
}

.workbench-head strong {
  color: #123052;
  font-size: 18px;
}

.selected-empty {
  min-height: 260px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: var(--muted);
  text-align: center;
}

.selected-empty strong {
  color: #153654;
  font-size: 18px;
}

.selected-empty p {
  margin: 0;
  font-size: 14px;
}

.selected-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.selected-task,
.selected-more {
  min-width: 0;
  padding: 13px 14px;
  border: 1px solid rgba(164, 211, 247, 0.54);
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(247, 252, 255, 0.96), rgba(234, 248, 255, 0.88));
}

.selected-task span,
.selected-task small {
  display: block;
  color: var(--muted);
  font-size: 13px;
}

.selected-task strong {
  display: block;
  margin: 5px 0;
  color: #123052;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-more {
  grid-column: 1 / -1;
  color: var(--muted);
  text-align: center;
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
  max-width: 100%;
  overflow-x: auto;
}

.analysis-side {
  position: sticky;
  top: 76px;
  padding: 16px;
  overflow: hidden;
}

.analysis-side::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: linear-gradient(90deg, #28f0a2, #18c5c8, #1f7df2);
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #153654;
  font-size: 16px;
  font-weight: 780;
}

.side-head strong {
  color: #0f79d8;
  font-size: 13px;
  letter-spacing: 0.08em;
}

.side-meter {
  display: grid;
  place-items: center;
  margin: 18px 0;
}

.meter-ring {
  width: 130px;
  height: 130px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background:
    radial-gradient(circle at center, #fff 0 55%, transparent 56%),
    conic-gradient(#1f7df2 calc(var(--progress) * 1%), #18c5c8 calc(var(--progress) * 1%), #e2edf7 0);
  box-shadow: 0 14px 28px rgba(31, 125, 242, 0.14);
}

.meter-ring strong,
.meter-ring span {
  grid-area: 1 / 1;
}

.meter-ring strong {
  align-self: center;
  margin-top: -14px;
  color: #1267bd;
  font-size: 28px;
}

.meter-ring span {
  align-self: center;
  margin-top: 36px;
  color: var(--muted);
  font-size: 13px;
}

.side-info {
  display: grid;
  gap: 9px;
  margin-bottom: 14px;
}

.side-info div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid rgba(164, 211, 247, 0.36);
}

.side-info span {
  color: var(--muted);
  font-size: 13px;
}

.side-info strong {
  color: #153654;
  font-size: 14px;
}

.analysis-btn,
.download-btn {
  width: 100%;
  min-height: 42px;
  margin-left: 0;
  margin-top: 10px;
  font-weight: 780;
}

.side-note {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.ai-empty {
  min-height: 240px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  padding: 30px;
  color: var(--muted);
  text-align: center;
  background:
    linear-gradient(rgba(31, 125, 242, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(24, 197, 200, 0.035) 1px, transparent 1px),
    rgba(255, 255, 255, 0.72);
  background-size: 28px 28px, 28px 28px, auto;
}

.ai-empty strong {
  color: #153654;
  font-size: 18px;
}

.ai-empty p {
  margin: 0;
  font-size: 14px;
}

.empty-orbit {
  position: relative;
  width: 92px;
  height: 92px;
  border: 1px solid rgba(31, 125, 242, 0.22);
  border-radius: 50%;
  animation: spin 10s linear infinite;
}

.empty-orbit::before,
.empty-orbit::after,
.empty-orbit span {
  content: "";
  position: absolute;
  border-radius: 50%;
}

.empty-orbit::before {
  inset: 18px;
  border: 1px dashed rgba(24, 197, 200, 0.32);
}

.empty-orbit::after {
  width: 12px;
  height: 12px;
  top: 8px;
  left: 50%;
  background: #1f7df2;
  box-shadow: 0 0 16px rgba(31, 125, 242, 0.6);
}

.empty-orbit span {
  inset: 34px;
  background: linear-gradient(135deg, #1f7df2, #18c5c8);
}

@keyframes heroSweep {
  0% { left: -44%; opacity: 0; }
  20% { opacity: 1; }
  55% { left: 112%; opacity: 0; }
  100% { left: 112%; opacity: 0; }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes coreGlowSpin {
  to { transform: rotate(360deg); }
}

@keyframes corePulse {
  0%, 100% { transform: scale(0.9); opacity: 0.48; }
  50% { transform: scale(1.12); opacity: 0.9; }
}

@keyframes orbitGlow {
  to { transform: rotate(360deg); }
}

@keyframes chipFloat {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-4px) scale(1.04); }
}

@keyframes chipEcho {
  0%, 100% { transform: scale(0.9); opacity: 0.14; }
  50% { transform: scale(1.08); opacity: 0.44; }
}

@keyframes breathe {
  0%, 100% { transform: scale(0.94); opacity: 0.7; }
  50% { transform: scale(1.08); opacity: 1; }
}

@keyframes scanMove {
  0%, 100% { top: 48px; opacity: 0.24; }
  50% { top: 160px; opacity: 1; }
}

@keyframes floatNode {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

@keyframes dotPulse {
  0%, 100% { transform: scale(0.92); }
  50% { transform: scale(1.18); }
}

@keyframes lanePulse {
  0% { left: -90px; opacity: 0; }
  18% { opacity: 0.85; }
  62% { opacity: 0.95; }
  100% { left: calc(100% + 20px); opacity: 0; }
}

@keyframes pulseDrift {
  0%, 100% { transform: translate(0, 0) scale(0.88); opacity: 0.48; }
  50% { transform: translate(14px, -8px) scale(1.28); opacity: 1; }
}

@keyframes capsuleBreath {
  0%, 100% { opacity: 0.48; }
  50% { opacity: 0.92; }
}

@keyframes railFlow {
  0%, 100% { opacity: 0.22; transform: translateX(-36%) scaleX(0.58); }
  50% { opacity: 0.88; transform: translateX(36%) scaleX(0.9); }
}

@keyframes auraDrift {
  0%, 100% { transform: translate(0, 0) scale(0.92); opacity: 0.6; }
  50% { transform: translate(-10px, 12px) scale(1.05); opacity: 0.95; }
}

@keyframes capsuleMeter {
  0%, 100% { transform: scaleX(0.42); opacity: 0.38; }
  50% { transform: scaleX(1); opacity: 0.92; }
}

@media (max-width: 1320px) {
  .ai-hero {
    grid-template-columns: minmax(0, 1fr) minmax(320px, 380px);
  }

  .ai-core,
  .data-lanes {
    display: none;
  }
}

@media (max-width: 1180px) {
  .ai-hero,
  .command-strip,
  .analysis-grid {
    grid-template-columns: 1fr;
  }

  .selection-panel {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .selection-progress {
    grid-column: 1 / -1;
  }

  .analysis-side {
    position: relative;
    top: auto;
  }
}

@media (max-width: 760px) {
  .ai-hero {
    padding: 18px;
  }

  .hero-copy h1 {
    font-size: 28px;
  }

  .engine-grid,
  .command-form,
  .command-actions,
  .selection-panel,
  .selected-list {
    grid-template-columns: 1fr;
  }
}
</style>
