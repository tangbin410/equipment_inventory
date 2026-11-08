const app = getApp();
const { getAreaTasks, claimTask: claimTaskApi } = require("../../utils/api");

function enhanceTask(task, focusTaskId) {
  const total = task.total || 0;
  const scanned = task.scanned || 0;
  const statusMap = {
    pending: "待领取",
    doing: "盘点中",
    done: "已完成",
  };
  return {
    ...task,
    _total: total,
    _scanned: scanned,
    _mismatch: task.mismatch || 0,
    _statusText: task.waiting_for_task_id ? "待旧任务释放" : statusMap[task.status] || task.status,
    _statusCls: task.waiting_for_task_id ? "waiting" : task.status === "done" ? "done" : task.status === "doing" ? "doing" : "pending",
    _progressPct: total > 0 ? (scanned / total) * 100 : 0,
    _canClaim: task.status === "pending" && !task.waiting_for_task_id && !task.superseded_by_task_id,
    _highlight: String(task.id) === String(focusTaskId),
    _claimedText: task.claimed_name ? "领取人：" + task.claimed_name : "暂未领取",
  };
}

Page({
  data: {
    userName: "",
    areaName: "",
    tasks: [],
    allTasks: [],
    statusFilter: "all",
    loading: true,
    empty: false,
    pendingCount: 0,
    doingCount: 0,
    doneCount: 0,
    waitingCount: 0,
    focusTaskId: null,
    claimingTaskId: null,
  },

  onLoad(options) {
    if (options && options.task_id) this.setData({ focusTaskId: options.task_id });
  },

  onShow() {
    if (!app.isLoggedIn()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    const user = app.globalData.userInfo || {};
    this.setData({
      userName: user.name || "",
      areaName: user.area_name || "汇聚及核心",
    });
    this.loadTasks();
  },

  onPullDownRefresh() {
    this.loadTasks();
  },

  async loadTasks() {
    if (!app.isLoggedIn()) return;
    this.setData({ loading: true });
    try {
      const rawTasks = await getAreaTasks();
      const tasks = rawTasks.map((task) => enhanceTask(task, this.data.focusTaskId));
      tasks.sort((a, b) => Number(b._highlight) - Number(a._highlight));
      this.setData({
        allTasks: tasks,
        pendingCount: tasks.filter((t) => t._canClaim).length,
        waitingCount: tasks.filter((t) => t.waiting_for_task_id).length,
        doingCount: tasks.filter((t) => t.status === "doing").length,
        doneCount: tasks.filter((t) => t.status === "done").length,
        loading: false,
      });
      this.applyFilters();
    } catch (err) {
      this.setData({ loading: false, empty: this.data.tasks.length === 0 });
    } finally {
      wx.stopPullDownRefresh();
    }
  },

  setFilter(e) {
    this.setData({ statusFilter: e.currentTarget.dataset.status });
    this.applyFilters();
  },

  applyFilters() {
    const status = this.data.statusFilter;
    const tasks = status === "all"
      ? this.data.allTasks
      : this.data.allTasks.filter((task) => task.status === status);
    this.setData({ tasks, empty: tasks.length === 0 });
  },

  async claimTask(e) {
    const taskId = e.currentTarget.dataset.id;
    const task = this.data.allTasks.find((row) => String(row.id) === String(taskId));
    if (!task || !task._canClaim || this.data.claimingTaskId) return;
    this.setData({ claimingTaskId: taskId });
    wx.showLoading({ title: "领取中" });
    try {
      const result = await claimTaskApi(taskId);
      wx.hideLoading();
      wx.showToast({ title: result.message || "领取成功", icon: "success" });
      setTimeout(() => {
        wx.navigateTo({ url: "/pages/tasks/tasks" });
      }, 500);
    } catch (err) {
      wx.hideLoading();
      await this.loadTasks();
    } finally {
      this.setData({ claimingTaskId: null });
    }
  },
});
