<template>
  <PageHeader :icon="User" title="账号管理" desc="维护现场人员、完整管理员与协作管理员，管理独立登录密码" />

  <section class="account-overview">
    <div class="account-main-card">
      <div class="account-main-top">
        <span>账号总览</span>
        <el-tag effect="plain" type="success">{{ activeRate }}% 启用</el-tag>
      </div>
      <div class="account-main-number">{{ users.length }}</div>
      <div class="account-main-sub">当前纳管账号</div>
      <div class="account-progress">
        <div class="account-progress-fill" :style="{ width: `${activeRate}%` }"></div>
      </div>
    </div>
    <div class="account-mini-card">
      <span>启用账号</span>
      <strong>{{ activeCount }}</strong>
      <small>可正常登录小程序</small>
    </div>
    <div class="account-mini-card warn">
      <span>停用待处理</span>
      <strong>{{ inactiveCount }}</strong>
      <small>需确认是否恢复或删除</small>
    </div>
    <div class="account-mini-card">
      <span>现场人员</span>
      <strong>{{ workerCount }}</strong>
      <small>参与机房盘点执行</small>
    </div>
    <div class="account-mini-card">
      <span>片区覆盖</span>
      <strong>{{ areaCount }}</strong>
      <small>已配置负责片区</small>
    </div>
    <div class="account-mini-card info">
      <span>资料完整</span>
      <strong>{{ profileCompleteRate }}%</strong>
      <small>姓名、手机号完整</small>
    </div>
  </section>

  <div class="account-list-heading">
    <SectionTitle :icon="User" title="账号列表" />
    <el-button class="account-create-button" type="primary" :icon="Plus" :disabled="busy" @click="openCreate">新建空白账号</el-button>
  </div>
  <div class="toolbar">
    <el-form-item class="span-2" label="查询字段">
      <el-select v-model="queryField" @change="onQueryFieldChange">
        <el-option v-for="item in queryOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
    </el-form-item>
    <el-form-item class="span-8" label="查询内容">
      <el-select v-if="queryField === 'area_name'" v-model="queryText" clearable class="full-btn" placeholder="请选择负责片区">
        <el-option v-for="item in areaOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-else-if="queryField === 'role'" v-model="queryText" clearable class="full-btn" placeholder="请选择角色">
        <el-option label="现场人员" value="现场人员" />
        <el-option label="完整管理员" value="完整管理员" />
        <el-option label="协作管理员" value="协作管理员" />
      </el-select>
      <el-select v-else-if="queryField === 'is_active'" v-model="queryText" clearable class="full-btn" placeholder="请选择状态">
        <el-option label="启用" value="启用" />
        <el-option label="停用" value="停用" />
      </el-select>
      <el-input v-else v-model="queryText" clearable :placeholder="`输入${queryLabel}筛选账号`" />
    </el-form-item>
    <div class="span-2">
      <el-button class="full-btn" @click="queryText = ''">重置</el-button>
    </div>
  </div>
  <div class="table-wrap">
    <el-table v-loading="loading" border table-layout="auto" :data="filteredUsers" row-key="id" height="440" empty-text="没有符合条件的账号">
      <el-table-column prop="name" label="姓名" min-width="110" show-overflow-tooltip />
      <el-table-column label="角色" min-width="120"><template #default="{ row }">{{ adminRoleText(row) }}</template></el-table-column>
      <el-table-column label="负责片区" min-width="130"><template #default="{ row }">{{ displayArea(row.area_name) }}</template></el-table-column>
      <el-table-column prop="phone" label="手机号" min-width="135" />
      <el-table-column label="状态" min-width="100">
        <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" effect="plain">{{ row.is_active ? "启用" : "停用" }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :disabled="busy" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="row.role === 'admin'" size="small" :disabled="busy" @click="openPasswordReset(row)">重置密码</el-button>
          <el-button size="small" :type="row.is_active ? 'warning' : 'success'" plain :disabled="busy" @click="toggleUserStatus(row)">{{ row.is_active ? "停用" : "启用" }}</el-button>
          <el-button size="small" type="danger" plain :disabled="busy" @click="deleteUser(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新建账号' : '编辑账号'" width="560px">
    <el-form :model="form" :disabled="saving" label-position="top">
      <el-form-item label="登录编码">
        <el-input v-model="form.wx_openid" placeholder="后台账号例如 admin_b；现场人员填写微信 openid" />
      </el-form-item>
      <el-form-item label="姓名">
        <el-input v-model="form.name" />
      </el-form-item>
      <el-form-item label="角色">
        <el-select v-model="form.account_role" class="full-btn">
          <el-option label="现场人员" value="worker" />
          <el-option label="完整管理员" value="full" />
          <el-option label="协作管理员" value="collaborator" />
        </el-select>
      </el-form-item>
      <el-alert v-if="form.account_role === 'collaborator'" title="可查看全部机房、执行比对、修正盘点数据和更新综资基线；账号、任务管理写入及系统配置由完整管理员负责。" type="info" :closable="false" show-icon />
      <el-form-item v-if="needsInitialPassword" label="管理员登录密码（12–128 位）">
        <el-input v-model="form.password" type="password" show-password autocomplete="new-password" maxlength="128" />
      </el-form-item>
      <el-form-item v-if="needsInitialPassword" label="再次输入密码">
        <el-input v-model="form.confirm_password" type="password" show-password autocomplete="new-password" maxlength="128" />
      </el-form-item>
      <el-form-item :label="form.account_role === 'worker' ? '负责片区' : '登记片区（后台管理员可查看全部机房）'">
        <el-select v-model="form.area_name" class="full-btn">
          <el-option v-for="item in areaOptions" :key="item" :label="item" :value="item" />
        </el-select>
      </el-form-item>
      <el-form-item label="手机号">
        <el-input v-model="form.phone" />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="form.is_active">启用账号</el-checkbox>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="saveUser">保存</el-button>
    </template>
  </el-dialog>
  <el-dialog v-model="passwordDialogVisible" title="重置管理员密码" width="480px" @closed="clearPasswordForm">
    <p>{{ passwordUser?.name || passwordUser?.wx_openid }}：重置后该账号的现有登录会话失效，需使用新密码登录。</p>
    <el-form label-position="top" :disabled="saving">
      <el-form-item label="新密码（12–128 位）"><el-input v-model="passwordForm.password" type="password" show-password autocomplete="new-password" maxlength="128" /></el-form-item>
      <el-form-item label="再次输入密码"><el-input v-model="passwordForm.confirm_password" type="password" show-password autocomplete="new-password" maxlength="128" /></el-form-item>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="passwordDialogVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="resetPassword">重置密码</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Plus, User } from "@element-plus/icons-vue";
