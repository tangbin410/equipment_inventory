<template>
  <PageHeader :icon="Files" title="资产信息" desc="管理员定期上传静态资产台账，按资产条码查询原始信息" />

  <div class="asset-import-layout" :style="!permissions.manageAssets ? { gridTemplateColumns: '1fr' } : undefined">
    <section v-if="permissions.manageAssets" class="asset-import-main">
      <div class="section-heading">
        <div>
          <h2>更新资产台账</h2>
          <p>上传 Excel 并确认条码列。三方比对还需资源名称、地点编码、规格型号三列。</p>
        </div>
        <el-tag v-if="status.current" type="success" effect="plain">当前快照可用</el-tag>
        <el-tag v-else type="info" effect="plain">尚未导入</el-tag>
      </div>

      <input ref="fileInput" type="file" accept=".xlsx" hidden :disabled="importing" @change="onFileChange" />
      <button class="file-drop" :class="{ 'has-file': file }" :disabled="importing" @click="fileInput?.click()">
        <span class="file-icon"><el-icon><Document /></el-icon></span>
        <span class="file-copy">
          <strong>{{ file?.name || "选择资产信息 Excel 文件" }}</strong>
          <small>{{ file ? "文件已就绪，可读取表头" : "支持 .xlsx；三方比对需资源名称、地点编码、规格型号" }}</small>
        </span>
        <el-button plain :disabled="importing" @click.stop="fileInput?.click()">{{ file ? "更换文件" : "浏览文件" }}</el-button>
      </button>

      <div class="workflow-row">
        <div class="workflow-step" :class="{ complete: !!preview }"><span>1</span><div><strong>读取文件</strong><small>识别工作表与字段</small></div></div>
        <div class="workflow-connector"></div>
        <div class="workflow-step" :class="{ complete: !!barcodeColumn }"><span>2</span><div><strong>选择条码列</strong><small>条码必须唯一且非空</small></div></div>
        <div class="workflow-connector"></div>
        <div class="workflow-step" :class="{ complete: !!status.current }"><span>3</span><div><strong>更新快照</strong><small>供盘点数据查询</small></div></div>
      </div>

      <div v-if="preview" class="preview-panel">
        <div class="preview-summary">
          <div><span class="eyebrow">读取结果</span><strong>{{ preview.row_count.toLocaleString() }} 条</strong></div>
          <div><span class="eyebrow">工作表</span><strong>{{ preview.sheet_name }}</strong></div>
          <div class="preview-picker">
            <span class="eyebrow">资产条码列</span>
            <el-select v-model="barcodeColumn" :disabled="importing" placeholder="选择表头列">
              <el-option v-for="header in preview.headers" :key="header" :label="header" :value="header" />
            </el-select>
          </div>
        </div>
        <div class="header-list">
          <span class="eyebrow">识别字段</span>
          <el-tag v-for="header in preview.headers" :key="header" effect="plain" :type="header === barcodeColumn ? 'primary' : 'info'">{{ header }}</el-tag>
        </div>
      </div>

      <div class="import-actions">
        <span class="validation-note"><el-icon><CircleCheck v-if="preview && barcodeColumn" /><InfoFilled v-else /></el-icon>{{ preview && barcodeColumn ? "导入前将校验空条码与重复条码" : "选择文件并读取表头后继续" }}</span>
        <el-button :disabled="!file || importing" :loading="previewing" @click="previewFile"><el-icon><Search /></el-icon>读取表头</el-button>
        <el-button type="primary" :disabled="!canImport" :loading="importing" @click="importFile"><el-icon><UploadFilled /></el-icon>确认导入</el-button>
      </div>
    </section>

    <aside class="asset-current-panel">
      <div class="section-heading compact-heading">
        <div><h2>当前快照</h2><p>资产条码查询使用的数据</p></div>
      </div>
      <template v-if="status.current">
        <div class="snapshot-count"><strong>{{ status.current.row_count.toLocaleString() }}</strong><span>条资产记录</span></div>
        <dl class="snapshot-details">
          <div><dt>来源文件</dt><dd :title="status.current.file_name">{{ status.current.file_name }}</dd></div>
          <div><dt>条码字段</dt><dd>{{ status.current.barcode_column }}</dd></div>
          <div><dt>最后更新</dt><dd>{{ formatTime(status.current.imported_at) }}</dd></div>
          <div><dt>操作人员</dt><dd>{{ status.current.imported_by_name || "-" }}</dd></div>
        </dl>
      </template>
      <div v-else class="empty-snapshot"><el-icon><Files /></el-icon><strong>尚无可查询快照</strong><span>导入后，盘点表中的资产条码即可点击查询。</span></div>
    </aside>
  </div>

  <section v-if="status.current && permissions.manageAssets" class="display-field-settings">
    <div class="section-heading history-heading">
      <div>
        <h2>弹窗展示字段</h2>
        <p>在盘点数据中点击条码时，只显示勾选的字段；资产条码显示在弹窗标题中。</p>
      </div>
      <div class="field-settings-actions">
        <el-button :aria-expanded="fieldsExpanded" @click="fieldsExpanded = !fieldsExpanded"><el-icon><ArrowUp v-if="fieldsExpanded" /><ArrowDown v-else /></el-icon>{{ fieldsExpanded ? '收起字段' : '展开字段' }}</el-button>
        <el-button type="primary" :disabled="!displayFields.length || !displayFieldsChanged" :loading="savingFields" @click="saveDisplayFields">保存字段设置</el-button>
      </div>
    </div>
    <el-checkbox-group v-if="fieldsExpanded" v-model="displayFields" class="display-field-list">
      <el-checkbox v-for="field in displayableFields" :key="field" :value="field">{{ field }}</el-checkbox>
    </el-checkbox-group>
    <div class="display-field-footer">
      <span>{{ displayFields.length }} / {{ displayableFields.length }} 个字段已选择</span>
      <span v-if="!displayFields.length" class="field-warning">至少保留一个展示字段</span>
    </div>
  </section>

  <section class="import-history">
    <div class="section-heading history-heading">
      <div><h2>导入记录</h2><p>保留每次更新的来源与操作时间</p></div>
      <span class="history-count">{{ status.history.length }} 次</span>
    </div>
    <div v-if="status.history.length" class="table-wrap">
      <el-table :data="status.history" border stripe>
        <el-table-column prop="file_name" label="文件名" min-width="240" show-overflow-tooltip />
        <el-table-column prop="barcode_column" label="资产条码列" min-width="150" />
        <el-table-column prop="row_count" label="数据条数" width="120" />
        <el-table-column prop="imported_by_name" label="导入人" width="140" />
        <el-table-column label="导入时间" min-width="190"><template #default="{ row }">{{ formatTime(row.imported_at) }}</template></el-table-column>
      </el-table>
    </div>
    <div v-else class="history-empty">暂无导入记录</div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { ArrowDown, ArrowUp, CircleCheck, Document, Files, InfoFilled, Search, UploadFilled } from "@element-plus/icons-vue";
