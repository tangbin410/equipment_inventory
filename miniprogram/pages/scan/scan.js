/* 设备盘点页 - 核心逻辑 */

const app = getApp();
const { getTaskItems, submitScan, submitMissing, submitSurplus } = require("../../utils/api");
const { suggestDeviceModels } = require("../../utils/device-models");

function emptySurplusForm() {
  return {
    deviceName: "",
    actualModel: "",
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
    deviceFilter: "all",
    filterOptions: ["全部设备", "未盘点", "异常设备", "盘盈", "盘亏"],
    activeDeviceId: null, // 当前展开的设备ID
    showSurplusForm: false,
    surplusForm: emptySurplusForm(),
    loading: true,
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
    this.restoreSurplusForm();
    this.loadDevices();
  },

  /** 每次显示页面时刷新（从提交页返回时更新） */
  onShow() {
    // wx.scanCode 返回时也会触发 onShow。
    // 如果这里重新 loadDevices，会导致设备列表整页重绘，滚动位置回到上方。
    if (this.skipNextOnShowReload) {
      this.skipNextOnShowReload = false;
      return;
    }
    if (this.taskId && !this.data.loading) {
      this.loadDevices();
    }
  },

  /** 加载设备清单 */
  async loadDevices() {
    this.setData({ loading: true });
    try {
      const result = await getTaskItems(this.taskId);
      const task = result.task;
      const items = result.items;

      // 将后端数据转为前端状态（每个设备加上扫描表单字段）
      const devices = items.map((item) => this.makeDevice(item));
      this.setData({
        taskName: task.task_name,
        allDevices: devices,
        devices,
        totalCount: this.countNormalDevices(devices),
        scannedCount: this.countScannedDevices(devices),
        loading: false,
      });
    } catch (err) {
      this.setData({ loading: false });
    }
  },

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value });
    this.applyDeviceFilters();
  },

  onDeviceFilterChange(e) {
    this.setData({ deviceFilter: ["all", "pending", "mismatch", "surplus", "missing"][Number(e.detail.value)] });
    this.applyDeviceFilters();
  },

  applyDeviceFilters() {
    const keyword = this.data.keyword.trim().toLowerCase();
    const filter = this.data.deviceFilter;
    const devices = this.data.allDevices.filter((device) => this.deviceMatchesFilter(device, keyword, filter));
    this.setData({ devices });
  },

  deviceMatchesFilter(device, keyword = this.data.keyword.trim().toLowerCase(), filter = this.data.deviceFilter) {
    const searchableText = [
      device.device_name,
      device.expected_model,
      device.actualModel,
      device.expectedBarcode,
      device.assetBarcodes.join(","),
      device.cabinetBarcodes.join(","),
      device.rackNo,
    ].join(" ").toLowerCase();
    const matchesText = !keyword || searchableText.includes(keyword);
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

  makeDevice(item) {
    const draft = wx.getStorageSync(`scan_draft_${item.id}`) || {};
    const deviceExtra = this.parseDeviceExtra(item.device_extra);
    const actualModel = Object.prototype.hasOwnProperty.call(draft, "actualModel")
      ? draft.actualModel
      : (item.actual_model || "");
    const device = {
      id: item.id,
      device_name: item.device_name,
      device_extra: deviceExtra,
      expectedRackNo: deviceExtra["设备机架"] || "",
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
      submitted: item.match_status !== "未扫",
      match_status: item.match_status,
      assetMatchStatus: item.asset_match_status || item.match_status,
      modelMatchStatus: item.model_match_status || item.match_status,
      ...draft,
      actualModel,
      modelConfirmed: !!draft.modelConfirmed,
      modelSuggestions: draft.modelConfirmed ? [] : suggestDeviceModels(actualModel),
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
    device._rackNoText = device.rackNo || "未填写";
    device._assetText = device.assetBarcodes.length > 0 ? device.assetBarcodes.join(", ") : "";
    device._hasAssetBarcode = device.assetBarcodes.length > 0;
    device._scannedAssetText = device._hasAssetBarcode ? device._assetText : device.assetNoBarcode ? "无条码" : "未填写";
    device._cabinetText = device.cabinetBarcodes.length > 0 ? device.cabinetBarcodes.join(", ") : "";
    device._hasCabinetBarcode = device.cabinetBarcodes.length > 0;
  },

  /** 展开/收起设备扫描表单 */
  toggleDevice(e) {
    const id = Number(e.currentTarget.dataset.id);
    this.setData({
      activeDeviceId: this.data.activeDeviceId === id ? null : id,
    });
  },

  /** 扫码 - 资产条码 */
  async scanAsset(e) {
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

  /** 扫码 - 机柜条码 */
  async scanCabinet(e) {
    const id = Number(e.currentTarget.dataset.id);
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
      this.updateDevice(id, (d) => ({
        cabinetBarcodes: [...d.cabinetBarcodes, result],
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

  /** 移除一条机柜条码 */
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

  /** 切换机柜"无条码" */
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

  buildModelSuggestions(query) {
    return suggestDeviceModels(query);
  },

  /** 设备型号输入 */
  onModelInput(e) {
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
    const id = Number(e.currentTarget.dataset.id);
    const model = e.currentTarget.dataset.model || "";
    this.updateDevice(id, () => ({
      actualModel: model,
      modelConfirmed: false,
      modelSuggestions: this.buildModelSuggestions(model),
    }));
  },

  chooseSurplusModelSuggestion(e) {
    const model = e.currentTarget.dataset.model || "";
    this.updateSurplusForm({ actualModel: model, modelConfirmed: false });
  },

  confirmModelInput(e) {
    const id = Number(e.currentTarget.dataset.id);
    const device = this.data.allDevices.find((d) => d.id === id);
    if (!device || !device.actualModel.trim()) {
      wx.showToast({ title: "请先填写设备型号", icon: "none" });
      return;
    }
    this.updateDevice(id, () => ({ modelConfirmed: true, modelSuggestions: [] }));
    wx.showToast({ title: "型号已确认", icon: "success" });
  },

  confirmSurplusModelInput() {
    const model = this.data.surplusForm.actualModel || "";
    if (!model.trim()) {
      wx.showToast({ title: "请先填写设备型号", icon: "none" });
      return;
    }
    this.updateSurplusForm({ modelConfirmed: true, modelSuggestions: [] });
    wx.showToast({ title: "型号已确认", icon: "success" });
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
            if (!modalRes.confirm) return;
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
              // API wrapper 已显示错误
            }
          },
        });
      },
    });
  },

  openSurplusForm() {
    this.setData({ showSurplusForm: !this.data.showSurplusForm });
  },

  onSurplusInput(e) {
    const field = e.currentTarget.dataset.field;
    const patch = { [field]: e.detail.value };
    if (field === "actualModel") {
      patch.modelConfirmed = false;
    }
    this.updateSurplusForm(patch);
  },

  updateSurplusForm(patch) {
    const form = { ...this.data.surplusForm, ...patch };
    form._assetText = form.assetBarcodes.length > 0 ? form.assetBarcodes.join(", ") : "";
    form._hasAssetBarcode = form.assetBarcodes.length > 0;
    form._cabinetText = form.cabinetBarcodes.length > 0 ? form.cabinetBarcodes.join(", ") : "";
    form._hasCabinetBarcode = form.cabinetBarcodes.length > 0;
    if (form.modelConfirmed) {
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
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
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
    try {
      this.skipNextOnShowReload = true;
      const { result } = await wx.scanCode({});
      this.updateSurplusForm({ cabinetBarcodes: [...this.data.surplusForm.cabinetBarcodes, result] });
    } catch (err) {
      // 用户取消扫码
    }
  },

  removeSurplusAssetBarcode(e) {
    const index = Number(e.currentTarget.dataset.index);
    this.updateSurplusForm({
      assetBarcodes: this.data.surplusForm.assetBarcodes.filter((_, i) => i !== index),
    });
  },

  removeSurplusCabinetBarcode(e) {
    const index = Number(e.currentTarget.dataset.index);
    this.updateSurplusForm({
      cabinetBarcodes: this.data.surplusForm.cabinetBarcodes.filter((_, i) => i !== index),
    });
  },

  toggleSurplusAssetNoBarcode() {
    const checked = !this.data.surplusForm.assetNoBarcode;
    this.updateSurplusForm({
      assetNoBarcode: checked,
      assetBarcodes: checked ? [] : this.data.surplusForm.assetBarcodes,
    });
  },

  toggleSurplusCabinetNoBarcode() {
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
    const form = this.data.surplusForm;
    if (!form.deviceName.trim()) {
      wx.showToast({ title: "请填写盘盈设备名称", icon: "none" });
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
      const devices = [newDevice, ...this.data.devices].filter((d) => this.deviceMatchesFilter(d));
      this.setData({
        allDevices,
        devices,
        totalCount: this.countNormalDevices(allDevices),
        scannedCount: this.countScannedDevices(allDevices),
      });
      this.resetSurplusForm();
      wx.showToast({ title: "盘盈已登记", icon: "success" });
    } catch (err) {
      // API wrapper 已显示错误
    }
  },

  /** 已提交记录允许现场复核后重新提交。 */
  reopenDevice(e) {
    const id = Number(e.currentTarget.dataset.id);
    wx.showModal({
      title: "重新盘点确认",
      content: "重新盘点会重新打开该设备表单，提交后将覆盖本次盘点结果。是否继续？",
      confirmText: "继续",
      cancelText: "取消",
      success: (res) => {
        if (res.confirm) {
          this.updateDevice(id, () => ({ submitted: false }));
        }
      },
    });
  },

  /** 提交盘点 */
  async submitScan(e) {
    const id = Number(e.currentTarget.dataset.id);
    const device = this.data.devices.find((d) => d.id === id);
    if (!device) return;

    // 校验
    if (!device.assetNoBarcode && device.assetBarcodes.length === 0) {
      wx.showToast({ title: "请扫码资产条码或勾选无条码", icon: "none" });
      return;
    }
    if (!device.cabinetNoBarcode && device.cabinetBarcodes.length === 0) {
      wx.showToast({ title: "请扫码机柜条码或勾选无条码", icon: "none" });
      return;
    }
    if (!device.actualModel.trim()) {
      wx.showToast({ title: "请填写设备型号", icon: "none" });
      return;
    }
    if (!device.rackNo.trim()) {
      wx.showToast({ title: "请填写设备所属机架号", icon: "none" });
      return;
    }
    try {
      const requestKey = `scan_request_${id}`;
      const result = await submitScan({
        client_request_id: this.ensureRequestId(requestKey, "scan", id),
        record_id: id,
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
        submitted: true,
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

      wx.showToast({
        title: result.match_status === "一致" ? "盘点完成 ✅" : "盘点完成 ⚠️",
        icon: "success",
        duration: 2000,
      });
    } catch (err) {
      // API wrapper 已显示错误
    }
  },

  /** 工具：更新指定设备的状态 */
  updateDevice(id, updater) {
    let changedDevice = null;
    const allDevices = this.data.allDevices.map((d) => {
      if (d.id === id) {
        const patch = updater(d) || {};
        const updated = { ...d, ...patch };
        this.fillDeviceDisplay(updated);
        if (updated.modelConfirmed) {
          updated.modelSuggestions = [];
        } else if (!Object.prototype.hasOwnProperty.call(patch, "modelSuggestions")) {
          updated.modelSuggestions = this.buildModelSuggestions(updated.actualModel);
        }
        if (!updated.submitted) {
          wx.setStorageSync(`scan_draft_${id}`, {
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

    const visibleDevices = this.data.devices
      .map((d) => (d.id === id ? changedDevice : d))
      .filter((d) => this.deviceMatchesFilter(d));
    const isVisible = visibleDevices.some((d) => d.id === id);
    if (!isVisible && this.deviceMatchesFilter(changedDevice)) {
      visibleDevices.push(changedDevice);
    }

    this.setData({
      allDevices,
      devices: visibleDevices,
    });
  },
});