import PageHeader from "../components/PageHeader.vue";
import SectionTitle from "../components/SectionTitle.vue";
import { apiDelete, apiGet, apiPatch, apiPost, clearSession, refreshSessionUser } from "../api";
import { adminRoleText, permissions, sessionUser } from "../permissions";
import type { AdminUser } from "../types";

const users = ref<AdminUser[]>([]);
const loading = ref(false);
const saving = ref(false);
const actionUserId = ref<number | null>(null);
const busy = computed(() => saving.value || actionUserId.value !== null);
const queryField = ref("name");
const queryText = ref("");
const selectedUser = ref<AdminUser | null>(null);
const dialogVisible = ref(false);
const dialogMode = ref<"create" | "edit">("create");
const form = reactive({
  wx_openid: "",
  name: "",
  account_role: "worker",
  password: "",
  confirm_password: "",
  area_name: "汇聚及核心",
  phone: "",
  is_active: true,
});
const needsInitialPassword = computed(() => form.account_role !== "worker" && (dialogMode.value === "create" || selectedUser.value?.role !== "admin"));
const passwordDialogVisible = ref(false);
const passwordUser = ref<AdminUser | null>(null);
const passwordForm = reactive({ password: "", confirm_password: "" });

const areaOptions = ["汇聚及核心", "南区", "北区", "西区"];
const queryOptions = [
  { label: "姓名", value: "name" },
  { label: "手机号", value: "phone" },
  { label: "登录编码", value: "wx_openid" },
  { label: "负责片区", value: "area_name" },
  { label: "角色", value: "role" },
  { label: "状态", value: "is_active" },
];

