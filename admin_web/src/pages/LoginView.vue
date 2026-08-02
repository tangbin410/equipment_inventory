<template>
  <div class="login-page">
    <div class="login-backdrop" aria-hidden="true">
      <div class="topology-frame">
        <span class="topology-node node-a"></span>
        <span class="topology-node node-b"></span>
        <span class="topology-node node-c"></span>
        <span class="topology-node node-d"></span>
        <span class="topology-node node-e"></span>
        <span class="topology-node node-f"></span>
      </div>
      <div class="backdrop-panel panel-left">
        <span></span><span></span><span></span><span></span>
      </div>
      <div class="backdrop-panel panel-right">
        <span></span><span></span><span></span><span></span>
      </div>
      <div class="server-stack">
        <span></span><span></span><span></span><span></span><span></span><span></span>
      </div>
      <div class="signal-board">
        <span></span><span></span><span></span><span></span>
        <span></span><span></span><span></span><span></span>
      </div>
      <span class="circuit-path path-one"></span>
      <span class="circuit-path path-two"></span>
      <span class="circuit-path path-three"></span>
      <div class="data-rail">
        <span></span><span></span><span></span><span></span><span></span>
      </div>
      <span class="link-line line-a"></span>
      <span class="link-line line-b"></span>
      <span class="link-line line-c"></span>
    </div>
    <section class="login-brand">
      <div class="login-badge">
        <span class="signal-dot"></span>
        传输网管维护 · AI 资产盘点
      </div>
      <h1>机房资产<br />盘点管理端</h1>
      <p>面向机房现场盘点、账号分配、任务跟踪和 AI 分析。</p>
      <div class="login-status-row">
        <span>LOCAL AGENT</span>
        <strong>READY</strong>
      </div>
      <div class="login-points">
        <span><el-icon><User /></el-icon>账号启停</span>
        <span><el-icon><Location /></el-icon>片区授权</span>
        <span><el-icon><Tickets /></el-icon>任务看板</span>
        <span><el-icon><Cpu /></el-icon>AI 分析</span>
      </div>
    </section>
    <section class="login-panel">
      <div class="login-title">
        <span class="lock"><el-icon><Lock /></el-icon></span>
        <div>
          <h2>管理员登录</h2>
          <p>PC 管理后台安全入口</p>
        </div>
      </div>
      <div class="login-mini-state">
        <span>身份校验</span>
        <span>数据脱敏</span>
        <span>模型分析</span>
      </div>
      <el-form :model="form" label-position="top" @submit.prevent>
        <el-form-item label="管理员账号">
          <el-input v-model="form.username" size="large" placeholder="请输入管理员账号" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" size="large" type="password" show-password placeholder="请输入密码" />
        </el-form-item>
        <el-button type="primary" size="large" class="full-btn" :loading="loading" @click="login">登录</el-button>
      </el-form>
    </section>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { Cpu, Location, Lock, Tickets, User } from "@element-plus/icons-vue";
import { apiPost, setSession } from "../api";
import type { AdminUser } from "../types";

const emit = defineEmits<{ "logged-in": [{ token: string; user: AdminUser }] }>();
const loading = ref(false);
const form = reactive({ username: "admin_001", password: "" });

async function login() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning("请输入管理员账号和密码");
    return;
  }
  loading.value = true;
  try {
    const result = await apiPost<{ access_token: string; user: AdminUser }>("/admin/login", {
      username: form.username.trim(),
      password: form.password,
    });
    setSession(result.access_token, result.user);
    emit("logged-in", { token: result.access_token, user: result.user });
  } finally {
    loading.value = false;
  }
}
</script>
