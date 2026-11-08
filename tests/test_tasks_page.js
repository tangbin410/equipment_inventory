const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function taskPage() {
  let definition;
  const state = {
    loggedIn: true,
    rows: [
      { id: 1, status: "doing", task_name: "甲机房首轮", room_name: "甲机房", total: 4, scanned: 0 },
      { id: 2, status: "doing", task_name: "乙机房首轮", room_name: "乙机房", total: 5, scanned: 2 },
      { id: 3, status: "done", task_name: "甲机房旧批次", room_name: "甲机房", total: 4, scanned: 4,
        field_completed_at: "2026-10-20T09:10:11" },
      { id: 4, status: "done", task_name: "甲机房新批次", room_name: "甲机房", total: 4, scanned: 4,
        completed_at: "2026-10-28T10:20:30" },
      { id: 5, status: "pending", task_name: "待领取任务", room_name: "丙机房", total: 3, scanned: 0 },
    ],
    requests: 0,
    stopped: 0,
    releases: [],
    routes: [],
    modals: [],
  };
  const api = {
    async getMyTasks() {
      state.requests += 1;
      if (state.error) throw state.error;
      return state.rows;
    },
    async releaseTask(id) {
      state.releases.push(id);
      return state.releaseResult || { message: "已释放" };
    },
    getAreaTasks() { throw new Error("个人列表不能读取片区任务池"); },
  };
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/tasks/tasks.js", "utf8"), {
    getApp: () => ({ isLoggedIn: () => state.loggedIn,
      globalData: { userInfo: { user_id: 8, name: "现场甲", area_name: "北区" } } }),
    Page: (value) => { definition = value; },
    require: () => api,
    wx: {
      setNavigationBarTitle: ({ title }) => { state.title = title; },
      reLaunch: ({ url }) => { state.routes.push(url); },
      navigateTo: ({ url }) => { state.routes.push(url); },
      stopPullDownRefresh: () => { state.stopped += 1; },
      showModal: (options) => { state.modals.push(options); },
      showLoading() {}, hideLoading() {}, showToast() {},
    },
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(patch) { Object.assign(this.data, patch); } };
  return { page, state };
}

const ids = (page) => Array.from(page.data.tasks, (task) => task.id);
const event = (id) => ({ currentTarget: { dataset: { id } } });

test("我的任务默认只展示本人领取的待完成任务，并区分未开始和盘点中", async () => {
  const { page, state } = taskPage();
  page.onLoad({});
  await page.onShow();
  assert.equal(state.title, "我的任务");
  assert.equal(page.data.userName, "现场甲");
  assert.deepEqual(ids(page), [1, 2]);
  assert.equal(page.data.totalTasks, 2);
  assert.equal(page.data.pendingTasks, 1);
  assert.equal(page.data.doingTasks, 1);
  assert.equal(page.data.unscannedTotal, 7);
  assert.equal(page.data.tasks[0]._statusTag, "未开始");
  assert.equal(page.data.tasks[1]._statusTag, "盘点中");
  assert.ok(page.data.tasks.every((task) => task._canRelease));
  assert.equal(state.requests, 1);
});

test("已完成盘点按完成时间展示同一账号的独立批次，只能查看不能释放", async () => {
  const { page, state } = taskPage();
  page.onLoad({ mode: "completed" });
  await page.onShow();
  assert.equal(state.title, "已完成盘点");
  assert.deepEqual(ids(page), [4, 3]);
  assert.equal(page.data.totalTasks, 2);
  assert.equal(page.data.scannedTotal, 8);
  assert.equal(page.data.tasks[0]._completedAt, "2026-10-28 10:20:30");
  assert.ok(page.data.tasks.every((task) => !task._canRelease && task._actionText === "查看结果"));
  page.releaseTask(event(4));
  page.releaseTask(event(1));
  assert.equal(state.modals.length, 0);
  assert.equal(state.releases.length, 0);
  page.openTask(event(4));
  assert.equal(state.routes[0], "/pages/scan/scan?task_id=4");
});

