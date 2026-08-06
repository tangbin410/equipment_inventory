<template>
  <section class="qr-page">
    <div class="page-header">
      <div>
        <h1>二维码资产</h1>
        <p>二维码只保存固定标识，设备改绑后扫码结果实时更新。</p>
      </div>
      <div class="header-actions">
        <el-button @click="downloadTemplate">下载导入模板</el-button>
        <el-upload :show-file-list="false" accept=".xlsx" :http-request="importAssets">
          <el-button>批量导入</el-button>
        </el-upload>
        <el-button type="primary" @click="openCreate">新增资产</el-button>
      </div>
    </div>

    <div class="toolbar">
      <el-input v-model="query" clearable placeholder="搜索资产条码、型号、机房或传输设备" @keyup.enter="loadAssets" />
      <el-button @click="loadAssets">查询</el-button>
    </div>

    <el-table v-loading="loading" :data="assets" border stripe row-key="id">
      <el-table-column label="固定编码" min-width="180">
        <template #default="{ row }">
          <span class="fixed-code">{{ shortToken(row.token) }}</span>
          <el-button link type="primary" @click="copyToken(row.token)">复制</el-button>
        </template>
      </el-table-column>
      <el-table-column prop="asset_barcode" label="资产条码" min-width="150" />
      <el-table-column prop="barcode_model" label="条码型号" min-width="120" />
      <el-table-column prop="room_name" label="所在机房" min-width="140" />
      <el-table-column label="当前传输设备" min-width="160">
        <template #default="{ row }">{{ row.current_device_name || "未绑定" }}</template>
      </el-table-column>
      <el-table-column label="历史记录" width="100">
        <template #default="{ row }"><el-button link type="primary" @click="showHistory(row)">{{ row.history?.length || 0 }} 条</el-button></template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openBinding(row)">改绑设备</el-button>
          <el-button link @click="downloadQr(row)">下载二维码</el-button>
          <el-button link @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="deleteAsset(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="assetDialog.open" :title="assetDialog.editing ? '编辑资产' : '新增资产'" width="460px">
      <el-form label-width="100px">
        <el-form-item label="资产条码"><el-input v-model="assetDialog.form.asset_barcode" /></el-form-item>
        <el-form-item label="条码型号"><el-input v-model="assetDialog.form.barcode_model" /></el-form-item>
        <el-form-item label="所在机房"><el-input v-model="assetDialog.form.room_name" /></el-form-item>
        <el-form-item label="当前设备"><el-input v-model="assetDialog.form.current_device_name" /></el-form-item>
        <el-form-item v-if="assetDialog.editing" label="变更原因"><el-input v-model="assetDialog.form.change_reason" placeholder="当前设备变化时填写，如：设备替换" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="assetDialog.open = false">取消</el-button><el-button type="primary" @click="saveAsset">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="bindingDialog.open" title="变更当前传输设备" width="430px">
      <el-form label-width="90px">
        <el-form-item label="资产条码"><span>{{ bindingDialog.asset?.asset_barcode }}</span></el-form-item>
        <el-form-item label="当前设备"><span>{{ bindingDialog.asset?.current_device_name || "未绑定" }}</span></el-form-item>
        <el-form-item label="新设备"><el-input v-model="bindingDialog.form.device_name" /></el-form-item>
        <el-form-item label="变更原因"><el-input v-model="bindingDialog.form.change_reason" placeholder="如：设备替换" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="bindingDialog.open = false">取消</el-button><el-button type="primary" @click="saveBinding">确认改绑</el-button></template>
    </el-dialog>

    <el-dialog v-model="historyDialog.open" title="历史关联设备" width="720px">
      <div class="history-toolbar">
        <span>资产条码：{{ historyDialog.asset?.asset_barcode }}</span>
        <el-button type="primary" size="small" @click="openHistoryCreate">补录历史</el-button>
      </div>
      <el-table :data="historyDialog.rows" border size="small">
        <el-table-column prop="device_name" label="设备" />
        <el-table-column prop="room_name" label="机房" />
        <el-table-column prop="bound_at" label="开始时间" min-width="160" />
        <el-table-column prop="unbound_at" label="结束时间" min-width="160"><template #default="{ row }">{{ row.unbound_at || "当前" }}</template></el-table-column>
        <el-table-column prop="change_reason" label="变更原因" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <template v-if="row.unbound_at">
              <el-button link type="primary" @click="openHistoryEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="deleteHistory(row)">删除</el-button>
            </template>
            <el-tag v-else type="success" size="small">当前关联</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="historyFormDialog.open" :title="historyFormDialog.editing ? '编辑历史记录' : '补录历史记录'" width="460px">
      <el-form label-width="90px">
        <el-form-item label="设备名称"><el-input v-model="historyFormDialog.form.device_name" /></el-form-item>
        <el-form-item label="所在机房"><el-input v-model="historyFormDialog.form.room_name" /></el-form-item>
        <el-form-item label="开始时间"><el-date-picker v-model="historyFormDialog.form.bound_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item>
        <el-form-item label="结束时间"><el-date-picker v-model="historyFormDialog.form.unbound_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item>
        <el-form-item label="变更原因"><el-input v-model="historyFormDialog.form.change_reason" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="historyFormDialog.open = false">取消</el-button><el-button type="primary" @click="saveHistory">保存</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import type { UploadRequestOptions } from "element-plus";
