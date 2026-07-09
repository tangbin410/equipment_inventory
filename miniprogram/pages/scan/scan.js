/* 设备盘点页 - 核心逻辑 */

const app = getApp();
const { getTaskItems, submitScan, uploadEvidencePhoto } = require("../../utils/api");

Page({
  data: {
    taskName: "",
    devices: [],          // 当前筛选后的设备列表
    allDevices: [],       // 完整设备列表
    keyword: "",
    deviceFilter: "all",
    filterOptions: ["全部设备", "未盘点", "条码异常"],
    activeDeviceId: null, // 当前展开的设备ID
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
    this.loadDevices();
  },

  /** 每次显示页面时刷新（从提交页返回时更新） */
  onShow() {
    // wx.scanCode / wx.chooseMedia 返回时也会触发 onShow。
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
      const devices = items.map((item) => {
        const modelOptions = [item.expected_model];
        if (item.expected_model) {
          modelOptions.push("其他...");
        }
        const draft = wx.getStorageSync(`scan_draft_${item.id}`) || {};
        return {
          id: item.id,
          device_name: item.device_name,
          device_extra: item.device_extra || {},
          expected_model: item.expected_model,
          expectedBarcode: item.expected_barcode || "",
          // 扫描状态
          assetBarcodes: item.asset_barcodes
            ? item.asset_barcodes.split(",").filter(Boolean)
            : [],
          assetNoBarcode: !!item.asset_no_barcode,
          cabinetBarcodes: item.cabinet_barcodes
            ? item.cabinet_barcodes.split(",").filter(Boolean)
            : [],
          cabinetNoBarcode: !!item.cabinet_no_barcode,
          // 型号
          modelOptions,
          showOtherModel: false,
          selectedModelIndex: -1,
          actualModel: item.actual_model || "",
          rackNo: item.rack_no || "",
          // 备注
          notes: item.notes || "",
          // 提交状态
          submitted: item.match_status !== "未扫",
          match_status: item.match_status,
          assetMatchStatus: item.asset_match_status || item.match_status,
          modelMatchStatus: item.model_match_status || item.match_status,
          evidencePhoto: "",
          ...draft,
        };
      });

      // 预计算 WXML 不能处理的表达式
      devices.forEach((d) => {
        d._matchIcon = d.match_status === "一致" ? "success" : d.match_status === "不一致" ? "error" : "wait";
        d._tagCls = d.match_status === "一致" ? "tag-success" : d.match_status === "不一致" ? "tag-error" : "tag-wait";
        d._matchText = d.match_status === "一致" ? "✅ 一致" : d.match_status === "不一致" ? "❌ 不一致" : "⏳ " + d.match_status;
        d._assetMatchText = "资产条码比对: " + (d.assetMatchStatus || "未扫");
        d._modelMatchText = "型号比对: " + (d.modelMatchStatus || "未扫");
        d._expectedBarcodeText = d.expectedBarcode || "未导入";
        d._assetText = d.assetBarcodes.length > 0 ? d.assetBarcodes.join(", ") : "";
        d._hasAssetBarcode = d.assetBarcodes.length > 0;
        d._cabinetText = d.cabinetBarcodes.length > 0 ? d.cabinetBarcodes.join(", ") : "";
        d._hasCabinetBarcode = d.cabinetBarcodes.length > 0;
      });
      const scanned = devices.filter((d) => d.submitted).length;
      this.setData({
        taskName: task.task_name,
        allDevices: devices,
        devices,
        totalCount: devices.length,
        scannedCount: scanned,
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
    this.setData({ deviceFilter: ["all", "pending", "mismatch"][Number(e.detail.value)] });
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
      device.expectedBarcode,
      device.assetBarcodes.join(","),
      device.cabinetBarcodes.join(","),
      device.rackNo,
    ].join(" ").toLowerCase();
    const matchesText = !keyword || searchableText.includes(keyword);
    const matchesStatus = filter === "all" || (filter === "pending" && !device.submitted) || (filter === "mismatch" && device.assetMatchStatus !== "一致");
    return matchesText && matchesStatus;
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
        wx.showToast({ title: "该资产条码已扫描", icon: "none" });
        return;
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
    const device = this.data.allDevices.find((d) => d.id === id);
    if (device && !device.assetNoBarcode) {
      wx.showToast({ title: "已标记无资产条码，请拍现场凭证", icon: "none" });
    }
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
    const device = this.data.allDevices.find((d) => d.id === id);
    if (device && !device.cabinetNoBarcode) {
      wx.showToast({ title: "已标记无机柜条码，请拍现场凭证", icon: "none" });
    }
  },

  /** 设备型号下拉选择 */
  onModelChange(e) {
    const id = Number(e.currentTarget.dataset.id);
    const index = Number(e.detail.value);
    this.updateDevice(id, (d) => {
      const showOther = d.modelOptions[index] === "其他...";
      return {
        selectedModelIndex: index,
        showOtherModel: showOther,
        actualModel: showOther ? "" : d.modelOptions[index],
      };
    });
  },

  /** 手动输入其他型号 */
  onOtherModelInput(e) {
    const id = Number(e.currentTarget.dataset.id);
    this.updateDevice(id, () => ({
      actualModel: e.detail.value,
    }));
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

  async chooseEvidencePhoto(e) {
    const id = Number(e.currentTarget.dataset.id);
    try {
      this.skipNextOnShowReload = true;
      const result = await wx.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["camera", "album"] });
      this.updateDevice(id, () => ({ evidencePhoto: result.tempFiles[0].tempFilePath }));
      wx.showToast({ title: "现场凭证已保存", icon: "success" });
    } catch (err) {
      // 用户取消选择。
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
    if (!device.actualModel) {
      wx.showToast({ title: "请选择设备型号", icon: "none" });
      return;
    }
    if (!device.rackNo.trim()) {
      wx.showToast({ title: "请填写设备所属机架号", icon: "none" });
      return;
    }
    if ((device.assetNoBarcode || device.cabinetNoBarcode) && !device.evidencePhoto) {
      wx.showToast({ title: "无条码设备请拍摄现场照片", icon: "none" });
      return;
    }

    try {
      if (device.evidencePhoto) {
        await uploadEvidencePhoto(id, device.evidencePhoto);
      }
      const result = await submitScan({
        record_id: id,
        asset_barcodes: device.assetBarcodes.join(","),
        asset_no_barcode: device.assetNoBarcode,
        cabinet_barcodes: device.cabinetBarcodes.join(","),
        cabinet_no_barcode: device.cabinetNoBarcode,
        rack_no: device.rackNo,
        actual_model: device.actualModel,
        notes: device.notes,
      });

      // 更新状态
      this.updateDevice(id, () => ({
        submitted: true,
        match_status: result.match_status,
        assetMatchStatus: result.asset_match_status,
        modelMatchStatus: result.model_match_status,
        _matchIcon: result.match_status === "一致" ? "success" : "error",
        _tagCls: result.match_status === "一致" ? "tag-success" : "tag-error",
        _matchText: result.match_status === "一致" ? "✅ 一致" : "❌ 不一致",
        _assetMatchText: "资产条码比对: " + result.asset_match_status,
        _modelMatchText: "型号比对: " + result.model_match_status,
      }));

      // 更新统计
      this.setData({
        scannedCount: this.data.allDevices.filter((d) => d.submitted).length,
      });
      wx.removeStorageSync(`scan_draft_${id}`);

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
        const updated = { ...d, ...updater(d) };
        // WXML 不能方便地在模板里计算数组长度和 join 文本。
        // 每次扫码、删除条码、勾选无条码后，都要同步刷新这些页面显示字段。
        updated._assetText = updated.assetBarcodes.length > 0 ? updated.assetBarcodes.join(", ") : "";
        updated._hasAssetBarcode = updated.assetBarcodes.length > 0;
        updated._cabinetText = updated.cabinetBarcodes.length > 0 ? updated.cabinetBarcodes.join(", ") : "";
        updated._hasCabinetBarcode = updated.cabinetBarcodes.length > 0;
        if (!updated.submitted) {
          wx.setStorageSync(`scan_draft_${id}`, {
            assetBarcodes: updated.assetBarcodes,
            assetNoBarcode: updated.assetNoBarcode,
            cabinetBarcodes: updated.cabinetBarcodes,
            cabinetNoBarcode: updated.cabinetNoBarcode,
            actualModel: updated.actualModel,
            rackNo: updated.rackNo,
            notes: updated.notes,
            evidencePhoto: updated.evidencePhoto,
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
