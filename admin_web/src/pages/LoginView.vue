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
          <el-input v-model="form.username" size="large" placeholder="请输入登录编码，如 admin_001" />
          <p class="login-account-hint">请填写分配的登录编码，不是邮箱或姓名。</p>
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" size="large" type="password" show-password placeholder="请输入密码" />
        </el-form-item>
        <el-button type="primary" size="large" class="full-btn" :loading="loading" @click="login">登录</el-button>
        <div class="login-help-action">
          <el-button type="primary" link @click="passwordHelpVisible = true">忘记密码？</el-button>
        </div>
      </el-form>
    </section>
    <el-dialog v-model="passwordHelpVisible" title="忘记后台密码" width="560px" style="max-width: calc(100vw - 32px)">
      <div class="password-help-content">
        <section>
          <h3>协作管理员</h3>
          <p>请联系完整管理员，在“账号管理”中找到你的账号，点击“重置密码”。重置后使用新密码重新登录。</p>
        </section>
        <section>
          <h3>完整管理员也无法登录</h3>
          <p>在运行本系统的 Mac 上打开本机密码恢复工具：</p>
          <code>scripts/mac/reset_admin_password_mac.command</code>
          <p>由本人在本机隐藏输入两次新密码，无需把密码发给他人。修改 .env 的初始化密码不能重置已有账号。</p>
        </section>
        <el-alert title="邮箱验证码和后台密码是两层验证。获准邮箱不能直接重置任意后台账号；本入口不会发送重置邮件。" type="info" :closable="false" show-icon />
      </div>
      <template #footer><el-button type="primary" @click="passwordHelpVisible = false">知道了</el-button></template>
    </el-dialog>
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
const passwordHelpVisible = ref(false);
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

<style scoped>
.login-account-hint {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}

.login-help-action {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.password-help-content {
  display: grid;
  gap: 20px;
  line-height: 1.7;
}

.password-help-content h3 {
  margin: 0 0 6px;
  color: #24496c;
  font-size: 16px;
}

.password-help-content p {
  margin: 0 0 8px;
}

.password-help-content p:last-child {
  margin-bottom: 0;
}

.password-help-content code {
  display: block;
  margin: 8px 0;
  padding: 10px 12px;
  border: 1px solid #d8e8f6;
  border-radius: 8px;
  background: #f2f8fd;
  color: #24496c;
  overflow-wrap: anywhere;
}
</style>
