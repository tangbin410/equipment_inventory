import assert from "node:assert/strict";
import { test } from "node:test";
import { comparisonIssues } from "../src/comparisonIssues.ts";

const row = (system, changes = {}) => ({ system, name: "设备甲", barcode: "A1", rack_no: "R1",
  model: "M1", location_code: "L1", ...changes });
const checks = () => ({
  综资现场: { 资产条码: "一致", 机架号: "一致", 设备型号: "一致" },
  综资资产: { 资源名称: "一致", 资产条码: "一致", 规格型号: "一致", 地点编码: "一致" },
  现场资产: { 资产条码: "一致", 地点编码: "一致" },
});
const group = (changes = {}) => ({
  group_key: "test", device_name: "设备甲", room_name: "机房甲", barcode: "A1", manual: false,
  sources: { 综资: [row("综资")], 现场: [row("现场")], 资产: [row("资产")] },
  pending_sources: { 综资: [], 现场: [], 资产: [] },
  checks: checks(), problems: [], consistent: false, ...changes,
});
const titles = (issues) => issues.map(issue => issue.title);

test("现场无条码且资产记录缺失时只列根因和可核对差异，不重复列三组待处理", () => {
  const input = group({
    sources: { 综资: [row("综资", { barcode: "TEST-ASSET-001", rack_no: "AM02B" })], 现场: [], 资产: [] },
    pending_sources: { 综资: [], 现场: [row("现场", { barcode: "", barcode_state: "现场无条码", rack_no: "AM02" })], 资产: [] },
    checks: {
      综资现场: { 资产条码: "待处理", 机架号: "不一致", 设备型号: "待判断" },
      综资资产: { 资源名称: "待处理", 资产条码: "待处理", 规格型号: "待判断", 地点编码: "待处理" },
      现场资产: { 资产条码: "待处理", 地点编码: "待处理" },
    },
    problems: ["资产表未找到对应资源名称", "现场已有记录，但登记为无资产条码，待管理员补录",
      "综资现场资产条码待处理：资产条码原值为 TEST-ASSET-001 / 未填写",
      "综资现场机架号不一致：机架号原值为 AM02B / AM02",
      "综资资产资产条码待处理：资产条码原值为 TEST-ASSET-001 / 未填写",
      "综资资产地点编码待处理：地点编码原值为 L1 / 未填写",
      "现场资产资产条码待处理：资产条码原值为 未填写 / 未填写",
      "现场资产地点编码待处理：地点编码原值为 L1 / 未填写",
      "综资现场设备型号待判断（管理员判断）", "综资资产规格型号待判断（管理员判断）"],
  });
  const issues = comparisonIssues(input);
  assert.deepEqual(titles(issues), ["资产表未找到对应资源名称", "现场无资产条码", "现场机柜位不一致", "现场设备型号待判断"]);
  assert.equal(issues.find(issue => issue.title === "现场机柜位不一致").detail, "综资：AM02B；现场：AM02");
  assert.ok(!JSON.stringify(issues).includes("原值为"));
});

test("三方条码不同合并为一个问题，仍保留三个系统的值", () => {
  const input = group();
  input.sources.综资[0].barcode = "CODE-A";
  input.sources.现场[0].barcode = "CODE-B";
  input.sources.资产[0].barcode = "CODE-C";
  for (const pair of Object.values(input.checks)) pair.资产条码 = "不一致";
  input.problems = ["综资现场资产条码不一致：旧的长描述", "综资资产资产条码不一致：旧的长描述", "现场资产资产条码不一致：旧的长描述"];
  const issues = comparisonIssues(input);
  assert.deepEqual(titles(issues), ["资产条码不一致"]);
  assert.equal(issues[0].detail, "综资：CODE-A；现场：CODE-B；资产：CODE-C");
});

test("两处型号判断分别保留，不能把待判断和不一致合并", () => {
  const input = group();
  input.sources.现场[0].model = "现场型号";
  input.sources.资产[0].model = "资产型号";
  input.checks.综资现场.设备型号 = "待判断";
  input.checks.综资资产.规格型号 = "不一致";
  const issues = comparisonIssues(input);
  assert.deepEqual(titles(issues), ["现场设备型号待判断", "资产表规格型号不一致"]);
  assert.match(issues[0].detail, /综资：M1；现场：现场型号/);
  assert.match(issues[1].detail, /综资：M1；资产：资产型号/);
});

