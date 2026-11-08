<template>
  <el-dialog :model-value="modelValue" :title="rack ? '更正机架登记' : '管理员补录机架'" width="620px" @close="closeDialog">
    <el-alert title="仅登记 ODF / IODF；传输综合柜由设备盘点反馈汇总。保存后按最新有效数据展示、导出和核对。" type="info" :closable="false" show-icon />
    <el-form label-position="top" class="rack-form">
      <el-form-item label="所属机房任务">
        <el-select v-model="form.task_id" :disabled="Boolean(rack)" placeholder="请选择本次盘点任务" filterable>
          <el-option v-for="task in tasks" :key="task.id" :value="task.id" :label="`${task.room_name} · ${task.import_batch_id || task.task_name} · #${task.id}`" />
        </el-select>
      </el-form-item>
      <div class="form-grid">
        <el-form-item label="机架类型">
          <el-select v-model="form.cabinet_type">
            <el-option label="ODF" value="odf" />
            <el-option label="IODF" value="iodf" />
          </el-select>
        </el-form-item>
        <el-form-item label="资源名称 / 机架编号">
          <el-input v-model="form.rack_no" maxlength="200" placeholder="例如 AA09B" />
        </el-form-item>
      </div>
      <el-form-item label="机架资产条码">
        <el-input v-model="form.cabinet_barcode" :disabled="form.no_barcode" maxlength="200" placeholder="填写此机架的资产条码" />
        <el-checkbox v-model="form.no_barcode" @change="clearBarcode">现场确实无机架条码</el-checkbox>
      </el-form-item>
      <el-form-item label="现场备注">
        <el-input v-model="form.notes" type="textarea" :rows="2" maxlength="1000" show-word-limit />
      </el-form-item>
      <el-form-item :label="rack ? '更正原因' : '补录原因'">
        <el-input v-model="form.correction_reason" type="textarea" :rows="2" maxlength="500" show-word-limit placeholder="请填写核实依据或原因" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="closeDialog">取消</el-button>
      <el-button type="primary" :loading="saving" @click="saveRack">{{ rack ? '保存更正' : '补录机架' }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import client, { apiPost } from "../api";
import type { CabinetRecord, Task } from "../types";

const props = defineProps<{ modelValue: boolean; rack: CabinetRecord | null; tasks: Task[] }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean]; saved: [] }>();
const saving = ref(false);
const form = reactive({
  task_id: null as number | null,
  cabinet_type: "odf",
  rack_no: "",
  cabinet_barcode: "",
  no_barcode: false,
  notes: "",
  correction_reason: "",
});

watch(() => [props.modelValue, props.rack?.id] as const, ([visible]) => {
  if (!visible) return;
  const rack = props.rack;
  form.task_id = rack?.task_id || (props.tasks.length === 1 ? props.tasks[0]!.id : null);
  form.cabinet_type = rack?.cabinet_type || "odf";
  form.rack_no = rack?.rack_no || rack?.cabinet_name || "";
  form.cabinet_barcode = rack?.cabinet_barcode || "";
  form.no_barcode = Boolean(rack?.no_barcode);
  form.notes = rack?.notes || "";
  form.correction_reason = "";
});

function clearBarcode(value: boolean | string | number) {
  if (Boolean(value)) form.cabinet_barcode = "";
}

function closeDialog() {
  emit("update:modelValue", false);
}

async function saveRack() {
  if (!form.task_id) {
    ElMessage.warning("请选择所属机房任务");
    return;
  }
  if (!form.rack_no.trim()) {
    ElMessage.warning("请填写资源名称 / 机架编号");
    return;
  }
  if (!form.no_barcode && !form.cabinet_barcode.trim()) {
    ElMessage.warning("请填写机架资产条码，或勾选现场确实无条码");
    return;
  }
  if (!form.correction_reason.trim()) {
    ElMessage.warning("请填写补录或更正原因");
    return;
  }
  const body = {
    cabinet_type: form.cabinet_type,
    cabinet_barcode: form.cabinet_barcode.trim(),
    no_barcode: form.no_barcode,
    rack_no: form.rack_no.trim(),
    notes: form.notes.trim(),
    correction_reason: form.correction_reason.trim(),
  };
  saving.value = true;
  try {
    const endpoint = `/admin/tasks/${form.task_id}/racks`;
    const result = props.rack
      ? (await client.put<{ message: string; warnings?: string[] }>(`${endpoint}/${props.rack.id}`, body)).data
      : await apiPost<{ message: string; warnings?: string[] }>(endpoint, body);
    ElMessage.success(result.message);
    (result.warnings || []).forEach((warning) => ElMessage.warning(warning));
    emit("saved");
    closeDialog();
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.rack-form { margin-top: 16px; }
.rack-form :deep(.el-select) { width: 100%; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 620px) { .form-grid { grid-template-columns: 1fr; } }
</style>
