export interface AiCheckSession {
  ok: boolean;
  message: string;
  modelName: string;
  checkedAt: number;
  token: string;
}

const AI_CHECK_KEY = "inventory_ai_check_state";
const AI_CHAT_TASK_IDS_KEY = "inventory_ai_chat_task_ids";
export const AI_CHECK_CHANGED_EVENT = "inventory-ai-check-changed";

export function readAiCheckSession(token: string): AiCheckSession | null {
  if (!token) return null;
  const raw = localStorage.getItem(AI_CHECK_KEY);
  if (!raw) return null;
  try {
    const state = JSON.parse(raw) as AiCheckSession;
    return state.token === token ? state : null;
  } catch {
    return null;
  }
}

export function saveAiCheckSession(token: string, state: Omit<AiCheckSession, "token" | "checkedAt">) {
  if (!token) return;
  localStorage.setItem(AI_CHECK_KEY, JSON.stringify({ ...state, token, checkedAt: Date.now() }));
  window.dispatchEvent(new Event(AI_CHECK_CHANGED_EVENT));
}

export function clearAiSessionState() {
  localStorage.removeItem(AI_CHECK_KEY);
  sessionStorage.removeItem(AI_CHAT_TASK_IDS_KEY);
}

export function setAiChatTaskIds(taskIds: number[]) {
  sessionStorage.setItem(AI_CHAT_TASK_IDS_KEY, JSON.stringify(taskIds.filter((id) => Number.isFinite(id) && id > 0)));
}

export function getAiChatTaskIds(): number[] {
  const raw = sessionStorage.getItem(AI_CHAT_TASK_IDS_KEY);
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value.map(Number).filter((id) => Number.isFinite(id) && id > 0) : [];
  } catch {
    return [];
  }
}
