<template>
  <LoginView v-if="!token" @logged-in="handleLoggedIn" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">传</div>
        <div>
          <div class="brand-title">机房资产盘点系统</div>
          <div class="brand-sub">传输网管维护 · PC 管理端</div>
        </div>
      </div>
      <div class="nav-group-label">业务模块</div>
      <button
        v-for="item in menus"
        :key="item.key"
        class="nav-item"
        :class="{ active: currentPage === item.key }"
        @click="currentPage = item.key"
      >
        <span class="nav-dot"></span>
        <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
        <span>{{ item.label }}</span>
      </button>
      <div class="sidebar-foot">
        <div class="sidebar-env">本地试用环境</div>
        <div class="sidebar-user">
          <div class="sidebar-user-meta">
            <span>当前账号</span>
            <strong>{{ user?.name || user?.wx_openid || "管理员" }}</strong>
          </div>
          <button class="sidebar-logout" @click="logout">退出登录</button>
        </div>
      </div>
    </aside>

    <section class="workspace">
      <main class="main">
        <component :is="activeComponent" />
      </main>
    </section>
    <AgentChat />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import type { Component } from "vue";
import { Cpu, DataAnalysis, List, UploadFilled, User } from "@element-plus/icons-vue";
import { clearSession, getSessionUser, getToken } from "./api";
import type { AdminUser } from "./types";
import LoginView from "./pages/LoginView.vue";
import UploadPage from "./pages/UploadPage.vue";
import TasksPage from "./pages/TasksPage.vue";
import ReportPage from "./pages/ReportPage.vue";
import AiPage from "./pages/AiPage.vue";
import UsersPage from "./pages/UsersPage.vue";
import AgentChat from "./components/AgentChat.vue";

type PageKey = "upload" | "tasks" | "report" | "ai" | "users";

const token = ref(getToken());
const user = ref<AdminUser | null>(getSessionUser<AdminUser>());
const currentPage = ref<PageKey>("upload");

const menus: Array<{ key: PageKey; icon: Component; label: string }> = [
  { key: "upload", icon: UploadFilled, label: "上传任务清单" },
  { key: "tasks", icon: List, label: "任务看板总览" },
  { key: "report", icon: DataAnalysis, label: "资产盘点数据" },
  { key: "ai", icon: Cpu, label: "AI模型分析" },
  { key: "users", icon: User, label: "小程序账号管理" },
];

const activeComponent = computed(() => {
  const map = {
    upload: UploadPage,
    tasks: TasksPage,
    report: ReportPage,
    ai: AiPage,
    users: UsersPage,
  };
  return map[currentPage.value];
});

function handleLoggedIn(payload: { token: string; user: AdminUser }) {
  token.value = payload.token;
  user.value = payload.user;
}

function logout() {
  clearSession();
  token.value = "";
  user.value = null;
}
</script>