import { apiDelete, apiGet, apiPatch, apiPost, downloadBlob } from "../api";
import type { QrAsset, QrAssetBinding, QrAssetForm } from "../types";

const assets = ref<QrAsset[]>([]);
const query = ref("");
const loading = ref(false);
const assetDialog = reactive({
  open: false,
  editing: false,
  id: 0,
  form: { asset_barcode: "", barcode_model: "", room_name: "", current_device_name: "", change_reason: "" } as QrAssetForm,
});
const bindingDialog = reactive({
  open: false,
  asset: null as QrAsset | null,
  form: { device_name: "", change_reason: "" } as QrAssetBinding,
});
const historyDialog = reactive({ open: false, asset: null as QrAsset | null, rows: [] as QrAsset["history"] });
const historyFormDialog = reactive({
  open: false,
  editing: false,
  historyId: 0,
  form: { device_name: "", room_name: "", bound_at: "", unbound_at: "", change_reason: "" } as QrAssetBinding,
});

async function loadAssets() {
  loading.value = true;
  try { assets.value = await apiGet<QrAsset[]>("/qr-assets", { params: { query: query.value } }); }
  finally { loading.value = false; }
}

function openCreate() {
  assetDialog.editing = false;
  assetDialog.id = 0;
  assetDialog.form = { asset_barcode: "", barcode_model: "", room_name: "", current_device_name: "", change_reason: "" };
  assetDialog.open = true;
}

function openEdit(row: QrAsset) {
  assetDialog.editing = true;
  assetDialog.id = row.id;
  assetDialog.form = {
    asset_barcode: row.asset_barcode,
    barcode_model: row.barcode_model,
    room_name: row.room_name,
    current_device_name: row.current_device_name || "",
    change_reason: "",
  };
  assetDialog.open = true;
}

async function saveAsset() {
  if (!assetDialog.form.asset_barcode.trim() || !assetDialog.form.room_name.trim()) { ElMessage.warning("资产条码和机房不能为空"); return; }
  if (assetDialog.editing) await apiPatch(`/qr-assets/${assetDialog.id}`, assetDialog.form);
  else await apiPost("/qr-assets", assetDialog.form);
  assetDialog.open = false;
  ElMessage.success("保存成功");
  await loadAssets();
}

async function deleteAsset(row: QrAsset) {
  try {
    await ElMessageBox.confirm(
      `确认删除资产 ${row.asset_barcode}？固定编码将永久保留且不会重新分配。`,
      "删除资产组",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "取消" },
    );
  } catch { return; }
  await apiDelete(`/qr-assets/${row.id}`);
  ElMessage.success("资产组已删除");
  await loadAssets();
}

function openBinding(row: QrAsset) {
  bindingDialog.asset = row;
  bindingDialog.form = { device_name: row.current_device_name || "", change_reason: "" };
  bindingDialog.open = true;
}

