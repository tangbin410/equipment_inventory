/* 设备盘点页 - 核心逻辑 */

const app = getApp();
const { getTaskItems, submitScan, submitMissing, submitSurplus, deleteSurplus, submitCabinet, deleteCabinet, completeInventoryTask, releaseTask: releaseTaskApi } = require("../../utils/api");
const { suggestDeviceModels } = require("../../utils/device-models");

function emptyCabinetForm() {
  return {
    candidateIndex: 0,
    cabinetTypeIndex: 0,
    cabinetType: "",
    cabinetBarcode: "",
    noBarcode: false,
    rackNo: "",
    notes: "",
  };
}

function emptySurplusForm() {
  return {
    deviceName: "",
    actualModel: "",
    _modelNotApplicable: false,
    modelSuggestions: [],
    modelConfirmed: false,
    assetBarcodes: [],
    assetNoBarcode: false,
    cabinetBarcodes: [],
    cabinetNoBarcode: false,
    rackNo: "",
    notes: "",
    _assetText: "",
    _hasAssetBarcode: false,
    _cabinetText: "",
    _hasCabinetBarcode: false,
  };
}

Page({
  data: {
    taskName: "",
    devices: [],          // 当前筛选后的设备列表
    allDevices: [],       // 完整设备列表
    keyword: "",
    activeTab: "devices",
    deviceFilter: "all",
    filterOptions: ["全部设备", "未盘点", "异常设备", "盘盈", "盘亏"],
    activeDeviceId: null, // 当前展开的设备ID
    showSurplusForm: false,
    surplusForm: emptySurplusForm(),
    cabinetRecords: [],
    deletingCabinetId: null,
    rackRows: [],
    rackCandidateLabels: ["手工输入本次机架"],
    cabinetSummary: { total: 0, equipment: 0, odf: 0, iodf: 0 },
    cabinetTypeLabels: ["ODF", "IODF"],
    showCabinetForm: false,
    cabinetForm: emptyCabinetForm(),
    loading: true,
    taskLocked: true,
    taskStatus: "",
    replacementTaskId: null,
    replacementNotice: "",
    submitting: false,
    collectionNotice: null,
    scannedCount: 0,
    totalCount: 0,
  },

  /** 页面加载：从 URL 获取 task_id，拉设备清单 */
  onLoad(options) {
    if (!options.task_id) {
      wx.showToast({ title: "缺少任务ID", icon: "none" });
      return;
    }
    this.taskId = options.task_id;
    this.restoreCabinetForm();
    this.restoreSurplusForm();
    return this.loadDevices();
  },

  /** 每次显示页面时刷新（从提交页返回时更新） */
  onShow() {
    if (this.data.submitting) return;
    // wx.scanCode 返回时也会触发 onShow。
    // 如果这里重新 loadDevices，会导致设备列表整页重绘，滚动位置回到上方。
    if (this.skipNextOnShowReload) {
      this.skipNextOnShowReload = false;
      return;
    }
    if (this.taskId && !this.data.loading) {
      return this.loadDevices();
    }
  },

  onHide() {
    this.clearCollectionNotice();
  },

  onUnload() {
    this.clearCollectionNotice();
  },

  /** 加载设备清单 */
  async loadDevices() {
    this.setData({ loading: true });
    try {
      const result = await getTaskItems(this.taskId);
      const task = result.task;
      const items = result.items;
      const taskLocked = task.status !== "doing" || Boolean(task.superseded_by_task_id);
      const cabinetRecords = (result.cabinet_records || [])
        .filter((item) => ["odf", "iodf"].includes(item.cabinet_type));

      // 机架没有综资清单；仅把同机房往期结果作为本次录入参考。
      const rackRows = (result.rack_reference || []).map((item) => ({
        id: item.id, name: item.rack_no || item.cabinet_name,
        type: item.cabinet_type, referenceBarcode: item.cabinet_barcode || "",
      }));
      const devices = items.map((item) => this.makeDevice(item, taskLocked));
      this.setData({
        taskName: task.task_name,
        taskLocked,
        taskStatus: task.status,
        replacementTaskId: task.superseded_by_task_id || null,
        replacementNotice: task.replacement_notice || "该机房有最新任务，请释放当前任务并领取最新任务。",
        ...(taskLocked ? { showCabinetForm: false, showSurplusForm: false } : {}),
        allDevices: devices,
        devices,
        rackRows,
        rackCandidateLabels: ["手工输入本次机架", ...rackRows.map((item) => `${item.type.toUpperCase()} · ${item.name}`)],
        totalCount: this.countNormalDevices(devices),
        scannedCount: this.countScannedDevices(devices),
        cabinetRecords,
        cabinetSummary: result.cabinet_summary || { total: 0, equipment: 0, odf: 0, iodf: 0 },
        loading: false,
      });
      this.applyDeviceFilters();
    } catch (err) {
      // 无法确认任务是否仍可采集时，先保持只读，避免陈旧页面继续修改。
      this.setData({ loading: false, taskLocked: true });
    }
  },

  releaseReplacedTask() {
    if (!this.data.replacementTaskId || this.data.taskStatus !== "doing" || this.data.loading || this.data.submitting) return;
    wx.showModal({
      title: "释放旧任务并前往领取",
      content: `该机房有最新任务 #${this.data.replacementTaskId}。释放后旧任务作废，未变化设备的已盘结果、手工机架和盘盈设备会沿用到最新任务。请在任务池领取最新任务后继续盘点。`,
      confirmText: "释放任务",
      success: async (res) => {
        if (!res.confirm || this.data.loading || this.data.submitting) return;
        this.setData({ submitting: true });
        try {
          const result = await releaseTaskApi(this.taskId, "同机房清单更新，释放旧任务");
          const latestId = result.replacement_task_id;
          // 释放后旧任务已作废，不能再请求旧清单，否则会弹出“任务不存在”。
          this.setData({ taskLocked: true, taskStatus: "void", replacementTaskId: latestId || this.data.replacementTaskId });
          wx.redirectTo({ url: "/pages/claim/claim" + (latestId ? `?task_id=${latestId}` : "") });
        } catch (err) {
          await this.loadDevices();
        } finally {
          this.setData({ submitting: false });
        }
      },
    });
  },

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value });
    this.applyDeviceFilters();
  },

  switchTab(e) {
    this.setData({ activeTab: e.currentTarget.dataset.tab });
  },

  onDeviceFilterChange(e) {
    this.setData({ deviceFilter: ["all", "pending", "mismatch", "surplus", "missing"][Number(e.detail.value)] });
    this.applyDeviceFilters();
  },

  applyDeviceFilters() {
    const keyword = this.data.keyword.trim().toLowerCase();
    const filter = this.data.deviceFilter;
    const matches = this.data.allDevices.filter((device) => this.deviceMatchesFilter(device, keyword, filter));
    const devices = [...matches.filter((device) => !device.submitted), ...matches.filter((device) => device.submitted)];
    this.setData({ devices });
  },

  deviceMatchesFilter(device, keyword = this.data.keyword.trim().toLowerCase(), filter = this.data.deviceFilter) {
    const searchableText = [
      device.device_name,
      device.expected_model,
      device.actualModel,
      device.rackNo,
    ].join(" ").toLowerCase();
    // 未盘设备按清单条码查找；已录入设备也可按现场条码查找。
    const barcodeKeyword = keyword.replace(/\s+/g, "");
    const matchesBarcode = [device.expectedBarcode, ...device.assetBarcodes, ...device.cabinetBarcodes]
      .some((barcode) => String(barcode || "").replace(/\s+/g, "").toLowerCase().includes(barcodeKeyword));
    const matchesText = !keyword || searchableText.includes(keyword) || matchesBarcode;
    const matchesStatus =
      filter === "all"
      || (filter === "pending" && !device.submitted)
      || (filter === "mismatch" && device.submitted && device.match_status === "不一致")
      || (filter === "surplus" && device.recordType === "surplus")
      || (filter === "missing" && device.recordType === "missing");
    return matchesText && matchesStatus;
  },

  newRequestId(prefix, id) {
    return `${prefix}_${id}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  },

  ensureRequestId(key, prefix, id) {
    let requestId = wx.getStorageSync(key);
    if (!requestId) {
      requestId = this.newRequestId(prefix, id);
      wx.setStorageSync(key, requestId);
    }
    return requestId;
  },

  clearRequestId(key) {
    wx.removeStorageSync(key);
  },

  countNormalDevices(devices = this.data.allDevices) {
    return devices.filter((d) => d.recordType !== "surplus").length;
  },

  countScannedDevices(devices = this.data.allDevices) {
    return devices.filter((d) => d.recordType !== "surplus" && d.submitted).length;
  },

  makeDevice(item, taskLocked = this.data.taskLocked) {
    const draft = item.write_locked || taskLocked ? {} : (wx.getStorageSync(`scan_draft_${item.id}`) || {});
    const deviceExtra = this.parseDeviceExtra(item.device_extra);
    const actualModel = Object.prototype.hasOwnProperty.call(draft, "actualModel")
      ? draft.actualModel
      : (item.actual_model || "");
    const device = {
      id: item.id,
      writeLocked: Boolean(item.write_locked),
      device_name: item.device_name,
      device_extra: deviceExtra,
      deviceCategory: String(deviceExtra["设备类别"] || "").trim() || String(deviceExtra["网元类型"] || "").trim(),
      expectedRackNo: Object.prototype.hasOwnProperty.call(item, "expected_rack_no")
        ? (item.expected_rack_no || "") : (deviceExtra["设备机架"] || ""),
      expected_model: item.expected_model || "",
      expectedBarcode: item.expected_barcode || "",
      recordType: item.record_type || "normal",
      fieldStatus: item.field_status || "正常",
      missingReason: item.missing_reason || "",
      assetBarcodes: item.asset_barcodes ? item.asset_barcodes.split(",").filter(Boolean) : [],
      assetNoBarcode: !!item.asset_no_barcode,
      cabinetBarcodes: item.cabinet_barcodes ? item.cabinet_barcodes.split(",").filter(Boolean) : [],
      cabinetNoBarcode: !!item.cabinet_no_barcode,
      rackNo: item.rack_no || "",
      notes: item.notes || "",
      submitted: item.match_status !== "未扫" && !Object.keys(draft).length,
      match_status: item.match_status,
      assetMatchStatus: item.asset_match_status || item.match_status,
      modelMatchStatus: item.model_match_status || item.match_status,
      ...draft,
      actualModel,
      modelConfirmed: !!draft.modelConfirmed,
      modelSuggestions: draft.modelConfirmed || actualModel.trim() === "不涉及" ? [] : suggestDeviceModels(actualModel),
    };
    this.fillDeviceDisplay(device);
    return device;
  },

  parseDeviceExtra(extra) {
    if (!extra) return {};
    if (typeof extra === "object") return extra;
    try {
      return JSON.parse(extra) || {};
    } catch (err) {
      return {};
    }
  },

  fillDeviceDisplay(device) {
    device._expectedModelText = device.expected_model || "未导入";
    device._expectedBarcodeText = device.expectedBarcode || "未导入";
    device._expectedRackText = device.expectedRackNo || "未导入";
    device._actualModelText = device.actualModel || "未填写";
    device._modelNotApplicable = device.actualModel.trim() === "不涉及";
    device._rackNoText = device.rackNo || "未填写";
    device._assetText = device.assetBarcodes.length > 0 ? device.assetBarcodes.join(", ") : "";
    device._hasAssetBarcode = device.assetBarcodes.length > 0;
    device._scannedAssetText = device._hasAssetBarcode ? device._assetText : device.assetNoBarcode ? "无条码" : "未填写";
    device._cabinetText = device.cabinetBarcodes.length > 0 ? device.cabinetBarcodes.join(", ") : "";
    device._hasCabinetBarcode = device.cabinetBarcodes.length > 0;
    device._cabinetDisplay = device._hasCabinetBarcode
      ? device._cabinetText
      : device.cabinetNoBarcode ? "无条码" : "未填写";
  },

  /** 展开/收起设备扫描表单 */
  toggleDevice(e) {
    const id = Number(e.currentTarget.dataset.id);
    const nextActiveId = this.data.activeDeviceId === id ? null : id;
    this.setData({
      activeDeviceId: nextActiveId,
    });
  },

  /** 扫码 - 资产条码 */
  async scanAsset(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
      const duplicate = this.data.allDevices.some((d) => d.assetBarcodes.includes(result));
      if (duplicate) {
        wx.showToast({ title: "该资产条码已扫描，请核实后提交", icon: "none", duration: 2500 });
      }
      const device = this.data.allDevices.find((d) => d.id === id);
      const expected = (device && device.expectedBarcode ? device.expectedBarcode : "").trim().toUpperCase();
      const scanned = result.trim().toUpperCase();
      if (expected && scanned === expected) {
        this.updateDevice(id, (d) => ({ assetBarcodes: [...d.assetBarcodes, result] }));
        wx.showToast({ title: "资产条码一致", icon: "success" });
      } else if (expected) {
        wx.showModal({
          title: "资产条码不一致",
          content: `期望条码：${expected}\n扫描条码：${scanned}\n是否保留该异常条码？`,
          confirmText: "保留异常",
          cancelText: "重新扫码",
          success: (res) => {
            if (res.confirm) {
              this.updateDevice(id, (d) => ({ assetBarcodes: [...d.assetBarcodes, result] }));
            }
          },
        });
      } else {
        this.updateDevice(id, (d) => ({ assetBarcodes: [...d.assetBarcodes, result] }));
        wx.showToast({ title: "已扫资产条码", icon: "success" });
      }
    } catch (err) {
      // 用户取消扫码，不做处理
    }
  },

  /** 扫码 - 设备所属传输综合柜条码 */
  async scanCabinet(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
      this.updateDevice(id, (d) => ({
        cabinetBarcodes: [result],
        cabinetNoBarcode: false,
      }));
    } catch (err) {
      // 用户取消扫码
    }
  },

  /** 移除一条资产条码 */
  removeAssetBarcode(e) {
    const id = Number(e.currentTarget.dataset.id);
    const index = Number(e.currentTarget.dataset.index);
    this.updateDevice(id, (d) => ({
      assetBarcodes: d.assetBarcodes.filter((_, i) => i !== index),
    }));
  },

  removeCabinetBarcode(e) {
    const id = Number(e.currentTarget.dataset.id);
    const index = Number(e.currentTarget.dataset.index);
    this.updateDevice(id, (d) => ({
      cabinetBarcodes: d.cabinetBarcodes.filter((_, i) => i !== index),
    }));
  },

  /** 切换资产"无条码" */
  toggleAssetNoBarcode(e) {
    const id = Number(e.currentTarget.dataset.id);
    this.updateDevice(id, (d) => {
      const checked = !d.assetNoBarcode;
      return {
        assetNoBarcode: checked,
        assetBarcodes: checked ? [] : d.assetBarcodes,
      };
    });
  },

  toggleCabinetNoBarcode(e) {
    const id = Number(e.currentTarget.dataset.id);
    this.updateDevice(id, (d) => {
      const checked = !d.cabinetNoBarcode;
      return {
        cabinetNoBarcode: checked,
        cabinetBarcodes: checked ? [] : d.cabinetBarcodes,
      };
    });
  },

  toggleCabinetForm() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const opening = !this.data.showCabinetForm;
    this.setData({ showCabinetForm: opening });
    if (opening) this.restoreCabinetForm();
  },

  onCabinetTypeChange(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const index = Number(e.detail.value);
    this.updateCabinetForm({
      cabinetTypeIndex: index,
      cabinetType: ["odf", "iodf"][index],
    });
  },

  onRackCandidateChange(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const index = Number(e.detail.value);
    const candidate = this.data.rackRows[index - 1];
    this.updateCabinetForm(candidate
      ? { candidateIndex: index, cabinetType: candidate.type,
          cabinetTypeIndex: candidate.type === "odf" ? 0 : 1, rackNo: candidate.name }
      : { candidateIndex: 0, cabinetType: "", cabinetTypeIndex: 0, rackNo: "" });
  },

  onCabinetFormInput(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    this.updateCabinetForm({ [e.currentTarget.dataset.field]: e.detail.value });
  },

  updateCabinetForm(patch) {
    const form = { ...this.data.cabinetForm, ...patch };
    this.setData({ cabinetForm: form });
    wx.setStorageSync(`cabinet_draft_${this.taskId}`, form);
  },

  restoreCabinetForm() {
    const draft = wx.getStorageSync(`cabinet_draft_${this.taskId}`);
    if (!draft) return;
    const cabinetType = ["odf", "iodf"].includes(draft.cabinetType)
      ? draft.cabinetType
      : "";
    this.updateCabinetForm({
      cabinetType,
      candidateIndex: Number(draft.candidateIndex) || 0,
      cabinetTypeIndex: cabinetType ? ["odf", "iodf"].indexOf(cabinetType) : 0,
      cabinetBarcode: String(draft.cabinetBarcode || ""),
      noBarcode: Boolean(draft.noBarcode),
      rackNo: String(draft.rackNo || draft.cabinetName || ""),
      notes: String(draft.notes || ""),
    });
  },

  async scanCabinetForm() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
      if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
      this.updateCabinetForm({ cabinetBarcode: result, noBarcode: false });
    } catch (err) {
      // 用户取消扫码
    }
  },

  toggleCabinetFormNoBarcode() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const noBarcode = !this.data.cabinetForm.noBarcode;
    this.updateCabinetForm({ noBarcode, cabinetBarcode: noBarcode ? "" : this.data.cabinetForm.cabinetBarcode });
  },

  resetCabinetForm() {
    wx.removeStorageSync(`cabinet_draft_${this.taskId}`);
    this.clearRequestId(`cabinet_request_${this.taskId}`);
    this.setData({ showCabinetForm: false, cabinetForm: emptyCabinetForm() });
  },

  async submitCabinet() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const form = this.data.cabinetForm;
    const cabinetBarcode = form.cabinetBarcode.trim();
    const rackNo = form.rackNo.trim();
    if (!["odf", "iodf"].includes(form.cabinetType)) {
      wx.showToast({ title: "请选择机架类型", icon: "none" });
      return;
    }
    if (!form.noBarcode && !cabinetBarcode) {
      wx.showToast({ title: "请扫码或填写机架条码", icon: "none" });
      return;
    }
    if (!rackNo) {
      wx.showToast({ title: "请填写机架编号", icon: "none" });
      return;
    }
    this.setData({ submitting: true });
    try {
      const requestKey = `cabinet_request_${this.taskId}`;
      const result = await submitCabinet({
        client_request_id: this.ensureRequestId(requestKey, "cabinet", this.taskId),
        task_id: Number(this.taskId),
        cabinet_type: form.cabinetType,
        cabinet_barcode: form.noBarcode ? "" : cabinetBarcode,
        no_barcode: form.noBarcode,
        rack_no: rackNo,
        notes: form.notes.trim(),
      });
      const cabinet = result.cabinet;
      const cabinetRecords = [cabinet, ...this.data.cabinetRecords.filter((item) => item.id !== cabinet.id)];
      const cabinetSummary = {
        ...this.data.cabinetSummary,
        total: cabinetRecords.length,
        [cabinet.cabinet_type]: cabinetRecords.filter((item) => item.cabinet_type === cabinet.cabinet_type).length,
      };
      this.setData({ cabinetRecords, cabinetSummary });
      this.resetCabinetForm();
      await this.loadDevices();
      this.showCollectionResult(result, result.message || "机架已登记");
    } catch (err) {
      await this.showCollectionConflict(err);
    } finally {
      this.setData({ submitting: false });
    }
  },


  deleteCabinet(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const cabinet = this.data.cabinetRecords.find((item) => item.id === id);
    if (!cabinet || cabinet.has_admin_correction) return;
    wx.showModal({
      title: "删除误录机架",
      content: `确认删除 ${cabinet.cabinet_type_label} ${cabinet.rack_no || cabinet.cabinet_name}（条码：${cabinet.cabinet_barcode || '无条码'}）？后台会同步移除本次登记，删除后可重新录入。`,
      confirmText: "删除",
      confirmColor: "#d94848",
      success: async (res) => {
        if (!res.confirm || this.data.taskLocked || this.data.submitting || this.data.loading) return;
        this.setData({ submitting: true, deletingCabinetId: id });
        try {
          await deleteCabinet(this.taskId, id);
          const cabinetRecords = this.data.cabinetRecords.filter((item) => item.id !== id);
          this.setData({
            cabinetRecords,
            cabinetSummary: {
              ...this.data.cabinetSummary,
              total: Math.max(0, this.data.cabinetSummary.total - 1),
              odf: cabinetRecords.filter((item) => item.cabinet_type === "odf").length,
              iodf: cabinetRecords.filter((item) => item.cabinet_type === "iodf").length,
            },
          });
          await this.loadDevices();
          wx.showToast({ title: "机架已删除", icon: "success" });
        } catch (err) {
          await this.showCollectionConflict(err);
        } finally {
          this.setData({ submitting: false, deletingCabinetId: null });
        }
      },
    });
  },

  buildModelSuggestions(query) {
    return suggestDeviceModels(query);
  },

  /** 仅盘盈设备允许修改现场录入的名称。 */
  onDeviceNameInput(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const device = this.data.allDevices.find((d) => d.id === id);
    if (!device || device.recordType !== "surplus" || device.submitted || device.writeLocked) return;
    this.updateDevice(id, () => ({ device_name: e.detail.value }));
  },

  /** 设备型号输入 */
  onModelInput(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const value = e.detail.value;
    this.updateDevice(id, () => ({
      actualModel: value,
      modelConfirmed: false,
      modelSuggestions: this.buildModelSuggestions(value),
    }));
  },

  /** 选择推荐型号 */
  chooseModelSuggestion(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const model = e.currentTarget.dataset.model || "";
    this.updateDevice(id, () => ({
      actualModel: model,
      modelConfirmed: true,
      modelSuggestions: [],
    }));
  },

  chooseSurplusModelSuggestion(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const model = e.currentTarget.dataset.model || "";
    this.updateSurplusForm({ actualModel: model, modelConfirmed: true });
  },

  toggleModelNotApplicable(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const device = this.data.allDevices.find((d) => d.id === id);
    if (!device || device.submitted || device.writeLocked) return;
    const selected = device.actualModel.trim() !== "不涉及";
    this.updateDevice(id, () => ({
      actualModel: selected ? "不涉及" : "",
      modelConfirmed: selected,
      modelSuggestions: [],
    }));
  },

  toggleSurplusModelNotApplicable() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const selected = this.data.surplusForm.actualModel.trim() !== "不涉及";
    this.updateSurplusForm({
      actualModel: selected ? "不涉及" : "",
      modelConfirmed: selected,
      modelSuggestions: [],
    });
  },

  onRackNoInput(e) {
    const id = Number(e.currentTarget.dataset.id);
    this.updateDevice(id, () => ({
      rackNo: e.detail.value,
    }));
  },

  /** 备注输入 */
  onNotesInput(e) {
    const id = Number(e.currentTarget.dataset.id);
    this.updateDevice(id, () => ({
      notes: e.detail.value,
    }));
  },

  markMissing(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const reasons = ["设备已拆除", "设备已搬迁", "暂不清楚"];
    wx.showActionSheet({
      itemList: reasons,
      success: (res) => {
        const reason = reasons[res.tapIndex];
        wx.showModal({
          title: "标记盘亏确认",
          content: `确认该设备现场未找到，并标记为盘亏？\n原因：${reason}`,
          confirmText: "确认盘亏",
          cancelText: "取消",
          success: async (modalRes) => {
            if (!modalRes.confirm || this.data.taskLocked || this.data.submitting || this.data.loading) return;
            this.setData({ submitting: true });
            try {
              const device = this.data.allDevices.find((d) => d.id === id);
              const requestKey = `missing_request_${id}`;
              const result = await submitMissing(id, {
                client_request_id: this.ensureRequestId(requestKey, "missing", id),
                reason,
                notes: device ? device.notes : "",
              });
              this.updateDevice(id, () => ({
                submitted: true,
                recordType: "missing",
                fieldStatus: "盘亏",
                missingReason: reason,
                assetBarcodes: [], assetNoBarcode: false,
                cabinetBarcodes: [], cabinetNoBarcode: false,
                actualModel: "", rackNo: "",
                match_status: result.match_status,
                assetMatchStatus: result.asset_match_status,
                modelMatchStatus: result.model_match_status,
              }));
              this.setData({
                scannedCount: this.countScannedDevices(),
              });
              wx.removeStorageSync(`scan_draft_${id}`);
              this.clearRequestId(requestKey);
              wx.showToast({ title: "已标记盘亏", icon: "success" });
            } catch (err) {
              await this.showCollectionConflict(err);
            } finally {
              this.setData({ submitting: false });
            }
          },
        });
      },
    });
  },

  openSurplusForm() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    this.setData({ showSurplusForm: !this.data.showSurplusForm });
  },

  onSurplusInput(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const field = e.currentTarget.dataset.field;
    const patch = { [field]: e.detail.value };
    if (field === "actualModel") {
      patch.modelConfirmed = false;
    }
    this.updateSurplusForm(patch);
  },

  updateSurplusForm(patch) {
    const form = { ...this.data.surplusForm, ...patch };
    form._modelNotApplicable = form.actualModel.trim() === "不涉及";
    form._assetText = form.assetBarcodes.length > 0 ? form.assetBarcodes.join(", ") : "";
    form._hasAssetBarcode = form.assetBarcodes.length > 0;
    form._cabinetText = form.cabinetBarcodes.length > 0 ? form.cabinetBarcodes.join(", ") : "";
    form._hasCabinetBarcode = form.cabinetBarcodes.length > 0;
    if (form.modelConfirmed || form._modelNotApplicable) {
      form.modelSuggestions = [];
    } else if (!Object.prototype.hasOwnProperty.call(patch, "modelSuggestions")) {
      form.modelSuggestions = this.buildModelSuggestions(form.actualModel);
    }
    this.setData({ surplusForm: form });
    wx.setStorageSync(`surplus_draft_${this.taskId}`, form);
  },

  restoreSurplusForm() {
    const draft = wx.getStorageSync(`surplus_draft_${this.taskId}`);
    if (draft) {
      this.updateSurplusForm(draft);
    }
  },

  async scanSurplusAsset() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
      if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
      const duplicate = this.data.allDevices.some((d) => d.assetBarcodes.includes(result));
      if (duplicate) {
        wx.showToast({ title: "该资产条码已扫描，请核实后提交", icon: "none", duration: 2500 });
      }
      this.updateSurplusForm({ assetBarcodes: [...this.data.surplusForm.assetBarcodes, result] });
    } catch (err) {
      // 用户取消扫码
    }
  },

  async scanSurplusCabinet() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
      if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
      this.updateSurplusForm({ cabinetBarcodes: [result], cabinetNoBarcode: false });
    } catch (err) {
      // 用户取消扫码
    }
  },

  removeSurplusAssetBarcode(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const index = Number(e.currentTarget.dataset.index);
    this.updateSurplusForm({
      assetBarcodes: this.data.surplusForm.assetBarcodes.filter((_, i) => i !== index),
    });
  },

  removeSurplusCabinetBarcode(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const index = Number(e.currentTarget.dataset.index);
    this.updateSurplusForm({
      cabinetBarcodes: this.data.surplusForm.cabinetBarcodes.filter((_, i) => i !== index),
    });
  },

  toggleSurplusAssetNoBarcode() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const checked = !this.data.surplusForm.assetNoBarcode;
    this.updateSurplusForm({
      assetNoBarcode: checked,
      assetBarcodes: checked ? [] : this.data.surplusForm.assetBarcodes,
    });
  },

  toggleSurplusCabinetNoBarcode() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const checked = !this.data.surplusForm.cabinetNoBarcode;
    this.updateSurplusForm({
      cabinetNoBarcode: checked,
      cabinetBarcodes: checked ? [] : this.data.surplusForm.cabinetBarcodes,
    });
  },

  resetSurplusForm() {
    wx.removeStorageSync(`surplus_draft_${this.taskId}`);
    this.clearRequestId(`surplus_request_${this.taskId}`);
    this.setData({
      showSurplusForm: false,
      surplusForm: emptySurplusForm(),
    });
  },

  async submitSurplusDevice() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const form = this.data.surplusForm;
    if (!form.deviceName.trim()) {
      wx.showToast({ title: "请填写盘盈设备名称", icon: "none" });
      return;
    }
    if (!form.actualModel.trim()) {
      wx.showToast({ title: "请填写设备型号", icon: "none" });
      return;
    }
    if (!form.assetNoBarcode && form.assetBarcodes.length === 0) {
      wx.showToast({ title: "请扫码资产条码或勾选无条码", icon: "none" });
      return;
    }
    if (!form.cabinetNoBarcode && form.cabinetBarcodes.length === 0) {
      wx.showToast({ title: "请扫码机柜条码或勾选无条码", icon: "none" });
      return;
    }
    if (!form.rackNo.trim()) {
      wx.showToast({ title: "请填写设备所属机架号", icon: "none" });
      return;
    }
    this.setData({ submitting: true });
    try {
      const user = app.globalData.userInfo || {};
      const requestKey = `surplus_request_${this.taskId}`;
      const result = await submitSurplus({
        client_request_id: this.ensureRequestId(requestKey, "surplus", this.taskId),
        task_id: Number(this.taskId),
        device_name: form.deviceName,
        actual_model: form.actualModel.trim(),
        asset_barcodes: form.assetBarcodes.join(","),
        asset_no_barcode: form.assetNoBarcode,
        cabinet_barcodes: form.cabinetBarcodes.join(","),
        cabinet_no_barcode: form.cabinetNoBarcode,
        rack_no: form.rackNo,
        notes: form.notes,
        created_by: user.user_id || null,
      });
      const newDevice = this.makeDevice(result.record);
      const allDevices = [newDevice, ...this.data.allDevices];
      this.setData({
        allDevices,
        totalCount: this.countNormalDevices(allDevices),
        scannedCount: this.countScannedDevices(allDevices),
      });
      this.applyDeviceFilters();
      this.resetSurplusForm();
      this.showCollectionResult(result, "盘盈已登记");
    } catch (err) {
      await this.showCollectionConflict(err);
    } finally {
      this.setData({ submitting: false });
    }
  },

  /** 进行中的任务可修正本次录入，保存时覆盖原记录。 */
  reopenDevice(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const device = this.data.allDevices.find((d) => d.id === id);
    if (!device || !device.submitted || device.writeLocked) return;
    wx.showModal({
      title: "修正本次录入",
      content: "将打开这条记录的表单，保存后覆盖本次录入的数据。是否继续？",
      confirmText: "修正",
      cancelText: "取消",
      success: (res) => {
        if (res.confirm && !this.data.taskLocked && !this.data.submitting && !this.data.loading) {
          this.clearRequestId(`scan_request_${id}`);
          this.updateDevice(id, () => ({ submitted: false }));
          this.setData({ scannedCount: this.countScannedDevices() });
        }
      },
    });
  },

  deleteSurplus(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const device = this.data.allDevices.find((d) => d.id === id);
    if (!device || device.recordType !== "surplus" || device.writeLocked) return;
    wx.showModal({
      title: "删除盘盈",
      content: `确认删除盘盈设备“${device.device_name}”？删除后将不再计入本次盘点。`,
      confirmText: "删除",
      confirmColor: "#d94848",
      cancelText: "取消",
      success: async (res) => {
        if (!res.confirm || this.data.taskLocked || this.data.submitting || this.data.loading) return;
        const current = this.data.allDevices.find((d) => d.id === id);
        if (!current || current.recordType !== "surplus" || current.writeLocked) return;
        this.setData({ submitting: true });
        try {
          const result = await deleteSurplus(this.taskId, id);
          const allDevices = this.data.allDevices.filter((d) => d.id !== id);
          this.setData({
            allDevices,
            activeDeviceId: this.data.activeDeviceId === id ? null : this.data.activeDeviceId,
            totalCount: this.countNormalDevices(allDevices),
            scannedCount: this.countScannedDevices(allDevices),
          });
          this.applyDeviceFilters();
          wx.removeStorageSync(`scan_draft_${id}`);
          this.clearRequestId(`scan_request_${id}`);
          this.showCollectionResult(result, "盘盈已删除");
        } catch (err) {
          await this.showCollectionConflict(err);
        } finally {
          this.setData({ submitting: false });
        }
      },
    });
  },

  /** 提交盘点 */
  async submitScan(e) {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    const id = Number(e.currentTarget.dataset.id);
    const device = this.data.devices.find((d) => d.id === id);
    if (!device || device.submitted || device.writeLocked) return;
    // 校验
    if (device.recordType === "surplus" && !device.device_name.trim()) {
      wx.showToast({ title: "请填写盘盈设备名称", icon: "none" });
      return;
    }
    if (!device.actualModel.trim()) {
      wx.showToast({ title: "请填写设备型号", icon: "none" });
      return;
    }
    if (!device.assetNoBarcode && device.assetBarcodes.length === 0) {
      wx.showToast({ title: "请扫码资产条码或勾选无条码", icon: "none" });
      return;
    }
    if (!device.rackNo.trim()) {
      wx.showToast({ title: "请填写设备所属机架号", icon: "none" });
      return;
    }
    if (!device.cabinetNoBarcode && device.cabinetBarcodes.length === 0) {
      wx.showToast({ title: "请扫码机柜资产条码或勾选无条码", icon: "none" });
      return;
    }
    this.setData({ submitting: true });
    try {
      const requestKey = `scan_request_${id}`;
      const result = await submitScan({
        client_request_id: this.ensureRequestId(requestKey, "scan", id),
        record_id: id,
        ...(device.recordType === "surplus" ? { device_name: device.device_name.trim() } : {}),
        asset_barcodes: device.assetBarcodes.join(","),
        asset_no_barcode: device.assetNoBarcode,
        cabinet_barcodes: device.cabinetBarcodes.join(","),
        cabinet_no_barcode: device.cabinetNoBarcode,
        rack_no: device.rackNo,
        actual_model: device.actualModel.trim(),
        notes: device.notes,
      });

      // 更新状态
      this.updateDevice(id, () => ({
        device_name: device.device_name.trim(),
        submitted: true,
        recordType: result.record_type || "normal",
        fieldStatus: result.record_type === "surplus" ? "盘盈" : "正常",
        missingReason: "",
        match_status: result.match_status,
        assetMatchStatus: result.asset_match_status,
        modelMatchStatus: result.model_match_status,
      }));

      // 更新统计
      this.setData({
        scannedCount: this.countScannedDevices(),
      });
      wx.removeStorageSync(`scan_draft_${id}`);
      this.clearRequestId(requestKey);

      this.showCollectionResult(result, device.recordType === "surplus" ? "盘盈已更新" : "盘点完成");
    } catch (err) {
      await this.showCollectionConflict(err);
    } finally {
      this.setData({ submitting: false });
    }
  },

  showCollectionResult(result, title) {
    this.clearCollectionNotice();
    const warnings = result.warnings || [];
    if (warnings.length) {
      wx.showModal({
        title: "已保存，请核实采集冲突",
        content: warnings.join("\n\n"),
        showCancel: false,
        confirmText: "我已知晓",
      });
      return;
    }
    const hasDifference = title === "盘点完成" && result.match_status && result.match_status !== "一致";
    this.setData({
      collectionNotice: {
        title,
        detail: title === "盘盈已删除" ? "误录记录已移除" : hasDifference ? "数据已保存，存在差异待核对" : "数据已保存",
        tone: hasDifference ? "warning" : "success",
        icon: hasDifference ? "warn" : "success_no_circle",
      },
    });
    this.collectionNoticeTimer = setTimeout(() => this.clearCollectionNotice(), 2000);
  },

  clearCollectionNotice() {
    clearTimeout(this.collectionNoticeTimer);
    this.collectionNoticeTimer = null;
    this.setData({ collectionNotice: null });
  },

  async showCollectionConflict(err) {
    this.clearCollectionNotice();
    // 其他请求错误由 API wrapper 提示；身份冲突内容较长，用弹窗完整展示。
    if (err.statusCode === 409) {
      await this.loadDevices();
      wx.showModal({
        title: "提交未成功",
        content: err.message,
        showCancel: false,
        confirmText: "返回核实",
      });
    }
  },

  confirmTaskComplete() {
    if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
    if (!this.data.totalCount || this.countScannedDevices() !== this.data.totalCount) {
      wx.showToast({ title: "请先处理全部清单设备", icon: "none" });
      return;
    }
    wx.showModal({
      title: "确认完成本次盘点",
      content: "请确认机架是否已完成盘点？请核实本机房 ODF／IODF 机架已逐一登记（如无机架，请确认现场确实没有），清单外设备也已补录。选择是后提交本次盘点，完成后任务锁定。",
      confirmText: "已完成",
      cancelText: "继续盘点",
      success: async (res) => {
        if (!res.confirm) {
          this.setData({ activeTab: "cabinets" });
          return;
        }
        if (this.data.taskLocked || this.data.submitting || this.data.loading) return;
        this.setData({ submitting: true });
        try {
          await completeInventoryTask(this.taskId);
          this.setData({ taskLocked: true, showCabinetForm: false, showSurplusForm: false });
          await this.loadDevices();
          wx.showToast({ title: "任务已完成并锁定", icon: "success" });
        } catch (err) {
          await this.showCollectionConflict(err);
        } finally {
          this.setData({ submitting: false });
        }
      },
    });
  },

  /** 工具：更新指定设备的状态 */
  updateDevice(id, updater) {
    if (this.data.taskLocked || this.data.loading) return;
    let changedDevice = null;
    const allDevices = this.data.allDevices.map((d) => {
      if (d.id === id && !d.writeLocked) {
        const patch = updater(d) || {};
        const updated = { ...d, ...patch };
        this.fillDeviceDisplay(updated);
        if (updated.modelConfirmed || updated._modelNotApplicable) {
          updated.modelSuggestions = [];
        } else if (!Object.prototype.hasOwnProperty.call(patch, "modelSuggestions")) {
          updated.modelSuggestions = this.buildModelSuggestions(updated.actualModel);
        }
        if (!updated.submitted) {
          wx.setStorageSync(`scan_draft_${id}`, {
            ...(updated.recordType === "surplus" ? { device_name: updated.device_name } : {}),
            assetBarcodes: updated.assetBarcodes,
            assetNoBarcode: updated.assetNoBarcode,
            cabinetBarcodes: updated.cabinetBarcodes,
            cabinetNoBarcode: updated.cabinetNoBarcode,
            actualModel: updated.actualModel,
            modelConfirmed: updated.modelConfirmed,
            rackNo: updated.rackNo,
            notes: updated.notes,
          });
        }
        changedDevice = updated;
        return updated;
      }
      return d;
    });
    if (!changedDevice) return;

    this.setData({ allDevices });
    this.applyDeviceFilters();
  },
});
