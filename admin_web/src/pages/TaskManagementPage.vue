<template>
  <PageHeader :icon="List" title="任务管理" :desc="permissions.manageTasks ? '导入盘点清单，查看任务进度并管理任务' : '查看全部机房任务与盘点进度'" />

  <el-tabs v-model="activeTab" @tab-change="refreshActiveTab">
    <el-tab-pane v-if="permissions.manageTasks" label="清单导入" name="upload" lazy>
      <UploadPage ref="uploadPage" />
    </el-tab-pane>
    <el-tab-pane label="任务看板" name="tasks" lazy>
      <TasksPage ref="tasksPage" />
    </el-tab-pane>
  </el-tabs>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { List } from "@element-plus/icons-vue";
import PageHeader from "../components/PageHeader.vue";
import UploadPage from "./UploadPage.vue";
import TasksPage from "./TasksPage.vue";
import { permissions } from "../permissions";

const activeTab = ref(permissions.value.manageTasks ? "upload" : "tasks");
const uploadPage = ref<InstanceType<typeof UploadPage> | null>(null);
const tasksPage = ref<InstanceType<typeof TasksPage> | null>(null);

function refreshActiveTab(name: string | number) {
  // 首次进入由子页面加载；再次切换时刷新任务，保留已选文件和查询内容。
  if (name === "upload") uploadPage.value?.refresh();
  if (name === "tasks") tasksPage.value?.refresh();
}
</script>
