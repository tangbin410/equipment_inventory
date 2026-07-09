/* 登录页逻辑 */

const app = getApp();
const { login } = require("../../utils/api");

Page({
  data: {
    loading: false,
    showDevHint: true,  // 开发测试用
  },

  onLoad() {
    // 如果已登录，直接跳转任务页
    if (app.isLoggedIn()) {
      wx.reLaunch({ url: "/pages/tasks/tasks" });
    }
  },

  /** 微信一键登录 */
  async handleLogin() {
    this.setData({ loading: true });
    try {
      // 1. 获取微信临时 code
      const { code } = await wx.login();
      if (!code) {
        throw new Error("获取登录凭证失败");
      }

      // 2. 请求后端登录
      const user = await login(code);

      // 3. 保存用户信息
      app.setUserInfo(user);

      // 4. 跳转到任务页
      wx.reLaunch({ url: "/pages/tasks/tasks" });
    } catch (err) {
      wx.showToast({
        title: err.message || "登录失败",
        icon: "none",
        duration: 3000,
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  /** 开发测试：选择账号登录 */
  async devLogin(e) {
    const openid = e.currentTarget.dataset.openid;
    this.setData({ loading: true });
    try {
      const user = await login(openid);
      app.setUserInfo(user);
      wx.reLaunch({ url: "/pages/tasks/tasks" });
    } catch (err) {
      wx.showToast({ title: "登录失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },
});
