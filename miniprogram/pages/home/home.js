const app = getApp();
const { bindProfile, getMyAdjustmentTasks } = require("../../utils/api");
const { getUnreadReminders } = require("../../utils/adjustment-reminders");

Page({
  data: {
    userName: "",
    areaName: "",
    phone: "",
    profileName: "",
    profileLoading: false,
    showProfileCard: false,
    adjustmentReminderCount: 0,
    adjustmentReminderCountText: "",
  },

  onShow() {
    if (!app.isLoggedIn()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    const user = app.globalData.userInfo || {};
    const userName = user.name || "";
    const phone = user.phone || "";
    this.setData({
      userName,
      areaName: user.area_name || "汇聚及核心",
      phone,
      profileName: userName === "待绑定用户" ? "" : userName,
      showProfileCard: !phone || !userName || userName === "待绑定用户",
    });
    this.loadAdjustmentReminderCount();
  },

  async loadAdjustmentReminderCount() {
    try {
      const rows = await getMyAdjustmentTasks();
      const user = app.globalData.userInfo || {};
      const unreadCount = getUnreadReminders(rows, user.user_id)
        .reduce((total, entry) => total + entry.unreadCount, 0);
      this.setData({
        adjustmentReminderCount: unreadCount,
        adjustmentReminderCountText: unreadCount > 99 ? "99+" : String(unreadCount),
      });
    } catch (err) {
      // 通用请求层已提示网络错误，首页保留上一次未读数。
    }
  },

  onProfileNameInput(e) {
    this.setData({ profileName: e.detail.value });
  },

  async handleGetPhoneNumber(e) {
    const name = (this.data.profileName || "").trim();
    if (!name) {
      wx.showToast({ title: "请先填写姓名", icon: "none" });
      return;
    }
    const code = e.detail && e.detail.code;
    if (!code) {
      wx.showToast({ title: "未完成手机号授权", icon: "none" });
      return;
    }
    this.setData({ profileLoading: true });
    try {
      const result = await bindProfile(name, code);
      if (result && result.user) {
        app.setUserInfo(result.user);
        this.setData({
          userName: result.user.name || name,
          phone: result.user.phone || "",
          profileName: result.user.name || name,
          showProfileCard: false,
        });
      }
      wx.showToast({ title: "资料已同步", icon: "success" });
    } catch (err) {
      wx.showToast({ title: err.message || "资料同步失败", icon: "none" });
    } finally {
      this.setData({ profileLoading: false });
    }
  },

  goClaim() {
    wx.navigateTo({ url: "/pages/claim/claim" });
  },

  goMyTasks() {
    wx.navigateTo({ url: "/pages/tasks/tasks" });
  },

  goAdjustments() {
    wx.navigateTo({ url: "/pages/adjustments/adjustments" });
  },

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
