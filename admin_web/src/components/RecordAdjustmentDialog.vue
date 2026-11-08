<template>
  <el-dialog :model-value="modelValue" title="人工转整改" width="620px" @close="close">
    <p>设备：{{ record?.device_name }}。填写确认依据和整改要求，保存后进入整改台账，由管理员下发并验收。</p>
    <el-form label-position="top">
      <el-form-item label="问题类型"><el-input v-model="form.problem_type" maxlength="100" /></el-form-item>
      <el-form-item label="人工确认依据"><el-input v-model="form.basis" type="textarea" :rows="3" maxlength="1000" /></el-form-item>
      <el-form-item label="整改要求"><el-input v-model="form.suggestion" type="textarea" :rows="3" maxlength="1000" /></el-form-item>
      <el-form-item label="风险等级">
        <el-select v-model="form.risk_level"><el-option v-for="risk in ['高', '中', '低']" :key="risk" :label="risk" :value="risk" /></el-select>
      </el-form-item>
      <el-form-item label="优先级">
        <el-select v-model="form.priority"><el-option v-for="priority in ['P1', 'P2', 'P3']" :key="priority" :label="priority" :value="priority" /></el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">加入整改台账</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { apiPost } from "../api";
import type { TaskRecord } from "../types";

const props = defineProps<{ modelValue: boolean; record: TaskRecord | null }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
const saving = ref(false);
const form = reactive({ problem_type: "", basis: "", suggestion: "", risk_level: "高", priority: "P1" });
watch(() => [props.modelValue, props.record?.id] as const, ([visible]) => {
  if (visible) Object.assign(form, { problem_type: "", basis: "", suggestion: "", risk_level: "高", priority: "P1" });
});
function close() { emit("update:modelValue", false); }
async function save() {
  if (!props.record || saving.value) return;
  if (![form.problem_type, form.basis, form.suggestion].every((value) => value.trim())) {
    ElMessage.warning("请填写问题类型、确认依据和整改要求");
    return;
  }
  saving.value = true;
  try {
    const result = await apiPost<{ message: string }>(`/admin/records/${props.record.id}/adjustments`, { ...form });
    ElMessage.success(result.message);
    close();
  } finally {
    saving.value = false;
  }
}
</script>