const queryLabel = computed(() => queryOptions.find((item) => item.value === queryField.value)?.label || "查询内容");
const activeCount = computed(() => users.value.filter((user) => user.is_active).length);
const inactiveCount = computed(() => Math.max(users.value.length - activeCount.value, 0));
const activeRate = computed(() => users.value.length ? Math.round((activeCount.value / users.value.length) * 100) : 0);
const workerCount = computed(() => users.value.filter((user) => user.role === "worker").length);
const areaCount = computed(() => {
  const areas = new Set<string>();
  users.value.forEach((user) => areas.add(displayArea(user.area_name)));
  return areas.size;
});
const profileCompleteRate = computed(() => {
  if (!users.value.length) return 0;
  const completeCount = users.value.filter((user) => Boolean(user.name && user.phone)).length;
  return Math.round((completeCount / users.value.length) * 100);
});
const filteredUsers = computed(() => {
  const needle = String(queryText.value || "").trim().toLowerCase();
  return users.value.filter((user) => !needle || userValue(user, queryField.value).toLowerCase().includes(needle));
});

function displayArea(areaName?: string) {
  const value = String(areaName || "").trim();
  if (!value || ["全市", "默认片区", "全部片区", "全片区", "*"].includes(value)) return "汇聚及核心";
  if (areaOptions.includes(value)) return value;
  const parts = value.split(/[,，、/;；\s]+/).map((item) => item.trim()).filter(Boolean);
  return ["南区", "北区", "西区"].every((item) => parts.includes(item)) ? "汇聚及核心" : value;
}

function userValue(user: AdminUser, field: string) {
  if (field === "role") return adminRoleText(user);
  if (field === "is_active") return user.is_active ? "启用" : "停用";
  if (field === "area_name") return displayArea(user.area_name);
  return String((user as unknown as Record<string, unknown>)[field] || "");
}

function onQueryFieldChange() {
  queryText.value = "";
}

function resetForm(user?: AdminUser) {
  form.wx_openid = user?.wx_openid || "";
  form.name = user?.name || "";
  form.account_role = user?.role === "admin" ? user.admin_scope || "full" : "worker";
  form.password = "";
  form.confirm_password = "";
  form.area_name = displayArea(user?.area_name);
  form.phone = user?.phone || "";
  form.is_active = user ? Boolean(user.is_active) : true;
}

