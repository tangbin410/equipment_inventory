const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function fakeTimers() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  return {
    setTimeout(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    advance(milliseconds) {
      now += milliseconds;
      for (const [id, timer] of Array.from(timers)) {
        if (timer.at <= now && timers.delete(id)) timer.callback();
      }
    },
    pendingCount() { return timers.size; },
  };
}

async function verifyCollectionNotice() {
  let definition;
  const clock = fakeTimers();
  const modals = [];
  const toasts = [];
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/scan/scan.js", "utf8"), {
    ...clock,
    getApp: () => ({}),
    Page: (value) => { definition = value; },
    require: () => ({}),
    wx: { showModal: (options) => modals.push(options), showToast: (options) => toasts.push(options) },
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(patch) { Object.assign(this.data, patch); } };
  assert.equal(page.data.collectionNotice, null);
  page.showCollectionResult({ match_status: "一致" }, "盘点完成");
  assert.deepEqual({ ...page.data.collectionNotice }, {
    title: "盘点完成", detail: "数据已保存", tone: "success", icon: "success_no_circle",
  });
  assert.equal(clock.pendingCount(), 1);
  assert.equal(modals.length, 0);
  assert.equal(toasts.length, 0, "普通完成使用页面轻提示，不能再弹原生灰色toast");
  clock.advance(1999);
  assert.ok(page.data.collectionNotice);
  clock.advance(1);
  assert.equal(page.data.collectionNotice, null, "提示2秒后自动消失");
  assert.equal(clock.pendingCount(), 0);

  page.showCollectionResult({ match_status: "一致" }, "盘点完成");
  clock.advance(1000);
  page.showCollectionResult({ match_status: "不一致" }, "盘点完成");
  assert.deepEqual({ ...page.data.collectionNotice }, {
    title: "盘点完成", detail: "数据已保存，存在差异待核对", tone: "warning", icon: "warn",
  });
  assert.equal(clock.pendingCount(), 1, "连续成功提交应替换旧提示并取消旧定时器");
  clock.advance(1000);
  assert.equal(page.data.collectionNotice.tone, "warning", "旧提示原到期时间不能提前关闭新提示");
  clock.advance(1000);
  assert.equal(page.data.collectionNotice, null);

  for (const title of ["机架已登记", "盘盈已登记"]) {
    page.showCollectionResult({ match_status: "盘盈" }, title);
    assert.equal(page.data.collectionNotice.title, title);
    assert.equal(page.data.collectionNotice.tone, "success", "正常登记不能因非设备比对状态误显示差异");
  }
  const warnings = ["传输综合柜C1对应两个柜位：R1、R2", "条码A1同时出现在设备甲和设备乙"];
  page.showCollectionResult({ warnings }, "盘点完成");
  assert.equal(page.data.collectionNotice, null, "冲突弹窗出现前清除普通成功提示");
  assert.equal(clock.pendingCount(), 0);
  assert.equal(modals.at(-1).content, warnings.join("\n\n"), "冲突内容应完整展示");
  assert.equal(modals.at(-1).showCancel, false);
  clock.advance(2000);
  assert.equal(page.data.collectionNotice, null);

  page.showCollectionResult({}, "机架已登记");
  await page.showCollectionConflict(new Error("网络请求失败"));
  assert.equal(page.data.collectionNotice, null, "提交失败时清除上次成功提示，避免误认本次已保存");
  assert.equal(clock.pendingCount(), 0);
  page.showCollectionResult({}, "盘盈已登记");
  page.loadDevices = async () => {};
  await page.showCollectionConflict(Object.assign(new Error("机架条码冲突"), { statusCode: 409 }));
  assert.equal(page.data.collectionNotice, null);
  assert.equal(modals.at(-1).content, "机架条码冲突");
  assert.equal(clock.pendingCount(), 0);

  for (const lifecycle of ["onHide", "onUnload"]) {
    page.showCollectionResult({}, "机架已登记");
    page[lifecycle]();
    assert.equal(page.data.collectionNotice, null, `${lifecycle}清除提示`);
    assert.equal(clock.pendingCount(), 0, `${lifecycle}清除定时器`);
    clock.advance(2000);
    assert.equal(page.data.collectionNotice, null);
  }
}