import PageHeader from "../components/PageHeader.vue";
import { apiGet, apiPost } from "../api";
import type { AssetInfoImportItem, AssetInfoPreview, AssetInfoStatus } from "../types";
import { formatTime } from "../utils";
import { permissions } from "../permissions";

const file = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const preview = ref<AssetInfoPreview | null>(null);
const previewedFile = ref<File | null>(null);
let previewSequence = 0;
const barcodeColumn = ref("");
const previewing = ref(false);
const importing = ref(false);
const savingFields = ref(false);
const fieldsExpanded = ref(false);
const displayFields = ref<string[]>([]);
const status = ref<{ current: AssetInfoStatus | null; history: AssetInfoImportItem[] }>({ current: null, history: [] });

const canImport = computed(() => !!file.value && previewedFile.value === file.value && !!preview.value && !!barcodeColumn.value && !previewing.value && !importing.value);
const displayableFields = computed(() => (status.value.current?.headers || []).filter((field) => field !== status.value.current?.barcode_column));
const displayFieldsChanged = computed(() => {
  const saved = status.value.current?.display_fields || [];
  return saved.length !== displayFields.value.length || saved.some((field, index) => field !== displayFields.value[index]);
});

function onFileChange(event: Event) {
  if (importing.value) return;
  previewSequence += 1;
  file.value = (event.target as HTMLInputElement).files?.[0] || null;
  preview.value = null;
  previewedFile.value = null;
  previewing.value = false;
  barcodeColumn.value = "";
}

