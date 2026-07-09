/* 小程序入口 */

App({
  globalData: {
    userInfo: null,       // { user_id, name, role }
    // === 【重要】真机测试时把 localhost 改为电脑的局域网IP ===
    // 例: http://192.168.1.100:8000/api
    apiBase: "http://localhost:8000/api",
  },

  onLaunch() {
    // 尝试从本地缓存恢复登录状态
    const saved = wx.getStorageSync("userInfo");
    if (saved && saved.user_id) {
      this.globalData.userInfo = saved;
    }
  },

  /** 检查是否已登录 */
  isLoggedIn() {
    return this.globalData.userInfo && this.globalData.userInfo.user_id;
  },

  /** 保存登录信息 */
  setUserInfo(user) {
    this.globalData.userInfo = user;
    wx.setStorageSync("userInfo", user);
  },

  /** 退出登录 */
  logout() {
    this.globalData.userInfo = null;
    wx.removeStorageSync("userInfo");
    wx.reLaunch({ url: "/pages/login/login" });
  },
});
