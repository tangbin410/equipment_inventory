<template>
  <el-dialog :model-value="modelValue" title="管理员修正现场反馈" width="820px" @close="closeDialog">
    <template v-if="record">
      <el-alert
        title="保存后以本次修正值更新当前盘点数据和导出结果。"
        type="info"
        :closable="false"
        show-icon
      />

      <section class="compare-grid">
        <div class="compare-block source-block">
          <h3>综资导入值</h3>
          <dl>
            <dt>设备</dt><dd>{{ record.device_name || "-" }}</dd>
            <dt>型号</dt><dd>{{ record.expected_model || "-" }}</dd>
            <dt>资产条码</dt><dd>{{ record.expected_barcode || "-" }}</dd>
            <dt>设备机柜位</dt><dd>{{ record.expected_rack_no || "-" }}</dd>
          </dl>
        </div>
        <div class="compare-block original-block">
          <h3>小程序原始反馈</h3>
          <dl>
            <dt>现场状态</dt><dd>{{ recordTypeText(record.original_record_type || record.record_type) }}</dd>
            <dt>型号</dt><dd>{{ record.original_actual_model || "-" }}</dd>
            <dt>资产条码</dt><dd>{{ originalAssetText }}</dd>
            <dt>设备机柜位</dt><dd>{{ record.original_rack_no || "-" }}</dd>
          </dl>
        </div>
      </section>

      <el-form label-position="top" class="correction-form">
        <el-form-item v-if="isSurplus" label="现场新增设备名称">
          <el-input v-model="form.device_name" maxlength="200" placeholder="请输入核实后的设备名称" />
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="修正后的现场状态">
            <el-select v-model="form.record_type" :disabled="record.record_type === 'surplus'">
              <el-option label="正常盘到" value="normal" />
              <el-option label="现场未找到" value="missing" />
              <el-option v-if="record.record_type === 'surplus'" label="现场新增" value="surplus" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.record_type === 'missing'" label="现场未找到原因">
            <el-input v-model="form.missing_reason" maxlength="500" />
          </el-form-item>
          <el-form-item v-else label="现场设备型号">
            <el-input v-model="form.actual_model" maxlength="200" />
          </el-form-item>
        </div>

        <template v-if="form.record_type !== 'missing'">
          <div class="form-grid">
            <el-form-item label="现场资产条码（多个用逗号分隔）">
              <el-input v-model="form.asset_barcodes" :disabled="form.asset_no_barcode" />
            </el-form-item>
            <el-form-item label="资产条码状态">
              <el-checkbox v-model="form.asset_no_barcode" @change="clearAssetCodes">现场确实无资产条码</el-checkbox>
            </el-form-item>
          </div>
          <div class="form-grid">
            <el-form-item label="现场机柜条码（多个用逗号分隔）">
              <el-input v-model="form.cabinet_barcodes" :disabled="form.cabinet_no_barcode" />
            </el-form-item>
            <el-form-item label="机柜条码状态">
              <el-checkbox v-model="form.cabinet_no_barcode" @change="clearCabinetCodes">现场确实无机柜条码</el-checkbox>
            </el-form-item>
          </div>
          <el-form-item label="现场设备机柜位">
            <el-input v-model="form.rack_no" maxlength="200" />
          </el-form-item>
        </template>

        <el-form-item label="现场备注">
          <el-input v-model="form.notes" type="textarea" :rows="2" maxlength="1000" show-word-limit />
        </el-form-item>

        <div class="form-grid">
          <el-form-item label="修正原因">
            <el-select v-model="form.correction_reason" placeholder="请选择">
              <el-option v-for="reason in correctionReasons" :key="reason" :label="reason" :value="reason" />
            </el-select>
          </el-form-item>
          <el-form-item label="修正说明">
            <el-input v-model="form.correction_note" :placeholder="form.correction_reason === '其他' ? '必填' : '可选'" maxlength="500" />
          </el-form-item>
        </div>
      </el-form>

      <el-collapse v-if="history.length" class="history-panel">
        <el-collapse-item :title="`历史修正 ${history.length} 次`">
          <div v-for="item in history" :key="item.id" class="history-item">
            <strong>{{ item.correction_reason }}</strong>
            <span>{{ item.corrected_by_name || "管理员" }} · {{ formatTime(item.corrected_at) }}</span>
            <p>{{ item.correction_note || "未填写说明" }}</p>
          </div>
        </el-collapse-item>
      </el-collapse>
    </template>

    <template #footer>
      <div class="dialog-actions">
        <el-button v-if="isSurplus" type="danger" plain :loading="voiding" :disabled="saving" @click="voidSurplus">作废误录盘盈</el-button>
        <span class="action-spacer" />
        <el-button :disabled="saving || voiding" @click="closeDialog">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="voiding" @click="saveCorrection">保存修正并重新核对</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { apiDelete, apiGet, apiPost } from "../api";
import type { RecordCorrection, TaskRecord } from "../types";
import { formatTime } from "../utils";

const props = defineProps<{ modelValue: boolean; record: TaskRecord | null }>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  saved: [payload: { record: TaskRecord; relatedAdjustmentIds: number[] }];
}>();

