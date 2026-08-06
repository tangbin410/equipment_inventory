const STORAGE_PREFIX = "adjustmentReminderSeen:";

function storageKey(userId) {
  return STORAGE_PREFIX + String(userId || "unknown");
}

function seenCounts(userId) {
  const value = wx.getStorageSync(storageKey(userId));
  return value && typeof value === "object" ? value : {};
}

function getUnreadReminders(rows, userId) {
  const seen = seenCounts(userId);
  return (rows || []).map((item) => {
    const currentCount = Math.max(Number(item.reminder_count || 0), 0);
    const seenCount = Math.max(Number(seen[item.id] || 0), 0);
    return { item, unreadCount: Math.max(currentCount - seenCount, 0) };
  }).filter((entry) => entry.unreadCount > 0);
}

function markRemindersSeen(rows, userId) {
  const seen = seenCounts(userId);
  (rows || []).forEach((item) => {
    const currentCount = Math.max(Number(item.reminder_count || 0), 0);
    if (currentCount > 0) seen[item.id] = currentCount;
  });
  wx.setStorageSync(storageKey(userId), seen);
}

module.exports = { getUnreadReminders, markRemindersSeen };