async function verifyDeviceOrdering() {
  let definition;
  let status = "doing";
  let modalPromise;
  const storage = new Map();
  const rows = [
    { id: 1, device_name: "设备甲-已盘", match_status: "一致" },
    { id: 2, device_name: "设备甲-待盘", match_status: "未扫" },
    { id: 3, device_name: "设备乙-盘亏", match_status: "盘亏", record_type: "missing" },
    { id: 4, device_name: "设备甲-新增", match_status: "未扫" },
    { id: 5, device_name: "设备甲-盘盈", match_status: "盘盈", record_type: "surplus" },
    { id: 6, device_name: "设备乙-已盘", match_status: "一致" },
  ].map((row) => ({ record_type: "normal", actual_model: "M", asset_barcodes: `A${row.id}`,
    cabinet_barcodes: "C1", rack_no: "R1", ...row }));
  const api = {
    async getTaskItems() {
      return { task: { task_name: "排序测试", status }, items: rows.map((row) => ({ ...row, write_locked: status === "done" })) };
    },
    async submitScan(payload) {
      Object.assign(rows.find((row) => row.id === payload.record_id), { ...payload, match_status: "一致" });
      return { match_status: "一致", record_type: "normal" };
    },
    async submitMissing(id) {
      Object.assign(rows.find((row) => row.id === id), { record_type: "missing", match_status: "盘亏" });
      return { match_status: "盘亏" };
    },
    async submitSurplus(payload) {
      const record = { ...payload, id: 7, match_status: "盘盈", record_type: "surplus" };
      rows.unshift(record);
      return { record };
    },
  };
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/scan/scan.js", "utf8"), {
    ...fakeTimers(),
    getApp: () => ({ globalData: { userInfo: { user_id: 1 } } }),
    Page: (value) => { definition = value; },
    require: (name) => name.endsWith("/api") ? api : { suggestDeviceModels: () => [] },
    wx: {
      getStorageSync: (key) => storage.get(key),
      setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: (key) => storage.delete(key),
      showToast() {},
      showActionSheet: (options) => options.success({ tapIndex: 0 }),
      showModal: (options) => { modalPromise = options.success({ confirm: true }); },
    },
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)), taskId: 1,
    setData(patch) { Object.assign(this.data, patch); } };
  const ids = () => Array.from(page.data.devices, (row) => row.id);
  const sourceIds = () => Array.from(page.data.allDevices, (row) => row.id);
  const event = (id) => ({ currentTarget: { dataset: { id } } });

  await page.loadDevices();
  assert.deepEqual(ids(), [2, 4, 1, 3, 5, 6], "首次进入未盘优先，盘亏和盘盈均属于已处理组，组内不打乱清单顺序");
  assert.deepEqual(sourceIds(), [1, 2, 3, 4, 5, 6], "排序仅作用于展示列表，不改原始记录顺序");
  assert.equal(page.data.totalCount, 5);
  assert.equal(page.data.scannedCount, 3);

  await page.submitScan(event(2));
  assert.deepEqual(ids(), [4, 1, 2, 3, 5, 6], "提交成功后设备立即下沉至已处理组的原位置");
  assert.deepEqual(sourceIds(), [1, 2, 3, 4, 5, 6]);
  assert.equal(page.data.scannedCount, 4);
  page.reopenDevice(event(1));
  await modalPromise;
  assert.deepEqual(ids(), [1, 4, 2, 3, 5, 6], "重新打开已盘设备后，按原顺序上浮到待处理组");
  page.onModelInput({ ...event(1), detail: { value: "重新核实型号" } });
  await page.loadDevices();
  assert.deepEqual(ids(), [1, 4, 2, 3, 5, 6], "刷新恢复编辑草稿后仍优先显示");
  assert.equal(page.data.scannedCount, 3);

  page.onSearchInput({ detail: { value: "设备甲" } });
  assert.deepEqual(ids(), [1, 4, 2, 5], "搜索后的各状态组继续保留原清单顺序");
  page.onDeviceFilterChange({ detail: { value: "1" } });
  assert.deepEqual(ids(), [1, 4], "未盘筛选同时遵循搜索条件和原顺序");
  page.onDeviceFilterChange({ detail: { value: "0" } });
  assert.deepEqual(ids(), [1, 4, 2, 5]);
  page.onSearchInput({ detail: { value: "" } });
  page.markMissing(event(4));
  await modalPromise;
  assert.deepEqual(ids(), [1, 2, 3, 4, 5, 6], "标记盘亏成功后立即进入已处理组");
  assert.equal(page.data.scannedCount, 4);

  page.updateSurplusForm({ deviceName: "新盘盈设备", actualModel: "M", assetNoBarcode: true, cabinetNoBarcode: true, rackNo: "R1" });
  await page.submitSurplusDevice();
  assert.deepEqual(ids(), [1, 7, 2, 3, 4, 5, 6], "新登记的已处理盘盈不能跑到未盘设备前");
  assert.deepEqual(sourceIds(), [7, 1, 2, 3, 4, 5, 6], "保留原有盘盈插入规则和原清单相对顺序");
  assert.equal(page.data.totalCount, 5);
  assert.equal(page.data.scannedCount, 4);
  assert.equal(page.data.allDevices.find((row) => row.id === 1).device_name, "设备甲-已盘");

  status = "done";
  await page.loadDevices();
  assert.equal(page.data.taskLocked, true);
  assert.deepEqual(ids(), [7, 1, 2, 3, 4, 5, 6], "全部已处理的锁定任务仍按原始组内顺序展示，陈旧草稿不导致上浮");
  assert.deepEqual(sourceIds(), [7, 1, 2, 3, 4, 5, 6]);
  assert.equal(page.data.scannedCount, 5);
}

async function verifyDeviceBarcodeSearch() {
  let definition;
  const rows = [
    { id: 1, device_name: "已盘设备", expected_barcode: "TEST-BASE-000001", match_status: "一致",
      actual_model: "现场型号", asset_barcodes: "FIELD-NEW-001", cabinet_barcodes: "CAB-7788", rack_no: "AA04" },
    { id: 2, device_name: "待盘设备甲", expected_barcode: "TEST-ASSET-123456", match_status: "未扫",
      expected_model: "OptiX PTN 7900E" },
    { id: 3, device_name: "待盘设备乙", expected_barcode: "MULTI-A, TEST-ASSET-65 4321", match_status: "未扫" },
    { id: 4, device_name: "管理员修正设备", expected_barcode: "", match_status: "一致",
      asset_barcodes: "ADMIN-NEW", original_asset_barcodes: "ADMIN-OLD" },
  ].map((row) => ({ record_type: "normal", ...row }));
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/scan/scan.js", "utf8"), {
    ...fakeTimers(),
    getApp: () => ({}),
    Page: (value) => { definition = value; },
    require: (name) => name.endsWith("/api") ? {
      async getTaskItems() { return { task: { task_name: "条码检索测试", status: "doing" }, items: rows }; },
    } : { suggestDeviceModels: () => [] },
    wx: { getStorageSync() {} },
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)), taskId: 1,
    setData(patch) { Object.assign(this.data, patch); } };
  const ids = () => Array.from(page.data.devices, (row) => row.id);
  const search = (value) => { page.onSearchInput({ detail: { value } }); return ids(); };
  await page.loadDevices();
  const originalRack = { id: 5, match_status: "未扫", device_extra: { "设备机架": "旧柜位" } };
  assert.equal(page.makeDevice(originalRack).expectedRackNo, "旧柜位");
  assert.equal(page.makeDevice({ ...originalRack, expected_rack_no: "新柜位" }).expectedRackNo, "新柜位", "当前综资柜位优先于原始导入值");
  assert.equal(page.makeDevice({ ...originalRack, expected_rack_no: "" }).expectedRackNo, "", "当前综资柜位清空后不能回退旧值");
  assert.deepEqual(ids(), [2, 3, 1, 4]);
  assert.deepEqual(search("123456"), [2], "未盘设备应能通过清单资产条码的末尾片段找到");
  assert.deepEqual(search("asset-1234"), [2], "条码任意连续片段匹配且不区分大小写");
  assert.deepEqual(search(" test- asset-123 456 "), [2], "输入资产条码时忽略首尾及中间空白");
  assert.deepEqual(search("multi-a"), [3], "多资产条码设备可由任意一个清单条码找到");
  assert.deepEqual(search("asset-6543"), [3], "清单条码中夹带空白仍可检索");
  assert.deepEqual(search("new-001"), [1], "已盘设备保留现场实际资产条码搜索");
  assert.deepEqual(search("admin-new"), [4], "管理员修正后按接口返回的最新有效现场条码检索");
  assert.deepEqual(search("admin-old"), [], "历史原始现场条码不参与最新有效条码检索");
  assert.deepEqual(search("7788"), [1], "保留现场机柜资产条码检索");
  assert.deepEqual(search("待盘设备甲"), [2], "保留设备名称搜索");
  assert.deepEqual(search("ptn 7900e"), [2], "保留清单型号搜索");
  assert.deepEqual(search("现场型号"), [1], "保留现场型号搜索");
  assert.deepEqual(search("aa04"), [1], "保留柜位搜索");
  assert.deepEqual(search("不存在的条码"), []);
  search("NEW");
  assert.deepEqual(ids(), [1, 4]);
  page.onDeviceFilterChange({ detail: { value: "1" } });
  assert.deepEqual(ids(), [], "未盘筛选继续与条码查询共同生效");
  search("test-asset");
  assert.deepEqual(ids(), [2, 3]);
  page.onDeviceFilterChange({ detail: { value: "0" } });
  assert.deepEqual(search("  \t "), [2, 3, 1, 4], "清空查询后恢复未盘优先，保留原组内顺序");
}

