/* 任务列表页逻辑 */

const app = getApp();
const { getMyTasks, releaseTask: releaseTaskApi } = require("../../utils/api");

Page({
  data: {
    userName: "",
    areaName: "",
    tasks: [],
    allTasks: [],
    keyword: "",
    statusFilter: "all",
    statusOptions: ["全部状态", "盘点中", "已完成"],
    totalTasks: 0,
    pendingTasks: 0,
    doingTasks: 0,
    doneTasks: 0,
    unscannedTotal: 0,
    _tasksEmpty: false,
    loading: true,
  },

  onShow() {
    this.checkLogin();
    this.loadTasks();
  },

  onPullDownRefresh() {
    this.loadTasks();
  },

  /** 检查登录状态 */
  checkLogin() {
    if (!app.isLoggedIn()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    this.setData({
      userName: app.globalData.userInfo.name,
      areaName: app.globalData.userInfo.area_name || "默认片区",
    });
  },

  /** 加载任务列表 */
  async loadTasks() {
    if (!app.isLoggedIn()) return;
    this.setData({ loading: true });
    try {
      const tasks = await getMyTasks(app.globalData.userInfo.user_id);
      const total = tasks.length;
      const pending = 0;
      const doing = tasks.filter((t) => t.status === "doing").length;
      const done = tasks.filter((t) => t.status === "done").length;
      const unscannedTotal = tasks.reduce((sum, t) => sum + Math.max((t.total || 0) - (t.scanned || 0), 0), 0);
      // 预计算 WXML 模板不能处理的表达式
      const enhancedTasks = tasks.map((t) => ({
        ...t,
        _dotClass: t.status === "done" ? "done" : t.status === "doing" ? "ing" : "wait",
        _statusTag: t.status === "done" ? "已完成" : t.status === "doing" ? "盘点中" : "待领取",
        _progressPct: t.total > 0 ? (t.scanned / t.total * 100) : 0,
        _unscanned: Math.max((t.total || 0) - (t.scanned || 0), 0),
        _mismatch: t.mismatch || 0,
        _tagCls: t.status === "done" ? "tag-success" : t.status === "doing" ? "tag-doing" : "tag-wait",
        _actionText: t.status === "done" ? "查看结果" : "继续盘点",
        _canRelease: t.status === "doing",
      }));
      this.setData({
        allTasks: enhancedTasks,
        tasks: enhancedTasks,
        totalTasks: total,
        pendingTasks: pending,
        doingTasks: doing,
        doneTasks: done,
        unscannedTotal,
        _tasksEmpty: enhancedTasks.length === 0,
        loading: false,
      });
    } catch (err) {
      this.setData({ loading: false, _tasksEmpty: this.data.tasks.length === 0 });
    } finally {
      wx.stopPullDownRefresh();
    }
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value });
    this.applyFilters();
  },

  onStatusChange(e) {
    const values = ["all", "doing", "done"];
    this.setData({ statusFilter: values[Number(e.detail.value)] });
    this.applyFilters();
  },

  applyFilters() {
    const keyword = this.data.keyword.trim().toLowerCase();
    const status = this.data.statusFilter;
    const tasks = this.data.allTasks.filter((task) => {
      const matchesText = !keyword || task.task_name.toLowerCase().includes(keyword) || task.room_name.toLowerCase().includes(keyword);
      return matchesText && (status === "all" || task.status === status);
    });
    this.setData({ tasks, _tasksEmpty: tasks.length === 0 });
  },

  /** 打开任务，进入盘点页 */
  openTask(e) {
    const taskId = e.currentTarget.dataset.id;
    wx.navigateTo({
      url: `/pages/scan/scan?task_id=${taskId}`,
    });
  },

  /** 释放自己已领取但未完成的任务 */
  releaseTask(e) {
    const taskId = e.currentTarget.dataset.id;
    wx.showModal({
      title: "释放任务",
      content: "释放后，该机房会回到片区待领取池，其他同片区人员可以领取。确定释放吗？",
      confirmText: "释放",
      success: async (res) => {
        if (!res.confirm) return;
        wx.showLoading({ title: "释放中" });
        try {
          const result = await releaseTaskApi(taskId, app.globalData.userInfo.user_id, "现场人员主动释放");
          wx.hideLoading();
          wx.showToast({ title: result.message || "已释放", icon: "success" });
          this.loadTasks();
        } catch (err) {
          wx.hideLoading();
        }
      },
    });
  },

  /** 返回上级 */
  goBack() {
    const pages = getCurrentPages();
    if (pages.length > 1) {
      wx.navigateBack();
      return;
    }
    wx.reLaunch({ url: "/pages/home/home" });
  },
});
