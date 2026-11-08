/* 任务列表页逻辑 */

const app = getApp();
const { getMyTasks, releaseTask: releaseTaskApi } = require("../../utils/api");

Page({
  data: {
    mode: "active",
    pageTitle: "我的任务",
    userName: "",
    areaName: "",
    tasks: [],
    allTasks: [],
    keyword: "",
    totalTasks: 0,
    pendingTasks: 0,
    doingTasks: 0,
    scannedTotal: 0,
    unscannedTotal: 0,
    _tasksEmpty: false,
    loading: true,
    releasingTaskId: null,
  },

  onLoad(options) {
    const mode = options && options.mode === "completed" ? "completed" : "active";
    const pageTitle = mode === "completed" ? "已完成盘点" : "我的任务";
    this.setData({ mode, pageTitle });
    wx.setNavigationBarTitle({ title: pageTitle });
  },

  onShow() {
    if (this.checkLogin()) return this.loadTasks();
  },

  onPullDownRefresh() {
    if (this.checkLogin()) return this.loadTasks();
    wx.stopPullDownRefresh();
  },

  /** 检查登录状态 */
  checkLogin() {
    if (!app.isLoggedIn()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return false;
    }
    const user = app.globalData.userInfo || {};
    this.setData({
      userName: user.name || "",
      areaName: user.area_name || "汇聚及核心",
    });
    return true;
  },

  /** 加载任务列表 */
  async loadTasks() {
    if (!app.isLoggedIn()) {
      wx.stopPullDownRefresh();
      return;
    }
    this.setData({ loading: true });
    try {
      // 接口只返回当前账号领取的任务；两个入口各自固定任务状态。
      const rows = await getMyTasks();
      const completed = this.data.mode === "completed";
      const tasks = rows.filter((task) => task.status === (completed ? "done" : "doing"));
      if (completed) {
        tasks.sort((a, b) => String(b.field_completed_at || b.completed_at || "")
          .localeCompare(String(a.field_completed_at || a.completed_at || "")) || b.id - a.id);
      }
      const total = tasks.length;
      const pending = tasks.filter((t) => !(t.scanned > 0)).length;
      const doing = tasks.filter((t) => t.scanned > 0).length;
      const scannedTotal = tasks.reduce((sum, t) => sum + (t.scanned || 0), 0);
      const unscannedTotal = tasks.reduce((sum, t) => sum + Math.max((t.total || 0) - (t.scanned || 0), 0), 0);
      // 预计算 WXML 模板不能处理的表达式
      const enhancedTasks = tasks.map((t) => ({
        ...t,
        _dotClass: t.status === "done" ? "done" : t.status === "doing" ? "ing" : "wait",
        _statusTag: completed ? "已完成" : t.superseded_by_task_id ? "清单已更新" : t.scanned > 0 ? "盘点中" : "未开始",
        _progressPct: t.total > 0 ? (t.scanned / t.total * 100) : 0,
        _unscanned: Math.max((t.total || 0) - (t.scanned || 0), 0),
        _mismatch: t.mismatch || 0,
        _tagCls: t.status === "done" ? "tag-success" : t.status === "doing" ? "tag-doing" : "tag-wait",
        _actionText: t.status === "done" ? "查看结果" : t.superseded_by_task_id ? "查看旧记录" : "继续盘点",
        _canRelease: !completed && t.status === "doing",
        _completedAt: (t.field_completed_at || t.completed_at || "").replace("T", " ").slice(0, 19),
      }));
      this.setData({
        allTasks: enhancedTasks,
        totalTasks: total,
        pendingTasks: pending,
        doingTasks: doing,
        scannedTotal,
        unscannedTotal,
        _tasksEmpty: enhancedTasks.length === 0,
        loading: false,
      });
      this.applyFilters();
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

  applyFilters() {
    const keyword = this.data.keyword.trim().toLowerCase();
    const tasks = this.data.allTasks.filter((task) => {
      return !keyword || (task.task_name || "").toLowerCase().includes(keyword)
        || (task.room_name || "").toLowerCase().includes(keyword);
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
    const task = this.data.allTasks.find((row) => String(row.id) === String(taskId));
    if (this.data.mode === "completed" || !task || task.status !== "doing" || this.data.releasingTaskId) return;
    const replaced = Boolean(task.superseded_by_task_id);
    wx.showModal({
      title: replaced ? "领取最新任务前请先释放" : "释放任务",
      content: replaced
        ? `该机房有最新任务 #${task.superseded_by_task_id}，请释放当前任务并领取最新任务。释放后旧任务作废，未变化设备的已盘结果、手工机架和盘盈设备会沿用到最新任务。`
        : "释放后，该机房会回到片区待领取池，其他同片区人员可以领取。确定释放吗？",
      confirmText: replaced ? "释放任务" : "释放",
      success: async (res) => {
        if (!res.confirm || this.data.releasingTaskId) return;
        this.setData({ releasingTaskId: taskId });
        wx.showLoading({ title: "释放中" });
        try {
          const result = await releaseTaskApi(taskId, "现场人员主动释放");
          wx.hideLoading();
          wx.showToast({ title: result.message || "已释放", icon: "success" });
          await this.loadTasks();
          if (replaced || result.replacement_task_id) {
            const latestId = result.replacement_task_id;
            wx.navigateTo({ url: "/pages/claim/claim" + (latestId ? `?task_id=${latestId}` : "") });
          }
        } catch (err) {
          wx.hideLoading();
        } finally {
          this.setData({ releasingTaskId: null });
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