async function verifySurplusCorrectionAndDeletion() {
  let definition;
  let taskStatus = "doing";
  let replacementTaskId = null;
  let modalConfirm = true;
  let modalPromise;
  let deferModal = false;
  let failDelete = false;
  let holdDelete = false;
  let finishDelete;
  let createCalls = 0;
  const storage = new Map();
  const modals = [];
  const toasts = [];
  const submitted = [];
  const deleted = [];
  const rows = [
    { id: 1, device_name: "清单设备", record_type: "normal", match_status: "一致" },
    { id: 2, device_name: "误录盘盈名称", record_type: "surplus", match_status: "不一致" },
    { id: 3, device_name: "已锁定盘盈", record_type: "surplus", match_status: "不一致", write_locked: true },
  ].map((row) => ({ actual_model: "旧型号", asset_barcodes: "A1", cabinet_barcodes: "C1", rack_no: "R1", ...row }));
  const api = {
    async getTaskItems() {
      return { task: { task_name: "盘盈修正测试", status: taskStatus, superseded_by_task_id: replacementTaskId },
        items: rows.map((row) => ({ ...row })) };
    },
    async submitScan(payload) {
      submitted.push({ ...payload });
      const row = rows.find((item) => item.id === payload.record_id);
      Object.assign(row, payload);
      return { record_id: row.id, record_type: row.record_type, match_status: row.match_status };
    },
    async submitSurplus() { createCalls += 1; throw new Error("修正不能新建盘盈"); },
    async deleteSurplus(taskId, id) {
      deleted.push([taskId, id]);
      if (holdDelete) await new Promise((resolve) => { finishDelete = resolve; });
      if (failDelete) throw new Error("网络请求失败");
      rows.splice(rows.findIndex((row) => row.id === id), 1);
      return { success: true, deleted: true };
    },
  };
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/scan/scan.js", "utf8"), {
    ...fakeTimers(),
    getApp: () => ({}),
    Page: (value) => { definition = value; },
    require: (name) => name.endsWith("/api") ? api : { suggestDeviceModels: () => [] },
    wx: {
      getStorageSync: (key) => storage.get(key),
      setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: (key) => storage.delete(key),
      showToast: (options) => toasts.push(options),
      showModal: (options) => {
        modals.push(options);
        if (!deferModal) modalPromise = options.success ? options.success({ confirm: modalConfirm }) : Promise.resolve();
      },
    },
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)), taskId: 22,
    setData(patch) { Object.assign(this.data, patch); } };
  const event = (id) => ({ currentTarget: { dataset: { id } } });
  const input = (id, value) => ({ ...event(id), detail: { value } });
  const surplus = () => page.data.allDevices.find((row) => row.id === 2);
  const assertProgress = () => {
    assert.equal(page.data.totalCount, 1, "盘盈编辑和删除不改变清单设备总数");
    assert.equal(page.data.scannedCount, 1, "盘盈编辑和删除不改变清单已盘数");
  };
  await page.loadDevices();
  assertProgress();
  modalConfirm = false;
  page.reopenDevice(event(2));
  await modalPromise;
  assert.equal(surplus().submitted, true, "取消修正保留已保存状态");
  assert.equal(storage.has("scan_draft_2"), false);

  modalConfirm = true;
  page.reopenDevice(event(2));
  await modalPromise;
  assert.equal(surplus().submitted, false, "盘盈可重新打开原记录表单");
  assert.equal(surplus().recordType, "surplus");
  page.onDeviceNameInput(input(2, "   "));
  await page.submitScan(event(2));
  assert.equal(submitted.length, 0, "盘盈名称不能为空");
  assert.equal(toasts.at(-1).title, "请填写盘盈设备名称");
  page.onDeviceNameInput(input(2, "  更正盘盈设备  "));
  page.onModelInput(input(2, "手工型号-X9"));
  page.onRackNoInput(input(2, "R2"));
  page.onNotesInput(input(2, "核实后修正"));
  page.updateDevice(2, () => ({ assetBarcodes: ["A2"], cabinetBarcodes: ["C2"] }));
  assert.equal(storage.get("scan_draft_2").device_name, "  更正盘盈设备  ", "名称随草稿保存");
  await page.loadDevices();
  assert.equal(surplus().device_name, "  更正盘盈设备  ", "重新进入保留盘盈名称草稿");
  assert.equal(surplus().actualModel, "手工型号-X9");
  assert.equal(surplus().submitted, false);
  assertProgress();
  await page.submitScan(event(2));
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].record_id, 2, "保存覆盖原盘盈记录ID");
  assert.equal(submitted[0].device_name, "更正盘盈设备");
  assert.equal(submitted[0].actual_model, "手工型号-X9");
  assert.equal(submitted[0].asset_barcodes, "A2");
  assert.equal(submitted[0].cabinet_barcodes, "C2");
  assert.equal(submitted[0].rack_no, "R2");
  assert.equal(submitted[0].notes, "核实后修正");
  assert.equal(createCalls, 0, "修正盘盈不调用新建接口");
  assert.equal(rows.length, 3, "不会产生重复盘盈");
  assert.equal(surplus().submitted, true);
  assert.equal(surplus().recordType, "surplus");
  assert.equal(surplus().fieldStatus, "盘盈");
  assert.equal(surplus().device_name, "更正盘盈设备");
  assert.equal(storage.has("scan_draft_2"), false);
  assert.equal(storage.has("scan_request_2"), false);
  assert.equal(page.data.collectionNotice.title, "盘盈已更新");
  assert.equal(page.data.collectionNotice.tone, "success", "盘盈保存成功不误报设备比对差异");
  await page.loadDevices();
  assert.equal(surplus().device_name, "更正盘盈设备", "刷新显示已保存新名称");
  assertProgress();

  page.onDeviceNameInput(input(1, "不能改清单名称"));
  assert.equal(page.data.allDevices.find((row) => row.id === 1).device_name, "清单设备");
  const beforeProtected = modals.length;
  for (const id of [1, 3, 999]) page.deleteSurplus(event(id));
  page.reopenDevice(event(3));
  assert.equal(modals.length, beforeProtected, "清单设备、锁定记录和无效ID不开放盘盈操作");
  assert.equal(deleted.length, 0);

  modalConfirm = false;
  page.deleteSurplus(event(2));
  await modalPromise;
  assert.equal(modals.at(-1).title, "删除盘盈");
  assert.match(modals.at(-1).content, /更正盘盈设备/);
  assert.equal(deleted.length, 0, "取消确认不发送删除请求");
  assert.ok(surplus());

  modalConfirm = true;
  failDelete = true;
  page.deleteSurplus(event(2));
  await modalPromise;
  assert.equal(deleted.length, 1);
  assert.ok(surplus(), "删除失败不能提前移除页面记录");
  assert.equal(page.data.submitting, false);
  assertProgress();

  for (const flag of ["loading", "submitting", "taskLocked"]) {
    page.setData({ [flag]: true });
    const count = modals.length;
    page.deleteSurplus(event(2));
    page.reopenDevice(event(2));
    assert.equal(modals.length, count, `${flag}期间不开放修改或删除`);
    page.setData({ [flag]: false });
  }
  deferModal = true;
  page.deleteSurplus(event(2));
  const pendingConfirmation = modals.at(-1);
  page.setData({ taskLocked: true });
  await pendingConfirmation.success({ confirm: true });
  assert.equal(deleted.length, 1, "删除确认弹窗打开期间任务被锁定，确认后仍不能删除");
  assert.ok(surplus());
  page.setData({ taskLocked: false });
  deferModal = false;
  for (const taskState of ["done", "replaced"]) {
    taskStatus = taskState === "done" ? "done" : "doing";
    replacementTaskId = taskState === "replaced" ? 23 : null;
    await page.loadDevices();
    const count = modals.length;
    page.reopenDevice(event(2));
    page.deleteSurplus(event(2));
    page.onDeviceNameInput(input(2, "锁定后不能改"));
    await page.submitScan(event(2));
    assert.equal(modals.length, count, `${taskState}任务不开放修改或删除`);
    assert.equal(surplus().device_name, "更正盘盈设备");
    assert.equal(submitted.length, 1);
    assert.equal(deleted.length, 1);
  }

  taskStatus = "doing";
  replacementTaskId = null;
  failDelete = false;
  holdDelete = true;
  await page.loadDevices();
  page.setData({ activeDeviceId: 2 });
  storage.set("scan_draft_2", { device_name: "待清理草稿" });
  storage.set("scan_request_2", "待清理请求ID");
  page.deleteSurplus(event(2));
  const pendingDelete = modalPromise;
  assert.equal(page.data.submitting, true);
  assert.ok(surplus(), "请求完成前保留记录");
  const modalCount = modals.length;
  page.deleteSurplus(event(2));
  assert.equal(modals.length, modalCount, "删除进行中重复点击不能再次弹窗或发请求");
  assert.equal(deleted.length, 2);
  finishDelete();
  await pendingDelete;
  assert.deepEqual(deleted.at(-1), [22, 2], "删除请求同时携带当前任务ID和盘盈ID");
  assert.equal(surplus(), undefined);
  assert.equal(page.data.devices.some((row) => row.id === 2), false);
  assert.equal(page.data.activeDeviceId, null);
  assert.equal(storage.has("scan_draft_2"), false);
  assert.equal(storage.has("scan_request_2"), false);
  assert.equal(page.data.submitting, false);
  assert.equal(page.data.collectionNotice.title, "盘盈已删除");
  assertProgress();
  await page.loadDevices();
  assert.equal(surplus(), undefined, "刷新后误录盘盈不会再次出现");
}