const correctionReasons = ["测试数据清理", "重复扫码", "条码录入错误", "型号录入错误", "盘盈名称录入错误", "无条码状态误选", "根据现场凭证修正", "其他"];
const history = ref<RecordCorrection[]>([]);
const saving = ref(false);
const voiding = ref(false);
const isSurplus = computed(() => props.record?.record_type === "surplus");
const form = reactive({
  device_name: "",
  actual_model: "",
  asset_barcodes: "",
  asset_no_barcode: false,
  cabinet_barcodes: "",
  cabinet_no_barcode: false,
  rack_no: "",
  notes: "",
  record_type: "normal",
  missing_reason: "",
  correction_reason: "",
  correction_note: "",
});

const originalAssetText = computed(() => {
  if (!props.record) return "-";
  return props.record.original_asset_no_barcode ? "无条码" : props.record.original_asset_barcodes || "-";
});

function recordTypeText(value?: string) {
  return { normal: "正常盘到", missing: "现场未找到", surplus: "现场新增" }[value || "normal"] || value || "-";
}

function resetForm(record: TaskRecord) {
  form.device_name = record.device_name || "";
  form.actual_model = record.actual_model || "";
  form.asset_barcodes = record.asset_barcodes || "";
  form.asset_no_barcode = Boolean(record.asset_no_barcode);
  form.cabinet_barcodes = record.cabinet_barcodes || "";
  form.cabinet_no_barcode = Boolean(record.cabinet_no_barcode);
  form.rack_no = record.rack_no || "";
  form.notes = record.notes || "";
  form.record_type = record.record_type || "normal";
  form.missing_reason = record.missing_reason || "";
  form.correction_reason = "";
  form.correction_note = "";
}

async function loadHistory(record: TaskRecord) {
  const result = await apiGet<{ corrections: RecordCorrection[] }>(`/admin/records/${record.id}/corrections`);
  history.value = result.corrections || [];
}

watch(
  () => [props.modelValue, props.record?.id] as const,
  async ([visible]) => {
    if (!visible || !props.record) return;
    resetForm(props.record);
    await loadHistory(props.record);
  },
);

function clearAssetCodes(value: boolean | string | number) {
  if (Boolean(value)) form.asset_barcodes = "";
}

function clearCabinetCodes(value: boolean | string | number) {
  if (Boolean(value)) form.cabinet_barcodes = "";
}

function closeDialog() {
  emit("update:modelValue", false);
}

async function saveCorrection() {
  if (!props.record) return;
  if (isSurplus.value && !form.device_name.trim()) {
    ElMessage.warning("请填写现场新增设备名称");
    return;
  }
  if (!form.correction_reason) {
    ElMessage.warning("请选择修正原因");
    return;
  }
  if (form.correction_reason === "其他" && !form.correction_note.trim()) {
    ElMessage.warning("选择其他原因时必须填写修正说明");
    return;
  }
  saving.value = true;
  try {
    const result = await apiPost<{
      message: string;
      record: TaskRecord;
      related_adjustment_ids: number[];
      warnings?: string[];
    }>(`/admin/records/${props.record.id}/corrections`, {
      ...form,
      device_name: isSurplus.value ? form.device_name.trim() : undefined,
    });
    ElMessage.success(result.message);
    (result.warnings || []).forEach((warning) => ElMessage.warning(warning));
    emit("saved", { record: result.record, relatedAdjustmentIds: result.related_adjustment_ids || [] });
    closeDialog();
  } finally {
    saving.value = false;
  }
}

async function voidSurplus() {
  if (!props.record || !isSurplus.value) return;
  let reason: string;
  try {
    const result = await ElMessageBox.prompt(
      `作废“${props.record.device_name}”后，该误录盘盈将不再参与盘点展示、导出和比对，历史记录保留。请填写作废原因。`,
      "作废误录盘盈",
      { confirmButtonText: "确认作废", cancelButtonText: "取消", inputType: "textarea", inputValidator: (value) => Boolean(value?.trim()) || "请填写作废原因" },
    );
    reason = result.value.trim();
  } catch {
    return;
  }
  voiding.value = true;
  try {
    const result = await apiDelete<{ message: string }>(`/admin/records/${props.record.id}`, { data: { reason } });
    ElMessage.success(result.message);
    emit("saved", { record: props.record, relatedAdjustmentIds: [] });
    closeDialog();
  } finally {
    voiding.value = false;
  }
}
</script>

<style scoped>
.compare-grid,
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.compare-grid { margin: 16px 0; }

.compare-block {
  padding: 14px;
  border: 1px solid #dbe7f2;
  border-radius: 8px;
  background: #f8fbfe;
}

.compare-block h3 { margin: 0 0 10px; color: #173b5e; font-size: 15px; }
.compare-block dl { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 7px 10px; margin: 0; }
.compare-block dt { color: #718397; }
.compare-block dd { margin: 0; color: #21384f; overflow-wrap: anywhere; }
.correction-form :deep(.el-select) { width: 100%; }
.history-panel { margin-top: 6px; }
.history-item { padding: 8px 2px; border-bottom: 1px solid #edf2f7; }
.history-item strong { margin-right: 10px; color: #254765; }
.history-item span { color: #7b8b9d; font-size: 12px; }
.history-item p { margin: 5px 0 0; color: #526579; }
.dialog-actions { display: flex; align-items: center; gap: 8px; }
.dialog-actions :deep(.el-button + .el-button) { margin-left: 0; }
.action-spacer { flex: 1; }

@media (max-width: 760px) {
  .compare-grid,
  .form-grid { grid-template-columns: 1fr; }
}
</style>
