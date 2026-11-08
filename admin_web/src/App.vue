<template>
  <LoginView v-if="!token" @logged-in="handleLoggedIn" />
  <div v-else-if="!sessionReady" class="session-check">
    <p>{{ sessionError || "正在验证账号权限…" }}</p>
    <el-button v-if="sessionError" type="primary" @click="verifySession">重新验证</el-button>
    <el-button @click="logout">退出登录</el-button>
  </div>
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
        v-for="item in visibleMenus"
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
        <div class="sidebar-env">{{ remoteEntry ? "远程管理入口" : "本地试用环境" }}</div>
        <div class="sidebar-user">
          <div class="sidebar-user-meta">
            <span>当前账号</span>
            <strong>{{ user?.name || user?.wx_openid || "管理员" }} · {{ user ? adminRoleText(user) : '' }}</strong>
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
    <AgentChat v-if="permissions.runAi" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import type { Component } from "vue";
import { CircleCheck, Cpu, DataAnalysis, Files, List, PriceTag, User } from "@element-plus/icons-vue";
import { clearSession, getToken, refreshSessionUser } from "./api";
import { adminRoleText, permissions, sessionUser } from "./permissions";
import { API_BASE } from "./config";
import type { AdminUser } from "./types";
import LoginView from "./pages/LoginView.vue";
import TaskManagementPage from "./pages/TaskManagementPage.vue";
import ReportPage from "./pages/ReportPage.vue";
import AiPage from "./pages/AiPage.vue";
import GovernancePage from "./pages/GovernancePage.vue";
import UsersPage from "./pages/UsersPage.vue";
import QrAssetsPage from "./pages/QrAssetsPage.vue";
import AssetInfoPage from "./pages/AssetInfoPage.vue";
import AgentChat from "./components/AgentChat.vue";

type PageKey = "task-management" | "report" | "ai" | "governance" | "users" | "qr-assets" | "asset-info";

const token = ref(getToken());
const remoteEntry = API_BASE.startsWith("/");
const user = sessionUser;
const sessionReady = ref(false);
const sessionError = ref("");
const currentPage = ref<PageKey>("task-management");

const menus: Array<{ key: PageKey; icon: Component; label: string }> = [
  { key: "task-management", icon: List, label: "任务管理" },
  { key: "report", icon: DataAnalysis, label: "资产盘点数据" },
  { key: "asset-info", icon: Files, label: "资产信息表" },
  { key: "ai", icon: Cpu, label: "三方比对" },
  { key: "governance", icon: CircleCheck, label: "整改闭环台账" },
  { key: "users", icon: User, label: "账号管理" },
  { key: "qr-assets", icon: PriceTag, label: "二维码资产" },
];
const visibleMenus = computed(() => menus.filter(item => item.key !== "users" || permissions.value.manageAccounts));
watch(visibleMenus, (items) => {
  if (!items.some(item => item.key === currentPage.value)) currentPage.value = "task-management";
});
watch(sessionUser, (value) => {
  if (!value && sessionReady.value) logout();
});

const activeComponent = computed(() => {
  const map = {
    "task-management": TaskManagementPage,
    report: ReportPage,
    ai: AiPage,
    governance: GovernancePage,
    users: UsersPage,
    "qr-assets": QrAssetsPage,
    "asset-info": AssetInfoPage,
  };
  return map[currentPage.value];
});

async function handleLoggedIn(payload: { token: string; user: AdminUser }) {
  token.value = payload.token;
  await verifySession();
}

async function verifySession() {
  sessionReady.value = false;
  sessionError.value = "";
  try {
    const verified = await refreshSessionUser();
    if (verified.role !== "admin" || verified.admin_scope && !["full", "collaborator"].includes(verified.admin_scope)) {
      logout();
      return;
    }
    sessionReady.value = true;
  } catch {
    if (!getToken()) logout();
    else sessionError.value = "无法验证当前账号权限，请重试。";
  }
}

function logout() {
  clearSession();
  token.value = "";
  user.value = null;
  sessionReady.value = false;
  currentPage.value = "task-management";
}
onMounted(() => { if (token.value) void verifySession(); });
</script>

<style scoped>
.session-check { padding: 48px; text-align: center; color: var(--muted); }
</style>
