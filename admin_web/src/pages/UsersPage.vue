<template>
  <PageHeader :icon="User" title="账号管理" desc="维护账号、片区和启停状态" />

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
      <small>姓名、工号、手机号完整</small>
    </div>
  </section>

  <SectionTitle :icon="User" title="账号列表" muted="总览账号，维护请到下方查询" />
  <div class="table-wrap">
    <el-table border table-layout="auto" :data="userRows" height="360">
      <el-table-column prop="姓名" label="姓名" min-width="110" show-overflow-tooltip />
      <el-table-column prop="角色" label="角色" min-width="110" />
      <el-table-column prop="负责片区" label="负责片区" min-width="130" />
      <el-table-column prop="手机号" label="手机号" min-width="135" />
      <el-table-column prop="工号" label="工号" min-width="120" />
      <el-table-column label="状态" min-width="100">
        <template #default="{ row }"><el-tag :type="row.状态 === '启用' ? 'success' : 'info'" effect="plain">{{ row.状态 }}</el-tag></template>
      </el-table-column>
    </el-table>
  </div>

  <SectionTitle :icon="Tools" title="账号维护" muted="查询并勾选账号后编辑" />
  <div class="toolbar">
    <el-form-item class="span-2" label="查询字段">
      <el-select v-model="queryField" @change="onQueryFieldChange">
        <el-option v-for="item in queryOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
    </el-form-item>
    <el-form-item class="span-6" label="查询内容">
      <el-select v-if="queryField === 'area_name'" v-model="queryText" class="full-btn" placeholder="请选择负责片区">
        <el-option v-for="item in areaOptions" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-else-if="queryField === 'role'" v-model="queryText" class="full-btn" placeholder="请选择角色">
        <el-option label="现场人员" value="现场人员" />
        <el-option label="管理员" value="管理员" />
      </el-select>
      <el-select v-else-if="queryField === 'is_active'" v-model="queryText" class="full-btn" placeholder="请选择状态">
        <el-option label="启用" value="启用" />
        <el-option label="停用" value="停用" />
      </el-select>
      <el-input v-else v-model="queryText" :placeholder="`请输入${queryLabel}`" />
    </el-form-item>
    <div class="span-2">
      <el-button type="primary" class="full-btn" @click="runQuery">查询</el-button>
    </div>
    <div class="span-2">
      <el-button class="full-btn" @click="openCreate"><el-icon><Plus /></el-icon>新建空白账号</el-button>
    </div>
  </div>

  <el-dialog v-model="resultDialogVisible" title="查询结果" width="900px" class="result-dialog">
    <el-empty v-if="!candidates.length" description="没有查到相关账号。" />
    <template v-else>
      <div class="dialog-tip">查询到 {{ candidates.length }} 个账号，请勾选需要维护的账号。</div>
      <div class="table-wrap compact">
        <el-table border table-layout="auto" :data="candidates" height="320" @selection-change="onSelectionChange">
          <el-table-column type="selection" width="48" />
          <el-table-column prop="name" label="姓名" min-width="110" show-overflow-tooltip />
          <el-table-column label="角色" min-width="110"><template #default="{ row }">{{ roleText(row.role) }}</template></el-table-column>
          <el-table-column label="负责片区" min-width="130"><template #default="{ row }">{{ displayArea(row.area_name) }}</template></el-table-column>
          <el-table-column prop="phone" label="手机号" min-width="135" />
          <el-table-column prop="employee_no" label="工号" min-width="120" />
          <el-table-column label="状态" min-width="100"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" effect="plain">{{ row.is_active ? "启用" : "停用" }}</el-tag></template></el-table-column>
        </el-table>
      </div>
    </template>
    <template #footer>
      <span class="dialog-footer-note">已选 {{ selectedUser ? 1 : 0 }} 个账号</span>
      <el-button @click="resultDialogVisible = false">关闭</el-button>
      <el-button type="primary" :disabled="!selectedUser" @click="openEdit">编辑选中账号</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新建账号' : '编辑账号'" width="560px">
    <el-form :model="form" label-position="top">
      <el-form-item label="登录编码">
        <el-input v-model="form.wx_openid" placeholder="例如 worker_006 或真实 openid" />
      </el-form-item>
      <el-form-item label="姓名">
        <el-input v-model="form.name" />
      </el-form-item>
      <el-form-item label="工号">
        <el-input v-model="form.employee_no" />
      </el-form-item>
      <el-form-item label="角色">
        <el-select v-model="form.role" class="full-btn">
          <el-option label="现场人员" value="worker" />
          <el-option label="管理员" value="admin" />
        </el-select>
      </el-form-item>
      <el-form-item label="负责片区">
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
      <el-button v-if="dialogMode === 'edit'" type="danger" plain @click="deleteUser">删除账号</el-button>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" @click="saveUser">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Plus, Tools, User } from "@element-plus/icons-vue";
