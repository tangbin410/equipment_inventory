const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function claimPage() {
  let definition;
  const state = {
    rows: [
      { id: 1, status: "doing", room_name: "甲机房", total: 2, scanned: 1, superseded_by_task_id: 5 },
      { id: 5, status: "pending", room_name: "甲机房", total: 3, scanned: 0, waiting_for_task_id: 1 },
      { id: 6, status: "pending", room_name: "乙机房", total: 4, scanned: 0 },
      { id: 7, status: "done", room_name: "丙机房", total: 1, scanned: 1 },
    ],
    claims: [],
    routes: [],
    requests: 0,
  };
  vm.runInNewContext(fs.readFileSync("miniprogram/pages/claim/claim.js", "utf8"), {
    getApp: () => ({ isLoggedIn: () => true, globalData: { userInfo: {} } }),
    Page: (value) => { definition = value; },
    require: () => ({
      async getAreaTasks() { state.requests += 1; return state.rows; },
      async claimTask(id) {
        state.claims.push(id);
        if (state.claimError) throw state.claimError;
        return { message: "领取成功" };
      },
    }),
    setTimeout: (callback) => callback(),
    wx: {
      navigateTo: ({ url }) => state.routes.push(url),
      showLoading() {}, hideLoading() {}, showToast() {}, stopPullDownRefresh() {},
    },
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(patch) { Object.assign(this.data, patch); } };
  return { page, state };
}

const event = (id) => ({ currentTarget: { dataset: { id } } });

test("最新任务等待旧任务释放期间不可领取，其他机房和已完成任务保持原规则", async () => {
  const { page, state } = claimPage();
  page.onLoad({ task_id: "5" });
  await page.loadTasks();
  assert.equal(page.data.tasks[0].id, 5, "释放后打开任务池时定位目标任务");
  assert.equal(page.data.tasks[0]._highlight, true);
  assert.equal(page.data.tasks[0]._statusText, "待旧任务释放");
  assert.equal(page.data.tasks[0]._canClaim, false);
  assert.equal(page.data.pendingCount, 1, "等待释放的任务不统计为可领取");
  assert.equal(page.data.waitingCount, 1);
  await page.claimTask(event(5));
  await page.claimTask(event(1));
  await page.claimTask(event(7));
  await page.claimTask(event(99));
  assert.deepEqual(state.claims, []);
  assert.deepEqual(state.routes, []);
  await page.claimTask(event(6));
  assert.deepEqual(state.claims, [6]);
  assert.deepEqual(state.routes, ["/pages/tasks/tasks"]);
});

test("旧任务释放后刷新才允许领取最新任务；领取竞争失败后刷新等待状态", async () => {
  const { page, state } = claimPage();
  page.onLoad({ task_id: "5" });
  await page.loadTasks();
  state.rows = state.rows.filter((task) => task.id !== 1)
    .map((task) => task.id === 5 ? { ...task, waiting_for_task_id: null, scanned: 1 } : task);
  await page.loadTasks();
  assert.equal(page.data.tasks[0]._canClaim, true);
  assert.equal(page.data.tasks[0]._scanned, 1, "释放后展示沿用的设备进度");
  assert.equal(page.data.waitingCount, 0);
  assert.deepEqual(state.claims, [], "刷新和定位不自动替用户领取");

  state.rows = state.rows.map((task) => task.id === 5 ? { ...task, waiting_for_task_id: 8 } : task);
  state.claimError = new Error("等待旧任务释放");
  await page.claimTask(event(5));
  assert.deepEqual(state.claims, [5]);
  assert.equal(page.data.tasks[0]._canClaim, false, "服务端拒绝后刷新并隐藏领取按钮");
  assert.equal(page.data.claimingTaskId, null);
  assert.deepEqual(state.routes, []);
  await page.claimTask(event(5));
  assert.deepEqual(state.claims, [5], "刷新后不能重复请求被阻止的任务");
});
