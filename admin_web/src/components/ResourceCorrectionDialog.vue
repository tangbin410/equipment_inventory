<template>
  <el-dialog
    :model-value="modelValue"
    title="修改综资数据"
    width="760px"
    style="max-width: calc(100vw - 32px)"
    :close-on-click-modal="!saving"
    :close-on-press-escape="!saving"
    :show-close="!saving"
    @close="closeDialog"
  >
    <el-alert title="保存后，新的综资数据用于本任务的三方比对和导出。" type="info" :closable="false" show-icon />
    <div v-loading="loading" class="resource-body">
      <template v-if="source">
        <div class="resource-context">
          <div><span>机房</span><strong>{{ source.room_name }}</strong></div>
          <div><span>网元</span><strong>{{ source.device_name }}</strong></div>
        </div>
        <el-alert v-if="!source.editable" :title="source.reason || '当前设备暂无可直接修改的综资记录。'" type="warning" :closable="false" show-icon />
        <div class="resource-edit-grid">
          <el-form label-position="top" :disabled="saving || !source.editable">
            <el-form-item label="综资设备型号">
              <el-input v-model="form.expected_model" maxlength="200" />
            </el-form-item>
            <el-form-item label="综资资产条码（多个用逗号分隔）">
              <el-input v-model="form.expected_barcode" type="textarea" :rows="2" maxlength="2000" placeholder="填写核实后的资产条码，没有可留空" />
            </el-form-item>
            <el-form-item label="综资设备机柜位">
              <el-input v-model="form.expected_rack_no" maxlength="200" />
            </el-form-item>
            <el-form-item label="修改说明（选填）">
              <el-input v-model="form.note" type="textarea" :rows="2" maxlength="500" />
            </el-form-item>
          </el-form>
          <section class="field-reference">
            <h3>现场盘点参考</h3>
            <dl>
              <dt>设备型号</dt><dd>{{ record?.actual_model || '未填写' }}</dd>
              <dt>资产条码</dt><dd>{{ record?.asset_no_barcode ? '无条码' : record?.asset_barcodes || '未填写' }}</dd>
              <dt>设备机柜位</dt><dd>{{ record?.rack_no || '未填写' }}</dd>
            </dl>
          </section>
        </div>
      </template>
      <div v-else-if="loadError" class="resource-load-error">
        <el-alert title="综资数据读取失败，请重新读取后再修改。" type="error" :closable="false" show-icon />
        <el-button @click="loadResource">重新读取</el-button>
      </div>
    </div>
    <template #footer>
      <el-button :disabled="saving" @click="closeDialog">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="loading || !source?.editable || !source?.fingerprint" @click="saveResource">保存综资数据</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { apiGet, apiPost } from "../api";
import type { TaskRecord } from "../types";

interface ResourceRecord {
  record_id: number;
  task_id: number;
  room_name: string;
  device_name: string;
  expected_model: string;
  expected_barcode: string;
  expected_rack_no: string;
  fingerprint: string;
  editable: boolean;
  reason: string;
}

const props = defineProps<{ modelValue: boolean; record: TaskRecord | null }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean]; saved: [] }>();
const source = ref<ResourceRecord | null>(null);
const loading = ref(false);
const saving = ref(false);
const loadError = ref(false);
const form = reactive({ expected_model: "", expected_barcode: "", expected_rack_no: "", note: "" });
let requestSequence = 0;

watch(() => [props.modelValue, props.record?.id] as const, ([visible]) => {
  requestSequence += 1;
  source.value = null;
  loading.value = false;
  loadError.value = false;
  if (visible && props.record) void loadResource();
}, { immediate: true });

onBeforeUnmount(() => { requestSequence += 1; });

async function loadResource() {
  const recordId = props.record?.id;
  if (!props.modelValue || !recordId) return;
  const sequence = ++requestSequence;
  loading.value = true;
  loadError.value = false;
  source.value = null;
  try {
    const result = await apiGet<ResourceRecord>(`/admin/records/${recordId}/resource`);
    if (sequence !== requestSequence || !props.modelValue || props.record?.id !== recordId) return;
    source.value = result;
    Object.assign(form, {
      expected_model: result.expected_model || "",
      expected_barcode: result.expected_barcode || "",
      expected_rack_no: result.expected_rack_no || "",
      note: "",
    });
  } catch {
    if (sequence === requestSequence) loadError.value = true;
  } finally {
    if (sequence === requestSequence) loading.value = false;
  }
}

function closeDialog() {
  if (saving.value) return;
  requestSequence += 1;
  emit("update:modelValue", false);
}

async function saveResource() {
  const current = source.value;
  if (!current?.editable || !current.fingerprint || loading.value || saving.value) return;
  const sequence = requestSequence;
  saving.value = true;
  try {
    const result = await apiPost<{ message: string }>(`/admin/records/${current.record_id}/resource`, {
      expected_model: form.expected_model.trim(),
      expected_barcode: form.expected_barcode.trim(),
      expected_rack_no: form.expected_rack_no.trim(),
      note: form.note.trim(),
      fingerprint: current.fingerprint,
    });
    ElMessage.success(result.message || "综资数据已更新");
    emit("saved");
    if (sequence === requestSequence && props.record?.id === current.record_id) emit("update:modelValue", false);
  } catch {
    // API 已显示失败原因；保留本次填写内容，供核实或重试。
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.resource-body { min-height: 180px; margin-top: 16px; }
.resource-context { display: grid; gap: 8px; margin-bottom: 16px; }
.resource-context > div { display: flex; align-items: baseline; gap: 12px; }
.resource-context span { flex: 0 0 32px; color: var(--muted); }
.resource-context strong { color: var(--text); font-weight: 600; overflow-wrap: anywhere; }
.resource-edit-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); align-items: start; gap: 18px; margin-top: 16px; }
.field-reference { padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: #f0f8ff; }
.field-reference h3 { margin: 0 0 12px; color: #173b5e; font-size: 14px; }
.field-reference dl { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 10px; margin: 0; font-size: 13px; line-height: 1.7; }
.field-reference dt { color: var(--muted); }
.field-reference dd { margin: 0; overflow-wrap: anywhere; }
.resource-load-error { display: grid; justify-items: start; gap: 14px; }
@media (max-width: 700px) { .resource-edit-grid { grid-template-columns: 1fr; } }
</style>
