const app = getApp();
const { getAreaTasks, claimTask: claimTaskApi } = require("../../utils/api");

function enhanceTask(task) {
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
    _statusText: statusMap[task.status] || task.status,
    _statusCls: task.status === "done" ? "done" : task.status === "doing" ? "doing" : "pending",
    _progressPct: total > 0 ? (scanned / total) * 100 : 0,
    _canClaim: task.status === "pending",
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
  },

  onShow() {
    if (!app.isLoggedIn()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    const user = app.globalData.userInfo || {};
    this.setData({
      userName: user.name || "",
      areaName: user.area_name || "默认片区",
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
      const rawTasks = await getAreaTasks(app.globalData.userInfo.user_id);
      const tasks = rawTasks.map(enhanceTask);
      this.setData({
        allTasks: tasks,
        pendingCount: tasks.filter((t) => t.status === "pending").length,
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
    wx.showLoading({ title: "领取中" });
    try {
      const result = await claimTaskApi(taskId, app.globalData.userInfo.user_id);
      wx.hideLoading();
      wx.showToast({ title: result.message || "领取成功", icon: "success" });
      setTimeout(() => {
        wx.navigateTo({ url: "/pages/tasks/tasks" });
      }, 500);
    } catch (err) {
      wx.hideLoading();
      this.loadTasks();
    }
  },
});
