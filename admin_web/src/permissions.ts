import { computed, ref } from "vue";
import type { AdminUser } from "./types";

export const sessionUser = ref<AdminUser | null>(null);

export function setPermissionUser(user: AdminUser | null) {
  sessionUser.value = user;
}

export const permissions = computed(() => {
  const admin = sessionUser.value?.role === "admin";
  const scope = sessionUser.value?.admin_scope;
  const full = admin && (!scope || scope === "full");
  const canCorrect = full || admin && scope === "collaborator";
  return {
    manageAccounts: full,
    manageTasks: full,
    manageAssets: full,
    manageQr: full,
    writeGovernance: full,
    runAi: full,
    configureAi: full,
    correctData: canCorrect,
    judgeData: canCorrect,
    updateBaseline: canCorrect,
  };
});

export function adminRoleText(user: Pick<AdminUser, "role" | "admin_scope">) {
  if (user.role !== "admin") return "现场人员";
  if (user.admin_scope === "collaborator") return "协作管理员";
  return !user.admin_scope || user.admin_scope === "full" ? "完整管理员" : "权限待确认";
}
