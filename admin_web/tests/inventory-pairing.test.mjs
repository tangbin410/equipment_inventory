import assert from "node:assert/strict";
import {test} from "node:test";
import {isPairingRecord, pairingNote, pairingResult, recordDeviceCategory} from "../src/inventoryPairing.ts";
import {inventoryPairingNotices} from "../src/threeWay.ts";

const record = (changes = {}) => ({id: 1, device_name: "设备甲", record_type: "normal", ...changes});
const pairing = (changes = {}) => ({id: 7, role: "merged", cross_room: false,
  source_room_name: "机房甲", target_room_name: "机房甲", note: "网管及序列号核实", status: "已拼销",
  missing: record({record_type: "missing"}), ...changes});

test("设备类别取本次有效值，兼容清单扩展字段，缺失或损坏时保留空值", () => {
  assert.equal(recordDeviceCategory(record({device_category: " OTN ", device_extra: '{"设备类别":"PTN"}'})), "OTN");
  assert.equal(recordDeviceCategory(record({device_extra: '{"设备类别":" SPN "}'})), "SPN");
  assert.equal(recordDeviceCategory(record({device_extra: {网元类型: " PON "}})), "PON");
  for (const device_extra of [undefined, "", "{损坏", "null", {}]) {
    assert.equal(recordDeviceCategory(record({device_extra})), "");
  }
});

test("仅盘亏、盘盈或已有拼销的记录使用拼销入口，正常设备仍修改综资", () => {
  assert.equal(isPairingRecord(record()), false);
  assert.equal(isPairingRecord(record({record_type: "missing"})), true);
  assert.equal(isPairingRecord(record({record_type: "surplus"})), true);
  assert.equal(isPairingRecord(record({pairing: pairing()})), true, "合并后虽为normal，仍应查看拼销记录而不能直接改综资");
});

test("拼销结果区分迁出、实际归属和失效，跨房不能误报整改已完成", () => {
  assert.equal(pairingResult(record()), "");
  assert.equal(pairingResult(record({pairing: pairing()})), "已拼销");
  assert.equal(pairingResult(record({pairing: pairing({cross_room: true})})), "已拼销·待调拨");
  assert.equal(pairingResult(record({pairing: pairing({cross_room: true, role: "outbound"})})), "已拼销迁出");
  assert.equal(pairingResult(record({pairing: pairing({cross_room: true, role: "stale"})})), "拼销待复核");
});

test("备注保留现场记录和确认依据，并标明从原房到实物机房的方向", () => {
  const input = record({notes: "现场标签完整", pairing: pairing({cross_room: true, target_room_name: "机房乙"})});
  const before = structuredClone(input);
  assert.equal(pairingNote(input), "现场标签完整；已拼销·待调拨：机房甲 → 机房乙；网管及序列号核实");
  assert.deepEqual(input, before, "展示不能覆盖原始盘点数据");
  assert.equal(pairingNote(record({notes: "原备注"})), "原备注");
});

test("同时选中原房和实物房，拼销提示按ID只显示一次且保留待调拨", () => {
  const input = [pairing({cross_room: true, role: "outbound", target_room_name: "机房乙"}),
    pairing({cross_room: true, target_room_name: "机房乙"}), pairing({id: 8})];
  const before = structuredClone(input);
  const notices = inventoryPairingNotices(input);
  assert.equal(notices.length, 2);
  assert.equal(notices[0].title, "拼销：设备甲由机房甲迁出，归机房乙；待跨机房调拨。");
  assert.equal(notices[1].title, "拼销：设备甲已在机房甲合并；三方资料继续核对。");
  assert.deepEqual(input, before);
  assert.deepEqual(inventoryPairingNotices(), []);
});

test("拼销来源失效时优先要求重新核实，不能继续显示已迁出或已合并", () => {
  const stale = pairing({role: "stale", cross_room: true, target_room_name: "机房乙"});
  for (const input of [[stale, pairing()], [pairing(), stale]]) {
    const [notice] = inventoryPairingNotices(input);
    assert.equal(notice.stale, true);
    assert.match(notice.title, /待重新核实.*设备甲.*盘点数据/);
    assert.ok(!notice.title.includes("待跨机房调拨"));
  }
});
