const app = getApp();

Page({
  data: {
    userName: "",
    areaName: "",
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
  },

  goClaim() {
    wx.navigateTo({ url: "/pages/claim/claim" });
  },

  goMyTasks() {
    wx.navigateTo({ url: "/pages/tasks/tasks" });
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
