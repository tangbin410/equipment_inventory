<template>
  <SectionTitle :icon="FolderOpened" title="导入 Excel 清单" muted="按九列表头填写，删除模板示例行；地点编码按文本填写，片区仅限：汇聚及核心、南区、北区、西区" />
  <div class="toolbar upload-actions">
    <div class="span-2">
      <input ref="fileInput" type="file" accept=".xlsx,.xls" hidden @change="onFileChange" />
      <el-button class="full-btn" @click="fileInput?.click()"><el-icon><UploadFilled /></el-icon>选择Excel文件</el-button>
    </div>
    <div class="span-2">
      <el-button class="full-btn" @click="downloadTemplate"><el-icon><Download /></el-icon>下载标准模板</el-button>
    </div>
    <div class="span-2">
      <el-button class="full-btn" :disabled="!file" @click="previewFile"><el-icon><Search /></el-icon>预检清单</el-button>
    </div>
    <div class="span-6">
      <el-button class="full-btn" type="primary" :disabled="!canImport" :loading="importing" @click="importFile">
        <el-icon><UploadFilled /></el-icon>导入并生成待盘点机房
      </el-button>
    </div>
  </div>
  <div v-if="file" class="page-muted selected-file">已选择：{{ file.name }}</div>
  <p class="page-muted">同一地点编码再次导入：未领取的旧任务作废；已领取的旧任务须先释放。未变化设备的有效结果、已登记机架和盘盈一并沿用，已完成任务保留历史。</p>

  <div v-if="preview" class="preview-block">
    <div class="metric-grid five">
      <div class="metric-card"><div class="metric-label">识别机房</div><div class="metric-value">{{ preview.room_count }}</div></div>
      <div class="metric-card"><div class="metric-label">设备总数</div><div class="metric-value">{{ preview.device_count }}</div></div>
      <div class="metric-card"><div class="metric-label">错误项</div><div class="metric-value">{{ errorCount }}</div></div>
      <div class="metric-card"><div class="metric-label">提醒项</div><div class="metric-value">{{ remindCount }}</div></div>
      <div class="metric-card"><div class="metric-label">空资产条码</div><div class="metric-value">{{ preview.missing_barcode }}</div></div>
    </div>

    <div class="table-wrap">
      <el-table border table-layout="auto" :data="preview.rooms" height="260">
        <el-table-column prop="room_name" label="机房名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="area_name" label="机房所在片区" min-width="130" />
        <el-table-column prop="room_code" label="地点编码" min-width="160" />
        <el-table-column prop="device_count" label="设备数" min-width="90" />
      </el-table>
    </div>

    <el-alert
      v-if="preview.valid"
      :type="remindCount ? 'warning' : 'success'"
      :title="remindCount ? '预检通过，但存在提醒项，建议确认后导入。' : '预检通过，可以正式导入。'"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else
      type="error"
      title="预检发现异常。可下载异常明细核对；确认可带入现场核查后再继续导入。"
      show-icon
      :closable="false"
    />
    <el-checkbox v-if="!preview.valid" v-model="forceImport">
      我已查看预检异常，确认仍继续导入并生成待盘点机房
    </el-checkbox>

    <template v-if="preview.issue_rows?.length">
      <el-button @click="downloadPreviewIssues(preview.issue_rows)"><el-icon><Download /></el-icon>下载预检异常明细</el-button>
      <div class="table-wrap">
        <el-table border table-layout="auto" :data="preview.issue_rows.slice(0, 100)" height="300">
          <el-table-column prop="级别" label="级别" min-width="80" />
          <el-table-column prop="Excel行号" label="Excel行号" min-width="100" />
          <el-table-column prop="机房名称" label="机房名称" min-width="190" show-overflow-tooltip />
          <el-table-column prop="设备名称" label="设备名称" min-width="170" show-overflow-tooltip />
          <el-table-column prop="问题类型" label="问题类型" min-width="150" show-overflow-tooltip />
          <el-table-column prop="处理建议" label="处理建议" min-width="220" show-overflow-tooltip />
        </el-table>
      </div>
    </template>
  </div>

  <SectionTitle :icon="Compass" title="片区待领取任务池" />
  <el-empty v-if="!pendingTasks.length" description="当前没有待领取机房。导入成功后，现场人员可在小程序领任务中领取同片区任务。" />
  <div v-else class="table-wrap">
    <el-table border table-layout="auto" :data="pendingTasks" height="360">
      <el-table-column prop="room_name" label="机房名称" min-width="220" show-overflow-tooltip />
      <el-table-column prop="area_name" label="机房所在片区" min-width="130" />
      <el-table-column prop="total" label="设备数" min-width="90" />
      <el-table-column label="状态" min-width="150">
        <template #default="{ row }">{{ taskStage(row) }}</template>
      </el-table-column>
      <el-table-column prop="replacement_notice" label="任务更新说明" min-width="260" show-overflow-tooltip />
      <el-table-column label="导入时间" min-width="160">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { Compass, Download, FolderOpened, Search, UploadFilled } from "@element-plus/icons-vue";
import SectionTitle from "../components/SectionTitle.vue";
import { apiGet, apiPost, downloadBlob } from "../api";
import type { Task, UploadPreview } from "../types";
import { downloadPreviewIssues, formatTime, taskStage } from "../utils";

const tasks = ref<Task[]>([]);
const file = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const preview = ref<UploadPreview | null>(null);
const forceImport = ref(false);
const importing = ref(false);

const pendingTasks = computed(() => tasks.value.filter((task) => task.status === "pending"));
const errorCount = computed(() => (preview.value?.issue_rows || []).filter((row) => row.级别 === "错误").length);
const remindCount = computed(() => (preview.value?.issue_rows || []).filter((row) => row.级别 === "提醒").length);
const canImport = computed(() => !!file.value && !!preview.value && (preview.value.valid || forceImport.value));

function onFileChange(event: Event) {
  const selected = (event.target as HTMLInputElement).files?.[0] || null;
  file.value = selected;
  preview.value = null;
  forceImport.value = false;
}

function formData() {
  const data = new FormData();
  if (file.value) data.append("file", file.value);
  return data;
}

async function loadTasks() {
  tasks.value = await apiGet<Task[]>("/tasks");
}

async function downloadTemplate() {
  await downloadBlob("/upload_template", "inventory_template.xlsx");
}

async function previewFile() {
  if (!file.value) return;
  preview.value = await apiPost<UploadPreview>("/upload_rooms/preview", formData());
}

async function importFile() {
  if (!file.value) return;
  importing.value = true;
  try {
    const data = formData();
    if (forceImport.value) data.append("force_import", "1");
    const result = await apiPost<{ message: string }>("/upload_rooms", data);
    ElMessage.success(result.message);
    preview.value = null;
    file.value = null;
    if (fileInput.value) fileInput.value.value = "";
    await loadTasks();
  } finally {
    importing.value = false;
  }
}

onMounted(loadTasks);
defineExpose({ refresh: loadTasks });
</script>

<style scoped>
.upload-actions :deep(.el-button) {
  height: 46px;
  font-weight: 650;
}

.selected-file { margin: -8px 0 16px; }
.preview-block { display: grid; gap: 16px; margin-bottom: 24px; }
.metric-grid.five { grid-template-columns: repeat(5, minmax(0, 1fr)); }
</style>
