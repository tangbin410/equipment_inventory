const app = getApp();
const { bindProfile, getMyAdjustmentTasks } = require("../../utils/api");
const { getUnreadReminders } = require("../../utils/adjustment-reminders");

Page({
  data: {
    userName: "",
    areaName: "",
    phone: "",
    profileName: "",
    profilePhone: "",
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
      profilePhone: phone,
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

  onProfilePhoneInput(e) {
    this.setData({ profilePhone: e.detail.value });
  },

  async saveProfile() {
    const name = (this.data.profileName || "").trim();
    if (!name) {
      wx.showToast({ title: "请先填写姓名", icon: "none" });
      return;
    }
    const phone = (this.data.profilePhone || "").trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      wx.showToast({ title: "请输入正确的11位手机号", icon: "none" });
      return;
    }
    this.setData({ profileLoading: true });
    try {
      const result = await bindProfile(name, phone);
      if (result && result.user) {
        app.setUserInfo(result.user);
        this.setData({
          userName: result.user.name || name,
          phone: result.user.phone || phone,
          profileName: result.user.name || name,
          profilePhone: result.user.phone || phone,
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