function formData(selectedFile: File) {
  const data = new FormData();
  data.append("file", selectedFile);
  return data;
}

async function loadStatus() {
  status.value = await apiGet("/asset-info/status");
  displayFields.value = [...(status.value.current?.display_fields || [])];
}

async function saveDisplayFields() {
  if (!displayFields.value.length) return;
  savingFields.value = true;
  try {
    await apiPost("/asset-info/display-fields", { fields: displayFields.value });
    ElMessage.success("弹窗展示字段已保存");
    await loadStatus();
  } finally {
    savingFields.value = false;
  }
}

async function previewFile() {
  const selectedFile = file.value;
  if (!selectedFile || previewing.value || importing.value) return;
  const sequence = ++previewSequence;
  preview.value = null;
  previewedFile.value = null;
  barcodeColumn.value = "";
  previewing.value = true;
  try {
    const result = await apiPost<AssetInfoPreview>("/asset-info/preview", formData(selectedFile), { timeout: 60000 });
    if (sequence !== previewSequence || file.value !== selectedFile) return;
    previewedFile.value = selectedFile;
    preview.value = result;
    barcodeColumn.value = result.barcode_candidates[0] || "";
  } finally {
    if (sequence === previewSequence) previewing.value = false;
  }
}

async function importFile() {
  const selectedFile = file.value;
  if (!selectedFile || !canImport.value) return;
  importing.value = true;
  try {
    const data = formData(selectedFile);
    data.append("barcode_column", barcodeColumn.value);
    const result = await apiPost<{ message: string }>("/asset-info/import", data, { timeout: 60000 });
    ElMessage.success(result.message);
    preview.value = null;
    previewedFile.value = null;
    barcodeColumn.value = "";
    file.value = null;
    if (fileInput.value) fileInput.value.value = "";
    await loadStatus();
  } finally {
    importing.value = false;
  }
}

onMounted(loadStatus);
onBeforeUnmount(() => { previewSequence += 1; });
</script>