async function main() {
  await verifySurplusCorrectionAndDeletion();
  await verifyCollectionNotice();
  await verifyDeviceOrdering();
  await verifyDeviceBarcodeSearch();
  let definition;
  let scanCalls = 0;
  let scanPayload;
  let savedModel = "M";
  let surplusPayload;
  let surplusCalls = 0;
  let completionCalls = 0;
  let taskStatus = "doing";
  let replacementTaskId = null;
  let rejectSuperseded = false;
  let inheritedTaskId = null;
  let extraItems = [];
  let releaseCalls = 0;
  let cameraCalls = 0;
  const routes = [];
  let submitted = false;
  let rackReference = [];
  let rackSubmitted = false;
  let rackPayload;
  let pendingScan;
  let holdScan = true;
  let failScan = false;
  let scanWarnings = [];
  let failRackSubmit = false;
  let rackWarnings = [];
  let failSurplusSubmit = false;
  let surplusWarnings = [];
  let rackDeleteCalls = 0;
  let failRackDelete = false;
  let holdRackDelete = false;
  let pendingRackDelete;
  let modalConfirm = true;
  const storage = new Map();
  const modals = [];
  const toasts = [];
  const conflictError = () => Object.assign(new Error("条码 C1 冲突：ODF A01；IODF B02，请核实双方登记"), { statusCode: 409 });
  const api = {
    async getTaskItems() {
      return { task: { task_name: "测试任务", status: taskStatus, superseded_by_task_id: replacementTaskId,
        replacement_notice: replacementTaskId ? "该机房有最新任务，请释放当前任务并领取最新任务" : "" }, items: [{ id: 1, device_name: "设备",
        expected_model: "M", expected_barcode: "A", actual_model: submitted ? savedModel : "",
        device_extra: '{"设备类别":" OTN ","设备机架":"R1"}',
        match_status: submitted ? "一致" : "未扫", record_type: "normal", write_locked: taskStatus !== "doing" || Boolean(replacementTaskId),
        inherited_from_task_id: inheritedTaskId,
        scanned_at: submitted ? "2026-10-27T12:00:00" : null }, ...extraItems],
        cabinet_records: rackSubmitted ? [{ id: 9, cabinet_type: "odf", cabinet_type_label: "ODF", cabinet_barcode: "NEW-ODF", rack_no: "ODF-A1",
          inherited_from_task_id: inheritedTaskId, scanned_at: "2026-10-27T11:00:00" }] : [],
        cabinet_summary: { total: rackSubmitted ? 1 : 0, equipment: 0, odf: rackSubmitted ? 1 : 0, iodf: 0 },
        rack_reference: rackReference };
    },
    async submitScan(data) {
      scanCalls += 1;
      scanPayload = data;
      if (rejectSuperseded) throw Object.assign(new Error("该机房有最新任务，请释放当前任务并领取最新任务"), { statusCode: 409 });
      if (failScan) throw conflictError();
      if (holdScan) await new Promise((resolve) => { pendingScan = resolve; });
      submitted = true;
      savedModel = data.actual_model;
      return { match_status: "一致", asset_match_status: "一致", model_match_status: "一致", record_type: "normal", warnings: scanWarnings };
    },
    async completeInventoryTask() {
      completionCalls += 1;
      taskStatus = "done";
    },
    async releaseTask(taskId) {
      assert.equal(Number(taskId), 1);
      releaseCalls += 1;
      taskStatus = "cancelled";
      return { success: true, replacement_task_id: 13 };
    },
    async submitCabinet(data) {
      rackPayload = data;
      if (failRackSubmit) throw conflictError();
      rackSubmitted = true;
      return { cabinet: { id: 9, cabinet_type: "odf", cabinet_type_label: "ODF", cabinet_barcode: "NEW-ODF", rack_no: "ODF-A1" }, message: "机架已登记", warnings: rackWarnings };
    },
    async submitSurplus(data) {
      surplusCalls += 1;
      surplusPayload = data;
      if (failSurplusSubmit) throw conflictError();
      return { record: { id: 2, device_name: data.device_name, actual_model: data.actual_model, match_status: "盘盈", record_type: "surplus" }, warnings: surplusWarnings };
    },
    async deleteCabinet(taskId, cabinetId) {
      assert.equal(Number(taskId), 1);
      assert.equal(cabinetId, 9);
      rackDeleteCalls += 1;
      if (failRackDelete) throw new Error("模拟网络失败");
      if (holdRackDelete) await new Promise((resolve) => { pendingRackDelete = resolve; });
      rackSubmitted = false;
      return { success: true, deleted: true };
    },
  };
  let modalPromise;
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/scan/scan.js", "utf8"), {
    ...fakeTimers(),
    getApp: () => ({ globalData: { userInfo: { user_id: 1 } } }),
    Page: (value) => { definition = value; },
    require: (name) => name.endsWith("/api") ? api : { suggestDeviceModels: (query) => query.startsWith("PTN") ? ["PTN-7900E"] : [] },
    wx: {
      getStorageSync: (key) => storage.get(key),
      setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: (key) => storage.delete(key),
      showToast: (options) => { toasts.push(options); },
      scanCode: async () => { cameraCalls += 1; return { result: "CANNOT-USE" }; },
      redirectTo: ({ url }) => routes.push(url),
      showModal: (options) => {
        modals.push(options);
        modalPromise = options.success ? options.success({ confirm: modalConfirm }) : Promise.resolve();
      },
    },
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)), taskId: 1,
    setData(patch) { Object.assign(this.data, patch); } };
  await page.loadDevices();
  assert.equal(page.data.taskLocked, false);
  assert.equal(page.data.scannedCount, 0);
  assert.equal(page.data.devices[0].deviceCategory, "OTN", "未盘设备显示清单设备类别");
  assert.equal(page.data.devices[0].device_name, "设备", "类别只作标签，不改网元名称");
  const objectExtraDevice = page.makeDevice({ id: 20, device_name: "PTN设备", match_status: "未扫",
    device_extra: { "设备类别": "PTN" } });
  assert.equal(objectExtraDevice.deviceCategory, "PTN", "兼容已解析的设备扩展信息");
  const legacyExtraDevice = page.makeDevice({ id: 23, device_name: "127-测试节点-PTN(测试区)", match_status: "未扫",
    device_extra: { "网元类型": " PTN " } });
  assert.equal(legacyExtraDevice.deviceCategory, "PTN", "旧任务的网元类型兼容显示设备类别");
  assert.equal(legacyExtraDevice.device_name, "127-测试节点-PTN(测试区)");
  const legacyJsonDevice = page.makeDevice({ id: 24, device_name: "旧任务设备", match_status: "未扫",
    device_extra: '{"网元类型":"OTN"}' });
  assert.equal(legacyJsonDevice.deviceCategory, "OTN", "兼容旧任务JSON扩展字段");
  const currentCategoryDevice = page.makeDevice({ id: 25, device_name: "新清单设备", match_status: "未扫",
    device_extra: { "设备类别": " OTN ", "网元类型": "PTN" } });
  assert.equal(currentCategoryDevice.deviceCategory, "OTN", "新设备类别字段优先于旧网元类型");
  const blankCurrentCategoryDevice = page.makeDevice({ id: 26, device_name: "旧清单补字段设备", match_status: "未扫",
    device_extra: { "设备类别": "  ", "网元类型": "PTN" } });
  assert.equal(blankCurrentCategoryDevice.deviceCategory, "PTN", "新字段空白时仍回退旧字段");
  const uncategorizedDevice = page.makeDevice({ id: 21, device_name: "无类别设备", match_status: "未扫",
    device_extra: { "设备类别": "  " } });
  assert.equal(uncategorizedDevice.deviceCategory, "", "空类别不虚构标签");
  const surplusWithoutCategory = page.makeDevice({ id: 22, device_name: "现场盘盈设备", match_status: "盘盈", record_type: "surplus" });
  assert.equal(surplusWithoutCategory.deviceCategory, "");
  assert.equal(surplusWithoutCategory.device_name, "现场盘盈设备", "清单外设备正常显示原名称");

  page.confirmTaskComplete();
  assert.equal(completionCalls, 0, "未盘完时不能确认完成");
  const event = { currentTarget: { dataset: { id: 1 } } };
  page.updateDevice(1, () => ({ assetBarcodes: ["A"], cabinetBarcodes: ["C"], rackNo: "R" }));
  await page.submitScan(event);
  assert.equal(scanCalls, 0, "型号为空仍须填写或选择不涉及");
  page.onModelInput({ ...event, detail: { value: "PTN" } });
  assert.equal(page.data.allDevices[0].modelSuggestions.length, 1);
  page.chooseModelSuggestion({ currentTarget: { dataset: { id: 1, model: "PTN-7900E" } } });
  assert.equal(page.data.allDevices[0].actualModel, "PTN-7900E");
  assert.equal(page.data.allDevices[0].modelSuggestions.length, 0, "选择候选后收起列表");
  page.toggleModelNotApplicable(event);
  assert.equal(page.data.allDevices[0].actualModel, "不涉及");
  assert.equal(page.data.allDevices[0]._modelNotApplicable, true);
  assert.equal(storage.get("scan_draft_1").actualModel, "不涉及", "不涉及保存到草稿");
  await page.loadDevices();
  assert.equal(page.data.allDevices[0]._modelNotApplicable, true, "重进表单恢复不涉及状态");
  assert.equal(page.data.allDevices[0].modelSuggestions.length, 0);
  page.toggleModelNotApplicable(event);
  assert.equal(page.data.allDevices[0].actualModel, "");
  assert.equal(page.data.allDevices[0]._modelNotApplicable, false);
  page.onModelInput({ ...event, detail: { value: "  未收录现场型号-X9  " } });
  assert.equal(page.data.allDevices[0].modelConfirmed, false);
  assert.equal(page.data.allDevices[0].modelSuggestions.length, 0);
  const firstSubmit = page.submitScan(event);
  await page.submitScan(event);
  assert.equal(scanCalls, 1, "重复点击不能发起并行提交");
  page.confirmTaskComplete();
  assert.equal(completionCalls, 0, "提交期间不能确认完成");
  pendingScan();
  await firstSubmit;
  assert.equal(scanPayload.actual_model, "未收录现场型号-X9", "未命中候选且未额外确认的手输型号直接提交");
  assert.equal(Object.prototype.hasOwnProperty.call(scanPayload, "device_name"), false, "提交按记录ID保存，不把类别拼入网元名写回");
  assert.equal(Object.prototype.hasOwnProperty.call(scanPayload, "deviceCategory"), false, "展示类别不进入采集提交字段");
  holdScan = false;
  assert.equal(page.data.scannedCount, 1);
  assert.equal(page.data.devices[0].deviceCategory, "OTN", "已盘设备继续显示类别");
  assert.equal(page.data.taskLocked, false, "扫完最后设备仍允许补录机柜");

  modalConfirm = false;
  page.confirmTaskComplete();
  await modalPromise;
  assert.equal(completionCalls, 0, "未确认机架盘点完成不能提交任务");
  assert.equal(page.data.taskLocked, false);
  assert.equal(page.data.activeTab, "cabinets", "继续盘点应展示机架页方便补录");
  assert.match(modals.at(-1).content, /请确认机架是否已完成盘点/);
  assert.equal(modals.at(-1).confirmText, "已完成");
  modalConfirm = true;
  page.confirmTaskComplete();
  await modalPromise;
  assert.equal(completionCalls, 1);
  assert.equal(page.data.taskLocked, true);
  assert.equal(page.data.devices[0].deviceCategory, "OTN", "完成锁定页继续显示类别");
  assert.equal(page.data.devices[0].device_name, "设备");
  await page.submitScan(event);
  await page.submitCabinet();
  await page.submitSurplusDevice();
  page.reopenDevice(event);
  page.toggleModelNotApplicable(event);
  page.onModelInput({ ...event, detail: { value: "不能修改" } });
  page.toggleSurplusModelNotApplicable();
  assert.equal(scanCalls, 1, "完成后禁止再次提交现场数据");
  assert.equal(page.data.allDevices[0].actualModel, "未收录现场型号-X9", "已完成任务不能改型号或切换不涉及");
  assert.equal(page.data.surplusForm.actualModel, "", "已完成任务不能修改盘盈型号");
  assert.equal(page.data.allDevices[0].submitted, true);

  storage.set("scan_draft_1", { actualModel: "陈旧草稿" });
  const record = page.makeDevice({ id: 1, actual_model: "管理员修正值", match_status: "一致", write_locked: true });
  assert.equal(record.actualModel, "管理员修正值", "锁定后陈旧草稿不能遮盖管理员修正值");
  assert.equal(record.submitted, true);
  taskStatus = "doing";
  rackReference = [{ id: 8, cabinet_type: "odf", rack_no: "ODF-A1", cabinet_barcode: "OLD-ODF" }];
  await page.loadDevices();
  assert.equal(page.data.totalCount, 1, "往期机架参考不能计入本次清单任务数");
  assert.equal(page.data.cabinetRecords.length, 0, "往期机架不能成为本次已盘记录");
  page.onRackCandidateChange({ detail: { value: "1" } });
  assert.equal(page.data.cabinetForm.rackNo, "ODF-A1");
  page.updateCabinetForm({ cabinetBarcode: "NEW-ODF" });
  await page.submitCabinet();
  assert.equal(rackPayload.cabinet_type, "odf");
  assert.equal(rackPayload.rack_no, "ODF-A1");
  assert.equal(page.data.cabinetRecords.length, 1);
  assert.equal(page.data.totalCount, 1);

  const rackEvent = { currentTarget: { dataset: { id: 9 } } };
  page.data.cabinetRecords[0].has_admin_correction = true;
  const modalCount = modals.length;
  page.deleteCabinet(rackEvent);
  assert.equal(modals.length, modalCount, "管理员修正过的机架不开放现场删除");
  assert.equal(rackDeleteCalls, 0);
  page.data.cabinetRecords[0].has_admin_correction = false;
  modalConfirm = false;
  page.deleteCabinet(rackEvent);
  await modalPromise;
  assert.equal(rackDeleteCalls, 0, "取消确认不能删除机架");
  assert.equal(page.data.cabinetRecords.length, 1);
  modalConfirm = true;
  failRackDelete = true;
  page.deleteCabinet(rackEvent);
  await modalPromise;
  assert.equal(page.data.cabinetRecords.length, 1, "删除请求失败时保留机架记录");
  assert.equal(page.data.cabinetSummary.odf, 1);
  assert.equal(page.data.submitting, false);
  failRackDelete = false;
  holdRackDelete = true;
  page.deleteCabinet(rackEvent);
  const deleting = modalPromise;
  assert.equal(page.data.submitting, true);
  page.deleteCabinet(rackEvent);
  page.confirmTaskComplete();
  assert.equal(rackDeleteCalls, 2, "删除中重复点击不能再次请求");
  assert.equal(completionCalls, 1, "删除中不能完成并锁定任务");
  pendingRackDelete();
  await deleting;
  assert.equal(page.data.cabinetRecords.length, 0);
  assert.equal(page.data.cabinetSummary.odf, 0);
  assert.equal(page.data.rackRows.length, 1, "删除本次机架不影响历史参考");
  assert.equal(page.data.totalCount, 1, "删除机架不影响设备任务数量");
  assert.equal(page.data.submitting, false);
  taskStatus = "done";
  rackSubmitted = true;
  await page.loadDevices();
  page.deleteCabinet(rackEvent);
  assert.equal(rackDeleteCalls, 2, "锁定任务不能删除机架");

  taskStatus = "doing";
  submitted = false;
  rackSubmitted = false;
  storage.delete("scan_draft_1");
  await page.loadDevices();
  page.updateDevice(1, () => ({ actualModel: "现场型号", assetBarcodes: ["A"], cabinetBarcodes: ["C1"], rackNo: "R1" }));
  failScan = true;
  let toastCount = toasts.length;
  await page.submitScan(event);
  assert.equal(page.data.allDevices[0].submitted, false, "设备提交冲突不能改成已盘点");
  assert.equal(page.data.allDevices[0].actualModel, "现场型号", "设备提交失败保留填写值");
  assert.equal(storage.get("scan_draft_1").rackNo, "R1", "设备提交失败保留草稿");
  assert.match(modals.at(-1).content, /ODF A01；IODF B02/, "冲突弹窗必须完整显示双方");
  assert.equal(toasts.length, toastCount, "失败不能显示登记成功");
  assert.equal(page.data.collectionNotice, null, "失败不能留下上次完成提示");
  failScan = false;
  scanWarnings = ["传输综合柜 C1：设备甲柜位 R1；设备乙柜位 R2"];
  await page.submitScan(event);
  assert.equal(page.data.allDevices[0].submitted, true, "采集提醒不丢弃已保存记录");
  assert.match(modals.at(-1).content, /设备甲柜位 R1；设备乙柜位 R2/);
  assert.equal(toasts.length, toastCount, "采集冲突提醒不能被普通成功提示取代");
  assert.equal(page.data.collectionNotice, null);

  page.updateCabinetForm({ cabinetType: "odf", cabinetBarcode: "NEW-ODF", rackNo: "ODF-A1" });
  failRackSubmit = true;
  await page.submitCabinet();
  assert.equal(page.data.cabinetRecords.length, 0, "机架身份冲突不加入已登记列表");
  assert.equal(page.data.cabinetForm.rackNo, "ODF-A1", "机架身份冲突保留表单");
  assert.equal(storage.get("cabinet_draft_1").cabinetBarcode, "NEW-ODF");
  assert.match(modals.at(-1).content, /ODF A01；IODF B02/);
  assert.equal(toasts.length, toastCount, "机架身份冲突不能显示登记成功");
  failRackSubmit = false;
  rackWarnings = ["条码 NEW-ODF：ODF ODF-A1；设备甲资产条码 NEW-ODF"];
  await page.submitCabinet();
  assert.match(modals.at(-1).content, /ODF ODF-A1；设备甲/);
  assert.equal(page.data.cabinetRecords.length, 1);
  assert.equal(toasts.length, toastCount);

  page.updateSurplusForm({ deviceName: "盘盈设备", actualModel: "M2", assetBarcodes: ["A2"], cabinetBarcodes: ["C2"], rackNo: "R2" });
  failSurplusSubmit = true;
  await page.submitSurplusDevice();
  assert.equal(page.data.surplusForm.deviceName, "盘盈设备", "盘盈失败保留表单");
  assert.equal(storage.get("surplus_draft_1").rackNo, "R2", "盘盈失败保留草稿");
  assert.match(modals.at(-1).content, /ODF A01；IODF B02/);
  assert.equal(toasts.length, toastCount);
  failSurplusSubmit = false;
  surplusWarnings = ["柜位 R2：设备甲机柜 C1；盘盈设备机柜 C2"];
  await page.submitSurplusDevice();
  assert.match(modals.at(-1).content, /设备甲机柜 C1；盘盈设备机柜 C2/);
  assert.equal(page.data.allDevices.some((device) => device.recordType === "surplus"), true);
  assert.equal(toasts.length, toastCount);

  scanWarnings = [];
  page.updateDevice(1, () => ({ submitted: false, assetBarcodes: ["A"], cabinetBarcodes: ["C1"], rackNo: "R1" }));
  page.toggleModelNotApplicable(event);
  await page.submitScan(event);
  assert.equal(scanPayload.actual_model, "不涉及", "清单设备不涉及按明确型号值提交");
  await page.loadDevices();
  assert.equal(page.data.allDevices[0].actualModel, "不涉及", "提交后刷新保留不涉及");
  assert.equal(page.data.allDevices[0]._modelNotApplicable, true);

  surplusWarnings = [];
  page.updateSurplusForm({ deviceName: "手输型号设备", assetNoBarcode: true, cabinetNoBarcode: true, rackNo: "R3" });
  page.onSurplusInput({ currentTarget: { dataset: { field: "actualModel" } }, detail: { value: "  自定义设备-2026  " } });
  assert.equal(page.data.surplusForm.modelConfirmed, false);
  await page.submitSurplusDevice();
  assert.equal(surplusPayload.actual_model, "自定义设备-2026", "盘盈设备未命中型号也可直接提交");
  assert.equal(page.data.allDevices[0].actualModel, "自定义设备-2026");
  page.toggleSurplusModelNotApplicable();
  assert.equal(page.data.surplusForm.actualModel, "不涉及");
  assert.equal(page.data.surplusForm.modelSuggestions.length, 0);
  page.setData({ surplusForm: {} });
  page.restoreSurplusForm();
  assert.equal(page.data.surplusForm._modelNotApplicable, true, "盘盈草稿恢复不涉及状态");
  page.toggleSurplusModelNotApplicable();
  assert.equal(page.data.surplusForm.actualModel, "");
  const callsBeforeEmptySurplus = surplusCalls;
  page.updateSurplusForm({ deviceName: "无型号设备", assetNoBarcode: true, cabinetNoBarcode: true, rackNo: "R4" });
  await page.submitSurplusDevice();
  assert.equal(surplusCalls, callsBeforeEmptySurplus, "取消不涉及后不能将空型号提交");
  page.toggleSurplusModelNotApplicable();
  await page.submitSurplusDevice();
  assert.equal(surplusPayload.actual_model, "不涉及", "盘盈设备不涉及按明确型号值提交");
  assert.equal(page.data.allDevices[0].actualModel, "不涉及");

  // 现场仍持旧表单时后台导入新版本，接口拒绝后须刷新成只读，不能继续扫描或提交。
  taskStatus = "doing";
  submitted = false;
  storage.delete("scan_draft_1");
  await page.loadDevices();
  page.updateDevice(1, () => ({ actualModel: "尚未提交型号", assetBarcodes: ["A"], cabinetBarcodes: ["C1"], rackNo: "R1" }));
  replacementTaskId = 12;
  rejectSuperseded = true;
  const scansBeforeReplacement = scanCalls;
  await page.submitScan(event);
  assert.equal(scanCalls, scansBeforeReplacement + 1, "旧表单首次提交由后端识别新清单");
  assert.equal(page.data.taskLocked, true, "409后重新读取任务状态并锁住页面");
  assert.equal(page.data.replacementTaskId, 12);
  assert.equal(page.data.allDevices[0].actualModel, "", "旧任务只读展示服务器值，不被本地未提交草稿覆盖");
  assert.match(modals.at(-1).content, /请释放当前任务并领取最新任务/);
  const completionBeforeReplacement = completionCalls;
  const surplusBeforeReplacement = surplusCalls;
  page.onModelInput({ ...event, detail: { value: "不应修改" } });
  page.onRackNoInput({ ...event, detail: { value: "不应修改" } });
  page.openSurplusForm();
  page.toggleCabinetForm();
  await page.scanAsset(event);
  await page.scanCabinet(event);
  await page.scanCabinetForm();
  await page.scanSurplusAsset();
  await page.scanSurplusCabinet();
  await page.submitScan(event);
  await page.submitCabinet();
  await page.submitSurplusDevice();
  page.confirmTaskComplete();
  assert.equal(cameraCalls, 0, "已替代任务不能调用扫码");
  assert.equal(scanCalls, scansBeforeReplacement + 1);
  assert.equal(completionCalls, completionBeforeReplacement);
  assert.equal(surplusCalls, surplusBeforeReplacement);
  assert.equal(page.data.showSurplusForm, false);
  assert.equal(page.data.showCabinetForm, false);
  assert.equal(page.data.allDevices[0].actualModel, "");
  assert.equal(page.data.allDevices[0].rackNo, "");
  page.releaseReplacedTask();
  await modalPromise;
  assert.equal(releaseCalls, 1);
  assert.deepEqual(routes, ["/pages/claim/claim?task_id=13"], "释放后只引导领取后端确认的最新任务，不自动领取");

  // 最新任务沿用的结果直接计为已处理，新增/变化设备仍需盘点。
  taskStatus = "doing";
  replacementTaskId = null;
  rejectSuperseded = false;
  inheritedTaskId = 11;
  submitted = true;
  savedModel = "沿用型号";
  rackSubmitted = true;
  storage.delete("scan_draft_1");
  extraItems = [
    { id: 3, device_name: "新增设备", record_type: "normal", match_status: "未扫" },
    { id: 4, device_name: "沿用盘盈", record_type: "surplus", match_status: "盘盈", actual_model: "M2",
      inherited_from_task_id: 11, scanned_at: "2026-10-27T10:00:00" },
  ];
  await page.loadDevices();
  assert.equal(page.data.taskLocked, false);
  assert.equal(page.data.totalCount, 2);
  assert.equal(page.data.scannedCount, 1, "沿用设备无需重扫，盘盈不计清单进度");
  assert.equal(page.data.allDevices[0].submitted, true);
  assert.equal(page.data.allDevices[1].submitted, false);
  assert.equal(page.data.allDevices[2].submitted, true);
  assert.equal(page.data.cabinetRecords.length, 1, "已登记机架沿用后直接呈现，无须重复登记");
  replacementTaskId = 15;
  await page.onShow();
  assert.equal(page.data.replacementTaskId, 15, "返回盘点页时自动发现最新清单");
  assert.equal(page.data.taskLocked, true);
  assert.equal(page.data.allDevices[0].actualModel, "沿用型号");

  const apiContext = {
    getApp: () => ({ globalData: { apiBase: "https://example.test", userInfo: {} } }),
    module: { exports: {} },
    wx: {
      request: (options) => options.success({ statusCode: 409, data: { detail: "机架条码登记冲突" } }),
      showToast() {},
    },
  };
  vm.runInNewContext(fs.readFileSync("miniprogram/utils/api.js", "utf8"), apiContext);
  await assert.rejects(apiContext.module.exports.submitCabinet({}), (error) => {
    assert.equal(error.statusCode, 409, "API错误必须保留状态码，供采集页识别身份冲突");
    assert.equal(error.message, "机架条码登记冲突");
    return true;
  });
  console.log("小程序行为验证通过：自由型号、完成锁定、采集冲突、新清单旧页锁定、释放引导及沿用记录。");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
