/* 小程序入口 */

const env = require("./env");

function getApiBase() {
  try {
    const account = wx.getAccountInfoSync();
    const envVersion = account && account.miniProgram && account.miniProgram.envVersion;
    return envVersion === "develop" ? env.devApiBase : env.prodApiBase;
  } catch (err) {
    return env.devApiBase;
  }
}

App({
  globalData: {
    userInfo: null,       // { user_id, name, role }
    apiBase: getApiBase(),
  },

  onLaunch() {
    // 尝试从本地缓存恢复登录状态
    const saved = wx.getStorageSync("userInfo");
    if (saved && saved.user_id && saved.access_token) {
      this.globalData.userInfo = saved;
    }
  },

  /** 检查是否已登录 */
  isLoggedIn() {
    return this.globalData.userInfo && this.globalData.userInfo.user_id && this.globalData.userInfo.access_token;
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
