const app = getApp();
const { completeAdjustmentTask, getMyAdjustmentTasks } = require("../../utils/api");
const { getUnreadReminders, markRemindersSeen } = require("../../utils/adjustment-reminders");

function formatReminderTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

Page({
  data: {
    userName: "",
    areaName: "",
    tasks: [],
    totalCount: 0,
    pendingCount: 0,
    verifyCount: 0,
    doneCount: 0,
    loading: true,
    empty: false,
  },

  onShow() {
    this.checkLogin();
    this.loadAdjustments();
  },

  onPullDownRefresh() {
    this.loadAdjustments();
  },

  checkLogin() {
    if (!app.isLoggedIn()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    const user = app.globalData.userInfo || {};
    this.setData({
      userName: user.name || "",
      areaName: user.area_name || "汇聚及核心",
    });
  },

  async loadAdjustments() {
    if (!app.isLoggedIn()) return;
    this.setData({ loading: true });
    try {
      const rows = await getMyAdjustmentTasks();
      const user = app.globalData.userInfo || {};
      const unreadReminders = getUnreadReminders(rows, user.user_id);
      const tasks = rows.map((item) => ({
        id: item.id,
        feedback_note: "",
        new_feedback: {task_id: item.field_target_tasks?.[0]?.id, device_name:"", actual_model:"", asset_barcodes:"", cabinet_barcodes:"", cabinet_no_barcode:false, rack_no:""},
        target_room: item.field_target_tasks?.[0]?.room_name || "",
        needs_new_feedback: !!item.comparison && !(item.field_records || []).length && !!item.field_target_tasks?.length,
        field_records: (item.field_records || []).map(r => ({...r, update_feedback: false})),
        review_note: item.review_note || "",
        status: item.status,
        problem_type: item.problem_type,
        room_name: item.room_name,
        device_name: item.device_name,
        area_name: item.area_name,
        risk_level: item.risk_level,
        basis: item.basis,
        suggestion: item.suggestion,
        priority: item.priority,
        assigned_name: item.assigned_name,
        completed_at: item.completed_at,
        reminder_count: Math.max(Number(item.reminder_count || 0), 0),
        reminded_at: item.reminded_at || "",
        reminder_note: item.reminder_note || "",
        _problemTitle: item.problem_type || "资产整改",
        _roomText: (item.room_name || "多机房") + " · " + (item.area_name || ""),
        _deviceText: item.device_name || "多设备",
        _riskText: item.risk_level || "-",
        _priorityText: item.priority || "-",
        _suggestionText: item.suggestion || "-",
        _basisText: item.basis || "-",
        _statusText: item.status === "done" ? "已闭环" : item.status === "pending_verify" ? "待管理员验收" : item.comparison?.field_done ? "等待管理员处理" : "待整改",
        _statusCls: item.status === "done" ? "tag-success" : item.status === "pending_verify" ? "tag-wait" : "tag-doing",
        _canComplete: item.status === "dispatched" && !item.comparison?.field_done,
        _tipText: item.status === "done" ? "管理员已验收，任务闭环。" : item.comparison?.field_done && item.status === "dispatched" ? "现场结果已提交，等待管理员处理资产或综资。" : "已提交整改结果，等待管理员验收。",
        _hasReminder: Number(item.reminder_count || 0) > 0,
        _reminderSummary: `管理员已催办 ${Math.max(Number(item.reminder_count || 0), 0)} 次 · ${formatReminderTime(item.reminded_at)}`,
        _reminderNote: item.reminder_note || "请尽快核实并完成整改",
      }));
      this.setData({
        tasks,
        totalCount: tasks.length,
        pendingCount: tasks.filter((item) => item._canComplete).length,
        verifyCount: tasks.filter((item) => item.status === "pending_verify").length,
        doneCount: tasks.filter((item) => item.status === "done").length,
        empty: tasks.length === 0,
        loading: false,
      });
      if (unreadReminders.length) {
        const unreadCount = unreadReminders.reduce((total, entry) => total + entry.unreadCount, 0);
        const first = unreadReminders[0].item;
        const content = unreadReminders.length === 1
          ? `整改任务 #${first.id} 收到管理员催办：${first.reminder_note || "请尽快核实并完成整改"}`
          : `收到 ${unreadCount} 条新催办，涉及 ${unreadReminders.length} 个整改任务，请及时查看处理。`;
        wx.showModal({ title: "收到整改催办", content, showCancel: false, confirmText: "知道了" });
        markRemindersSeen(rows, user.user_id);
      }
    } catch (err) {
      this.setData({ loading: false, empty: this.data.tasks.length === 0 });
    } finally {
      wx.stopPullDownRefresh();
    }
  },

  inputNewFeedback(e) {
    const {index, field} = e.currentTarget.dataset;
    this.setData({[`tasks[${index}].new_feedback.${field}`]: e.detail.value});
  },

  inputFeedback(e) {
    const {index} = e.currentTarget.dataset;
    this.setData({[`tasks[${index}].feedback_note`]: e.detail.value});
  },

  toggleCabinetMissing(e) {
    const {index, recordindex} = e.currentTarget.dataset;
    this.setData({[`tasks[${index}].field_records[${recordindex}].cabinet_no_barcode`]: e.detail.value,
      [`tasks[${index}].field_records[${recordindex}].update_feedback`]: true});
  },

  editFeedbackRecord(e) {
    const {index, recordindex, field} = e.currentTarget.dataset;
    this.setData({[`tasks[${index}].field_records[${recordindex}].${field}`]: e.detail.value,
      [`tasks[${index}].field_records[${recordindex}].update_feedback`]: true});
  },

  completeTask(e) {
    const taskId = e.currentTarget.dataset.id;
    const task = this.data.tasks.find(item => item.id === taskId);
    if (!task || !task.feedback_note.trim()) {
      wx.showToast({ title: "请填写实际整改情况", icon: "none" });
      return;
    }
    const records = task.field_records.filter(r => r.update_feedback).map(r => ({record_id:r.id, actual_model:r.actual_model || "", asset_barcodes:r.asset_barcodes || "", cabinet_barcodes:r.cabinet_barcodes || "", cabinet_no_barcode:!!r.cabinet_no_barcode, rack_no:r.rack_no || ""}));
    wx.showModal({
      title: "确认完成",
      content: "确认现场整改已完成，并提交给 PC 管理员验收吗？",
      confirmText: "提交",
      success: async (res) => {
        if (!res.confirm) return;
        wx.showLoading({ title: "提交中" });
        try {
          await completeAdjustmentTask(taskId, task.feedback_note, records, task.needs_new_feedback && task.new_feedback.asset_barcodes.trim() ? task.new_feedback : null);
          wx.hideLoading();
          wx.showToast({ title: "已提交", icon: "success" });
          this.loadAdjustments();
        } catch (err) {
          wx.hideLoading();
        }
      },
    });
  },

  goBack() {
    const pages = getCurrentPages();
    if (pages.length > 1) {
      wx.navigateBack();
      return;
    }
    wx.reLaunch({ url: "/pages/home/home" });
  },
});
