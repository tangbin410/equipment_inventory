import assert from "node:assert/strict";
import { test } from "node:test";
import { comparisonTaskTime, latestCompletedRooms, queryCompletedRooms } from "../src/comparisonSelection.ts";

const task = (id, room_name, changes = {}) => ({ id, room_name, room_code: `00${id}`, import_batch_id: `batch-${id}`,
  status: "done", area_name: "南区", assigned_name: "张三", completed_at: "2026-10-01T10:00:00", ...changes });

test("仅保留每个机房最近一次已完成记录，不让新未完成任务遮住已完成结果", () => {
  const rows = [task(1, "机房甲"), task(2, "机房甲", { completed_at: "2026-11-01T10:00:00" }),
    task(3, "机房甲", { status: "doing", completed_at: "2026-11-02T10:00:00" }),
    task(4, "机房乙", { status: "pending" }), task(5, "机房乙"), task(6, "机房丙", { status: "void" })];
  assert.deepEqual(latestCompletedRooms(rows).map(row => row.id), [2, 5]);
  assert.equal(rows.length, 6, "不得更改任务源列表");
});

test("同名机房地点编码不同也仅保留最新完成记录；同时间按ID兜底", () => {
  assert.deepEqual(latestCompletedRooms([task(1, "机房甲"), task(8, "机房甲")]).map(row => row.id), [8]);
  assert.equal(comparisonTaskTime(task(1, "机房甲", { field_completed_at: "2026-11-01T12:00:00" })), "2026-11-01T12:00:00");
  assert.deepEqual(latestCompletedRooms([task(1, "机房甲", { completed_at: "", created_at: "2026-11-02T10:00:00" }),
    task(8, "机房甲", { completed_at: "", created_at: "2026-11-01T10:00:00" })]).map(row => row.id), [1]);
});

test("机房与领取人模糊查询始终针对最新已完成记录，不返回旧领取人的历史盘点", () => {
  const rows = [task(1, "全球通机房", { assigned_name: "张三" }),
    task(2, "全球通机房", { assigned_name: "李四", completed_at: "2026-11-01T10:00:00" }), task(3, "唐家机房")];
  assert.deepEqual(queryCompletedRooms(rows, "room_name", " 全球通 ").map(row => row.id), [2]);
  assert.deepEqual(queryCompletedRooms(rows, "assigned_name", "张").map(row => row.id), [3]);
  assert.deepEqual(queryCompletedRooms(rows, "all", "忽略内容").map(row => row.id), [2, 3]);
  assert.deepEqual(queryCompletedRooms(rows, "assigned_name", "无人"), []);
});
