/* 任务列表页逻辑 */

const app = getApp();
const { getTasks } = require("../../utils/api");

Page({
  data: {
    userName: "",
    tasks: [],
    allTasks: [],
    keyword: "",
    statusFilter: "all",
    statusOptions: ["全部状态", "待处理", "进行中", "已完成"],
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
    });
  },

  /** 加载任务列表 */
  async loadTasks() {
    if (!app.isLoggedIn()) return;
    this.setData({ loading: true });
    try {
      const tasks = await getTasks(app.globalData.userInfo.user_id);
      const total = tasks.length;
      const pending = tasks.filter((t) => t.status === "pending").length;
      const doing = tasks.filter((t) => t.status === "doing").length;
      const done = tasks.filter((t) => t.status === "done").length;
      const unscannedTotal = tasks.reduce((sum, t) => sum + Math.max((t.total || 0) - (t.scanned || 0), 0), 0);
      // 预计算 WXML 模板不能处理的表达式
      const enhancedTasks = tasks.map((t) => ({
        ...t,
        _dotClass: t.status === "done" ? "done" : t.status === "doing" ? "ing" : "wait",
        _statusTag: t.status === "done" ? "已完成" : t.status === "doing" ? "进行中" : "待处理",
        _progressPct: t.total > 0 ? (t.scanned / t.total * 100) : 0,
        _unscanned: Math.max((t.total || 0) - (t.scanned || 0), 0),
        _mismatch: t.mismatch || 0,
        _tagCls: t.status === "done" ? "tag-success" : t.status === "doing" ? "" : "tag-wait",
        _actionText: t.status === "done" ? "查看结果" : t.status === "doing" ? "继续盘点" : "开始盘点",
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
    const values = ["all", "pending", "doing", "done"];
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

  /** 退出登录 */
  handleLogout() {
    wx.showModal({
      title: "提示",
      content: "确定要退出登录吗？",
      success: (res) => {
        if (res.confirm) {
          app.logout();
        }
      },
    });
  },
});