test("两个入口的搜索和刷新都保留关键词，不能混入另一状态的任务", async () => {
  for (const mode of ["active", "completed"]) {
    const { page, state } = taskPage();
    page.onLoad({ mode });
    await page.onShow();
    page.onKeywordInput({ detail: { value: "  甲机房  " } });
    state.rows = [...state.rows,
      { id: 6, status: "doing", task_name: "甲机房补充", room_name: "甲机房", total: 2, scanned: 1 },
      { id: 7, status: "done", task_name: "乙机房完成", room_name: "乙机房", total: 2, scanned: 2 },
    ];
    await page.onPullDownRefresh();
    assert.equal(page.data.keyword, "  甲机房  ");
    assert.equal(page.data.mode, mode);
    assert.deepEqual(ids(page), mode === "active" ? [1, 6] : [4, 3]);
    page.onKeywordInput({ detail: { value: "找不到的机房" } });
    await page.onShow();
    assert.equal(page.data._tasksEmpty, true);
    assert.deepEqual(ids(page), []);
    assert.ok(state.stopped >= 3);
  }
});

test("释放只对当前待完成任务生效，完成或未知任务即使直接调用方法也不请求", async () => {
  const { page, state } = taskPage();
  page.onLoad({ mode: "unknown" });
  await page.onShow();
  assert.equal(page.data.mode, "active");
  page.releaseTask(event(3));
  page.releaseTask(event(99));
  assert.equal(state.modals.length, 0);
  page.releaseTask(event("1"));
  assert.equal(state.modals.length, 1);
  await state.modals[0].success({ confirm: false });
  assert.equal(state.releases.length, 0);
  page.releaseTask(event(1));
  await state.modals[1].success({ confirm: true });
  assert.deepEqual(state.releases, [1]);
});

test("未登录不读取任务，刷新仍结束；请求失败保留当前模式和筛选结果", async () => {
  const { page, state } = taskPage();
  page.onLoad({ mode: "completed" });
  state.loggedIn = false;
  await page.onShow();
  await page.onPullDownRefresh();
  assert.equal(state.requests, 0);
  assert.equal(state.stopped, 1);
  assert.deepEqual(state.routes, ["/pages/login/login", "/pages/login/login"]);
  state.loggedIn = true;
  await page.onShow();
  page.onKeywordInput({ detail: { value: "新批次" } });
  state.error = new Error("网络错误");
  await page.onPullDownRefresh();
  assert.equal(page.data.mode, "completed");
  assert.equal(page.data.keyword, "新批次");
  assert.deepEqual(ids(page), [4]);
  assert.equal(page.data.loading, false);
});

test("首页两个入口复用同一任务页，并为已完成入口传固定模式", () => {
  let definition;
  const routes = [];
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/home/home.js", "utf8"), {
    getApp: () => ({}), Page: (value) => { definition = value; }, require: () => ({}),
    wx: { navigateTo: ({ url }) => routes.push(url) },
  });
  definition.goMyTasks();
  definition.goCompletedTasks();
  assert.deepEqual(routes, ["/pages/tasks/tasks", "/pages/tasks/tasks?mode=completed"]);
});

test("被新清单替代的本人任务只读提示，释放后按服务器最新任务号引导领取", async () => {
  const { page, state } = taskPage();
  state.rows[0].superseded_by_task_id = 8;
  page.onLoad({});
  await page.onShow();
  assert.equal(page.data.tasks[0]._statusTag, "清单已更新");
  assert.equal(page.data.tasks[0]._actionText, "查看旧记录");
  assert.equal(page.data.tasks[0]._canRelease, true);
  page.releaseTask(event(1));
  assert.match(state.modals.at(-1).content, /最新任务 #8/);
  assert.match(state.modals.at(-1).content, /未变化设备的已盘结果、手工机架和盘盈设备/);
  state.releaseResult = { message: "旧任务已作废", replacement_task_id: 9 };
  state.rows = state.rows.filter((task) => task.id !== 1);
  await state.modals.at(-1).success({ confirm: true });
  assert.deepEqual(state.releases, [1]);
  assert.equal(ids(page).includes(1), false);
  assert.deepEqual(state.routes, ["/pages/claim/claim?task_id=9"], "连续导入时应使用释放接口返回的最新号");
  assert.equal(page.data.releasingTaskId, null);
});