async function loadUsers() {
  loading.value = true;
  try {
    users.value = await apiGet<AdminUser[]>("/users");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  selectedUser.value = null;
  dialogMode.value = "create";
  resetForm();
  dialogVisible.value = true;
}

function openEdit(user: AdminUser) {
  selectedUser.value = user;
  dialogMode.value = "edit";
  resetForm(user);
  dialogVisible.value = true;
}

async function saveUser() {
  if (saving.value) return;
  if (needsInitialPassword.value && !validPassword(form.password, form.confirm_password)) return;
  const payload = {
    wx_openid: form.wx_openid.trim(), name: form.name.trim(),
    role: form.account_role === "worker" ? "worker" : "admin",
    admin_scope: form.account_role === "collaborator" ? "collaborator" : "full",
    area_name: form.area_name, phone: form.phone.trim(), is_active: form.is_active ? 1 : 0,
    ...(needsInitialPassword.value ? { password: form.password } : {}),
  };
  saving.value = true;
  try {
    if (dialogMode.value === "create") {
      await apiPost("/users", payload);
      ElMessage.success("账号已创建");
    } else if (selectedUser.value) {
      await apiPatch(`/users/${selectedUser.value.id}`, payload);
      ElMessage.success("账号已保存");
    }
    dialogVisible.value = false;
    form.password = "";
    form.confirm_password = "";
    if (selectedUser.value?.id === sessionUser.value?.id) {
      if (payload.role !== "admin" || !payload.is_active) clearSession();
      else await refreshSessionUser();
    }
    if (permissions.value.manageAccounts) await loadUsers();
  } catch {
    return;
  } finally {
    saving.value = false;
  }
}

function validPassword(password: string, confirmation: string) {
  if (!password.trim()) {
    ElMessage.warning("管理员密码不能全部为空白字符");
    return false;
  }
  if (password.length < 12 || password.length > 128) {
    ElMessage.warning("管理员密码长度应为 12–128 位");
    return false;
  }
  if (password !== confirmation) {
    ElMessage.warning("两次输入的密码不一致");
    return false;
  }
  return true;
}

function clearPasswordForm() {
  passwordForm.password = "";
  passwordForm.confirm_password = "";
}

function openPasswordReset(user: AdminUser) {
  clearPasswordForm();
  passwordUser.value = user;
  passwordDialogVisible.value = true;
}

async function resetPassword() {
  if (!passwordUser.value || saving.value || !validPassword(passwordForm.password, passwordForm.confirm_password)) return;
  saving.value = true;
  try {
    await apiPost(`/users/${passwordUser.value.id}/password`, { password: passwordForm.password });
    passwordDialogVisible.value = false;
    clearPasswordForm();
    ElMessage.success("密码已重置，该账号需重新登录");
    if (passwordUser.value.id === sessionUser.value?.id) await refreshSessionUser();
  } finally {
    saving.value = false;
  }
}

async function toggleUserStatus(user: AdminUser) {
  if (busy.value) return;
  actionUserId.value = user.id;
  try {
    await apiPatch(`/users/${user.id}`, { is_active: user.is_active ? 0 : 1 });
    ElMessage.success(user.is_active ? "账号已停用" : "账号已启用");
    await loadUsers();
  } catch {
    return;
  } finally {
    actionUserId.value = null;
  }
}

async function deleteUser(user: AdminUser) {
  if (busy.value) return;
  try {
    await ElMessageBox.confirm(`确认删除账号：${user.name || user.wx_openid}？`, "删除账号", { type: "warning" });
  } catch {
    return;
  }
  actionUserId.value = user.id;
  try {
    await apiDelete(`/users/${user.id}`);
    ElMessage.success("账号已删除");
    await loadUsers();
  } catch {
    return;
  } finally {
    actionUserId.value = null;
  }
}

onMounted(loadUsers);
</script>

<style scoped>
.account-overview {
  display: grid;
  grid-template-columns: 1.2fr repeat(5, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.account-main-card,
.account-mini-card {
  position: relative;
  min-height: 124px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.account-main-card {
  background:
    linear-gradient(135deg, rgba(35, 135, 245, 0.1), rgba(24, 197, 200, 0.11)),
    rgba(255, 255, 255, 0.94);
}

.account-main-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #31536f;
  font-size: 15px;
  font-weight: 720;
}

.account-main-number {
  margin-top: 14px;
  color: #1068c2;
  font-size: 42px;
  line-height: 1;
  font-weight: 860;
}

.account-main-sub {
  margin-top: 6px;
  color: var(--muted);
  font-size: 14px;
}

.account-progress {
  height: 8px;
  margin-top: 16px;
  border-radius: 999px;
  background: #dfeefa;
  overflow: hidden;
}

.account-progress-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2387f5, #18c5c8);
}

.account-mini-card::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: linear-gradient(90deg, #2387f5, #18c5c8);
}

.account-mini-card.warn::before {
  background: linear-gradient(90deg, #f3ac3d, #ffd16b);
}

.account-mini-card.info::before {
  background: linear-gradient(90deg, #7b8cff, #23c7dd);
}

.account-mini-card span {
  color: #5d7188;
  font-size: 15px;
  font-weight: 680;
}

.account-mini-card strong {
  display: block;
  margin-top: 12px;
  color: #1267bd;
  font-size: 32px;
  line-height: 1;
  font-weight: 850;
}

.account-mini-card small {
  display: block;
  margin-top: 10px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}

.account-list-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin: 20px 0 12px;
}

.account-list-heading :deep(.section-title) {
  margin: 0;
}

.account-list-heading .el-button.account-create-button {
  --el-button-text-color: #fff;
  --el-button-hover-text-color: #fff;
  --el-button-active-text-color: #fff;
  --el-button-disabled-text-color: #fff;
  min-height: 36px;
  padding: 9px 16px;
  color: #fff;
  border-color: transparent;
  background: linear-gradient(135deg, #1976e5, #129fad);
  box-shadow: 0 4px 10px rgba(31, 125, 242, 0.2);
}

.account-list-heading .el-button.account-create-button:not(.is-disabled):hover,
.account-list-heading .el-button.account-create-button:not(.is-disabled):focus-visible {
  color: #fff;
  border-color: transparent;
  background: linear-gradient(135deg, #1267cf, #0c8997);
}

.account-list-heading .el-button.account-create-button.is-disabled,
.account-list-heading .el-button.account-create-button.is-disabled:hover {
  color: #fff;
  border-color: transparent;
  background: #83a6ca;
  box-shadow: none;
  opacity: 1;
}

@media (max-width: 1280px) {
  .account-overview {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .account-overview {
    grid-template-columns: 1fr;
  }
}
</style>