<style scoped>
.asset-import-layout { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(280px, 0.8fr); gap: 16px; align-items: stretch; }
.asset-import-main, .asset-current-panel, .import-history { min-width: 0; padding: 20px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, 0.96); box-shadow: var(--shadow); }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { margin: 0; color: #193b5b; font-size: 18px; font-weight: 760; }
.section-heading p { margin: 6px 0 0; color: var(--muted); font-size: 13px; }
.file-drop { width: 100%; display: flex; align-items: center; gap: 14px; padding: 16px; text-align: left; border: 1px dashed #a9c5dd; border-radius: 6px; background: #f7fbff; cursor: pointer; transition: border-color .15s, background .15s; }
.file-drop:hover, .file-drop.has-file { border-color: #2991df; background: #f0f8ff; }
.file-icon { width: 42px; height: 42px; display: grid; flex: 0 0 auto; place-items: center; border-radius: 6px; color: #1683d4; background: #e3f3ff; font-size: 20px; }
.file-copy { min-width: 0; flex: 1; }
.file-copy strong, .file-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-copy strong { color: #274863; font-size: 14px; }
.file-copy small { margin-top: 4px; color: #71869a; font-size: 12px; }
.workflow-row { display: flex; align-items: center; gap: 12px; margin: 20px 0; }
.workflow-step { min-width: 0; display: flex; align-items: center; gap: 8px; color: #7890a5; }
.workflow-step > span { width: 24px; height: 24px; display: grid; flex: 0 0 auto; place-items: center; border: 1px solid #cbd8e3; border-radius: 50%; font-size: 12px; }
.workflow-step.complete { color: #1879bd; }
.workflow-step.complete > span { border-color: #168bd4; color: #fff; background: #168bd4; }
.workflow-step strong, .workflow-step small { display: block; white-space: nowrap; }
.workflow-step strong { font-size: 12px; }
.workflow-step small { margin-top: 3px; color: #8093a5; font-size: 11px; }
.workflow-connector { min-width: 12px; height: 1px; flex: 1; background: #d9e4ed; }
.preview-panel { margin-bottom: 16px; padding: 14px; border: 1px solid #dbe8f2; border-radius: 6px; background: #f9fcff; }
.preview-summary { display: grid; grid-template-columns: minmax(90px, .6fr) minmax(110px, .8fr) minmax(190px, 1.2fr); gap: 14px; align-items: end; }
.preview-summary > div, .preview-picker { min-width: 0; }
.eyebrow { display: block; margin-bottom: 6px; color: #70869a; font-size: 11px; }
.preview-summary strong { color: #274863; font-size: 14px; }
.preview-picker :deep(.el-select) { width: 100%; }
.header-list { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
.header-list .eyebrow { margin: 0 4px 0 0; }
.import-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; padding-top: 14px; border-top: 1px solid #edf2f6; }
.validation-note { display: flex; align-items: center; gap: 6px; flex: 1; color: #788b9d; font-size: 12px; }
.validation-note :deep(.el-icon) { color: #1b91bc; }
.compact-heading { margin-bottom: 16px; }
.snapshot-count { display: flex; align-items: baseline; gap: 9px; padding-bottom: 16px; border-bottom: 1px solid #edf2f6; }
.snapshot-count strong { color: #1576be; font-size: 30px; font-weight: 780; line-height: 1; }
.snapshot-count span { color: #71869a; font-size: 13px; }
.snapshot-details { margin: 4px 0 0; }
.snapshot-details > div { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid #f0f3f6; font-size: 12px; }
.snapshot-details dt { flex: 0 0 auto; color: #7a8e9f; }
.snapshot-details dd { min-width: 0; margin: 0; overflow: hidden; color: #304d66; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.empty-snapshot { min-height: 188px; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 9px; color: #8193a3; text-align: center; }
.empty-snapshot :deep(.el-icon) { color: #94bdd8; font-size: 28px; }
.empty-snapshot strong { color: #526d84; font-size: 14px; }
.empty-snapshot span { max-width: 230px; font-size: 12px; line-height: 1.6; }
.import-history { margin-top: 16px; }
.display-field-settings { margin-top: 16px; padding: 20px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, 0.96); box-shadow: var(--shadow); }
.display-field-list { display: flex; flex-wrap: wrap; gap: 4px 22px; }
.display-field-list :deep(.el-checkbox) { margin-right: 0; }
.field-settings-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.field-settings-actions :deep(.el-button + .el-button) { margin-left: 0; }
.display-field-footer { display: flex; gap: 14px; margin-top: 14px; color: #71869a; font-size: 12px; }
.field-warning { color: #c45656; }
.history-heading { align-items: center; margin-bottom: 14px; }
.history-count { color: #71869a; font-size: 12px; }
.import-history .table-wrap { margin: 0; box-shadow: none; }
.history-empty { padding: 24px; border: 1px dashed #d9e3ec; border-radius: 6px; color: #8596a5; text-align: center; font-size: 13px; }
@media (max-width: 980px) {
  .asset-import-layout { grid-template-columns: 1fr; }
  .asset-current-panel { min-height: 0; }
}
@media (max-width: 640px) {
  .asset-import-main, .asset-current-panel, .import-history { padding: 14px; }
  .workflow-row { align-items: flex-start; flex-direction: column; gap: 8px; }
  .workflow-connector { width: 1px; height: 12px; min-width: 1px; margin-left: 11px; flex: 0 0 auto; }
  .workflow-step small { white-space: normal; }
  .preview-summary { grid-template-columns: 1fr 1fr; }
  .preview-picker { grid-column: 1 / -1; }
  .import-actions { align-items: stretch; flex-wrap: wrap; }
  .validation-note { flex-basis: 100%; }
  .import-actions :deep(.el-button) { flex: 1; }
}
</style>