import PageHeader from "../components/PageHeader.vue";
import SectionTitle from "../components/SectionTitle.vue";
import { apiDelete, apiGet, apiPatch, apiPost } from "../api";
import type { AdminUser } from "../types";

const users = ref<AdminUser[]>([]);
const queried = ref(false);
const queryField = ref("name");
const queryText = ref("");
const candidates = ref<AdminUser[]>([]);
const selectedUser = ref<AdminUser | null>(null);
const resultDialogVisible = ref(false);
const dialogVisible = ref(false);
const dialogMode = ref<"create" | "edit">("create");
const form = reactive({
  wx_openid: "",
  name: "",
  employee_no: "",
  role: "worker",
  area_name: "汇聚及核心",
  phone: "",
  is_active: true,
});

const areaOptions = ["汇聚及核心", "南区", "北区", "西区"];
const queryOptions = [
  { label: "姓名", value: "name" },
  { label: "手机号", value: "phone" },
  { label: "登录编码", value: "wx_openid" },
  { label: "工号", value: "employee_no" },
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
  const completeCount = users.value.filter((user) => Boolean(user.name && user.employee_no && user.phone)).length;
  return Math.round((completeCount / users.value.length) * 100);
});
const userRows = computed(() => users.value.map((user) => ({
  姓名: user.name || "",
  角色: roleText(user.role),
  负责片区: displayArea(user.area_name),
  手机号: user.phone || "",
  工号: user.employee_no || "",
  状态: user.is_active ? "启用" : "停用",
})));

function roleText(role: string) {
  return role === "admin" ? "管理员" : "现场人员";
}

function displayArea(areaName?: string) {
  const value = String(areaName || "").trim();
  if (!value || ["全市", "默认片区", "全部片区", "全片区", "*"].includes(value)) return "汇聚及核心";
  if (areaOptions.includes(value)) return value;
  const parts = value.split(/[,，、/;；\s]+/).map((item) => item.trim()).filter(Boolean);
  return ["南区", "北区", "西区"].every((item) => parts.includes(item)) ? "汇聚及核心" : value;
}

function userValue(user: AdminUser, field: string) {
  if (field === "role") return roleText(user.role);
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
  form.employee_no = user?.employee_no || "";
  form.role = user?.role || "worker";
  form.area_name = displayArea(user?.area_name);
  form.phone = user?.phone || "";
  form.is_active = user ? Boolean(user.is_active) : true;
}

async function loadUsers() {
  users.value = await apiGet<AdminUser[]>("/users");
}

function runQuery() {
  const needle = queryText.value.trim().toLowerCase();
  queried.value = true;
  selectedUser.value = null;
  if (!needle) {
    ElMessage.warning("请输入查询内容后再查询");
    candidates.value = [];
    resultDialogVisible.value = false;
    return;
  }
  candidates.value = users.value.filter((user) => userValue(user, queryField.value).toLowerCase().includes(needle));
  resultDialogVisible.value = true;
}

function onSelectionChange(rows: AdminUser[]) {
  selectedUser.value = rows[rows.length - 1] || null;
}

function openCreate() {
  dialogMode.value = "create";
  resetForm();
  dialogVisible.value = true;
}

function openEdit() {
  if (!selectedUser.value) return;
  dialogMode.value = "edit";
  resetForm(selectedUser.value);
  dialogVisible.value = true;
  resultDialogVisible.value = false;
}

async function saveUser() {
  const payload = { ...form, is_active: form.is_active ? 1 : 0 };
  if (dialogMode.value === "create") {
    await apiPost("/users", payload);
    ElMessage.success("账号已创建");
  } else if (selectedUser.value) {
    await apiPatch(`/users/${selectedUser.value.id}`, payload);
    ElMessage.success("账号已保存");
  }
  dialogVisible.value = false;
  await loadUsers();
  if (queried.value) runQuery();
}

async function deleteUser() {
  if (!selectedUser.value) return;
  await ElMessageBox.confirm(`确认删除账号：${selectedUser.value.name || selectedUser.value.wx_openid}？`, "删除账号", { type: "warning" });
  await apiDelete(`/users/${selectedUser.value.id}`);
  ElMessage.success("账号已删除");
  dialogVisible.value = false;
  await loadUsers();
  if (queried.value) runQuery();
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

.span-9 { grid-column: span 9; }

.dialog-tip {
  margin-bottom: 12px;
  color: var(--muted);
  font-size: 14px;
}

.dialog-footer-note {
  float: left;
  color: var(--muted);
  line-height: 32px;
}

.table-wrap.compact {
  margin-bottom: 0;
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