async function saveBinding() {
  if (!bindingDialog.asset) return;
  await apiPost(`/qr-assets/${bindingDialog.asset.id}/binding`, bindingDialog.form);
  bindingDialog.open = false;
  ElMessage.success("设备关联已更新，原记录已进入历史");
  await loadAssets();
}

async function downloadQr(row: QrAsset) {
  await downloadBlob(`/qr-assets/${row.id}/qrcode`, `${row.asset_barcode}.png`);
}

async function downloadTemplate() {
  await downloadBlob("/qr-assets/import-template", "二维码资产导入模板.xlsx");
}

async function importAssets(options: UploadRequestOptions) {
  const data = new FormData();
  data.append("file", options.file);
  const result = await apiPost<{ created: number; message: string }>("/qr-assets/import", data, { timeout: 60000 });
  ElMessage.success(result.message || `成功导入 ${result.created} 组资产`);
  await loadAssets();
}

function shortToken(token: string) {
  return token.length > 16 ? `${token.slice(0, 8)}...${token.slice(-6)}` : token;
}

async function copyToken(token: string) {
  await navigator.clipboard.writeText(token);
  ElMessage.success("固定编码已复制");
}

async function showHistory(row: QrAsset) {
  const detail = await apiGet<QrAsset>(`/qr-assets/${row.id}`);
  historyDialog.asset = detail;
  historyDialog.rows = detail.history || [];
  historyDialog.open = true;
}

function openHistoryCreate() {
  if (!historyDialog.asset) return;
  historyFormDialog.editing = false;
  historyFormDialog.historyId = 0;
  historyFormDialog.form = {
    device_name: "",
    room_name: historyDialog.asset.room_name,
    bound_at: "",
    unbound_at: "",
    change_reason: "管理员补录历史记录",
  };
  historyFormDialog.open = true;
}

function openHistoryEdit(row: QrAssetBinding) {
  historyFormDialog.editing = true;
  historyFormDialog.historyId = row.id || 0;
  historyFormDialog.form = {
    device_name: row.device_name,
    room_name: row.room_name || "",
    bound_at: row.bound_at || "",
    unbound_at: row.unbound_at || "",
    change_reason: row.change_reason || "",
  };
  historyFormDialog.open = true;
}

async function refreshHistory() {
  if (!historyDialog.asset) return;
  const detail = await apiGet<QrAsset>(`/qr-assets/${historyDialog.asset.id}`);
  historyDialog.asset = detail;
  historyDialog.rows = detail.history || [];
  await loadAssets();
}

async function saveHistory() {
  if (!historyDialog.asset || !historyFormDialog.form.device_name.trim()) {
    ElMessage.warning("历史关联设备名称不能为空");
    return;
  }
  if (historyFormDialog.editing) {
    await apiPatch(
      `/qr-assets/${historyDialog.asset.id}/history/${historyFormDialog.historyId}`,
      historyFormDialog.form,
    );
  } else {
    await apiPost(`/qr-assets/${historyDialog.asset.id}/history`, historyFormDialog.form);
  }
  historyFormDialog.open = false;
  ElMessage.success("历史记录已保存");
  await refreshHistory();
}

async function deleteHistory(row: QrAssetBinding) {
  if (!historyDialog.asset || !row.id) return;
  try {
    await ElMessageBox.confirm(`确认删除历史关联设备 ${row.device_name}？`, "删除历史记录", {
      type: "warning",
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
    });
  } catch { return; }
  await apiDelete(`/qr-assets/${historyDialog.asset.id}/history/${row.id}`);
  ElMessage.success("历史记录已删除");
  await refreshHistory();
}

onMounted(loadAssets);
</script>

<style scoped>
.qr-page { padding: 24px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; color: #1f2d3d; }
.page-header p { margin: 0; color: #718096; font-size: 13px; }
.header-actions { display: flex; gap: 10px; align-items: center; }
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; max-width: 620px; }
.fixed-code { margin-right: 8px; font-family: Consolas, monospace; color: #475569; }
.history-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; color: #475569; }
</style>