test("现场整条反馈缺失时只报缺反馈，不制造多个空字段或型号问题", () => {
  const input = group();
  input.sources.现场 = [];
  input.checks.综资现场 = { 资产条码: "待处理", 机架号: "待处理", 设备型号: "待判断" };
  input.checks.现场资产 = { 资产条码: "待处理", 地点编码: "待处理" };
  input.problems = ["现场未找到对应网元反馈", "综资现场资产条码待处理：字段为空", "现场资产地点编码待处理：字段为空"];
  assert.deepEqual(titles(comparisonIssues(input)), ["现场未找到对应网元反馈"]);
});

test("现场盘亏按已登记盘亏展示，不误写为没有反馈", () => {
  const input = group({ stage_one_status: "现场登记盘亏" });
  input.sources.现场 = [];
  input.checks.综资现场 = { 资产条码: "待处理", 机架号: "待处理", 设备型号: "待判断" };
  input.checks.现场资产 = { 资产条码: "待处理", 地点编码: "待处理" };
  assert.deepEqual(titles(comparisonIssues(input)), ["现场登记盘亏"]);
});

test("同一现场空条码导致的两组待处理只提示一次未填写", () => {
  const input = group();
  input.sources.现场[0].barcode = "";
  input.checks.综资现场.资产条码 = "待处理";
  input.checks.现场资产.资产条码 = "待处理";
  assert.deepEqual(titles(comparisonIssues(input)), ["现场资产条码未填写"]);
});

test("人工核对填写值覆盖原值，问题和证据依据当前判断", () => {
  const input = group({ manual: true, manual_decision: {
    values: { 综资: row("综资", { barcode: "CORRECTED-A" }), 现场: row("现场", { barcode: "CORRECTED-B" }), 资产: row("资产", { barcode: "CORRECTED-A" }) },
    note: "人工核对",
  } });
  input.sources.综资[0].barcode = "OLD-A";
  input.sources.现场[0].barcode = "OLD-B";
  input.checks.综资现场.资产条码 = "不一致";
  input.checks.现场资产.资产条码 = "不一致";
  const issues = comparisonIssues(input);
  assert.equal(issues.length, 1);
  assert.match(issues[0].title, /资产条码不一致/);
  assert.match(issues[0].detail, /CORRECTED-A/);
  assert.match(issues[0].detail, /CORRECTED-B/);
  assert.ok(!JSON.stringify(issues).includes("OLD-"), "不能展示已覆盖的原值");
});

test("未人工核对的多条码记录不擅自取第一条生成差异证据", () => {
  const input = group({ manual: true, problems: ["多条码设备待人工核对"] });
  input.sources.资产.push(row("资产", { barcode: "A2" }));
  for (const pair of Object.values(input.checks)) for (const key of Object.keys(pair)) pair[key] = "待判断";
  assert.deepEqual(comparisonIssues(input), [{ title: "多条码设备待人工核对", detail: "" }]);
});

test("人工核对表单不包含机柜位时仍沿用原采集值，不误报空字段", () => {
  const values = Object.fromEntries(["综资", "现场", "资产"].map(system => [system,
    { name: "设备甲", barcode: "核对后条码", model: "核对后型号", location_code: "L1" }]));
  const input = group({ manual: true, manual_decision: { values, note: "人工核对" } });
  input.sources.综资[0].rack_no = "R1";
  input.sources.现场[0].rack_no = "R2";
  input.checks.综资现场.机架号 = "不一致";
  const issues = comparisonIssues(input);
  assert.deepEqual(titles(issues), ["现场机柜位不一致"]);
  assert.equal(issues[0].detail, "综资：R1；现场：R2");
});

test("未知关联问题保留并去重，一致记录不再显示旧问题", () => {
  const input = group({ problems: ["同一网元多条现场反馈，待管理员关联", "同一网元多条现场反馈，待管理员关联"] });
  assert.deepEqual(comparisonIssues(input), [{ title: input.problems[0], detail: "" }]);
  input.consistent = true;
  assert.deepEqual(comparisonIssues(input), []);
});

test("生成简短文案不修改原始来源、判断或问题记录", () => {
  const input = group();
  input.checks.综资现场.机架号 = "不一致";
  const before = structuredClone(input);
  const freeze = (value) => {
    if (value && typeof value === "object") {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  };
  comparisonIssues(freeze(input));
  assert.deepEqual(input, before);
});
