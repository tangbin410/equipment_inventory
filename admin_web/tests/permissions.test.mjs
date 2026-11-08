import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { adminRoleText, permissions, setPermissionUser } from "../src/permissions.ts";

const admin = { id: 1, wx_openid: "test_admin", name: "测试账号", role: "admin", area_name: "北区", is_active: 1 };
afterEach(() => setPermissionUser(null));

test("未校验的会话和现场人员不开放管理操作", () => {
  setPermissionUser(null);
  assert.ok(Object.values(permissions.value).every(value => !value));
  setPermissionUser({ ...admin, role: "worker", admin_scope: "full" });
  assert.ok(Object.values(permissions.value).every(value => !value));
});

test("旧管理员缺少范围字段时兼容完整管理员", () => {
  setPermissionUser(admin);
  assert.ok(Object.values(permissions.value).every(Boolean));
  assert.equal(adminRoleText(admin), "完整管理员");
});

test("协作管理员仅开放盘点修正、比对判断和基线维护", () => {
  setPermissionUser({ ...admin, admin_scope: "full" });
  assert.equal(permissions.value.manageAccounts, true);
  setPermissionUser({ ...admin, admin_scope: "collaborator" });
  const allowed = new Set(["correctData", "judgeData", "updateBaseline"]);
  for (const [action, value] of Object.entries(permissions.value)) assert.equal(value, allowed.has(action), action);
  assert.equal(adminRoleText({ ...admin, admin_scope: "collaborator" }), "协作管理员");
});

test("未知权限范围不开放完整管理员功能", () => {
  setPermissionUser({ ...admin, admin_scope: "unknown" });
  assert.ok(Object.values(permissions.value).every(value => !value));
  assert.equal(adminRoleText({ ...admin, admin_scope: "unknown" }), "权限待确认");
});
