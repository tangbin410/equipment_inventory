/* API 调用封装（CommonJS 模块，兼容微信小程序） */

const app = getApp();

/**
 * 通用请求
 * @param {string} method  - GET / POST
 * @param {string} path    - 路径，如 /tasks
 * @param {object} data    - 参数
 */
function request(method, path, data = {}) {
  const base = app.globalData.apiBase;
  const url = base + path;

  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method,
      data,
      header: { "Content-Type": "application/json" },
      timeout: 15000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const msg = (res.data && res.data.detail) || ("请求失败 (" + res.statusCode + ")");
          wx.showToast({ title: msg, icon: "none", duration: 3000 });
          reject(new Error(msg));
        }
      },
      fail(err) {
        wx.showToast({
          title: "网络异常，请检查服务器连接",
          icon: "none",
          duration: 3000,
        });
        reject(err);
      },
    });
  });
}

// 用户相关
function login(code) { return request("POST", "/login", { code }); }
function getWorkers() { return request("GET", "/workers"); }

// 任务相关
function getTasks(userId) { return request("GET", "/tasks?user_id=" + userId); }
function getTaskItems(taskId) { return request("GET", "/tasks/" + taskId + "/items"); }

// 盘点操作
function submitScan(data) { return request("POST", "/scan", data); }
function uploadEvidencePhoto(recordId, filePath) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: app.globalData.apiBase + "/records/" + recordId + "/evidence",
      filePath,
      name: "file",
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(JSON.parse(res.data));
        } else {
          const data = JSON.parse(res.data || "{}");
          wx.showToast({ title: data.detail || "照片上传失败", icon: "none" });
          reject(new Error(data.detail || "照片上传失败"));
        }
      },
      fail(err) {
        wx.showToast({ title: "照片已保存在草稿，请联网后重试", icon: "none" });
        reject(err);
      },
    });
  });
}

// 报告
function getReport(taskId) { return request("GET", "/report/" + taskId); }
function getExportUrl(taskId) {
  return app.globalData.apiBase + "/export/" + taskId;
}

module.exports = {
  login,
  getWorkers,
  getTasks,
  getTaskItems,
  submitScan,
  uploadEvidencePhoto,
  getReport,
  getExportUrl,
};
