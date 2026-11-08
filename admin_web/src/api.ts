import axios, { type AxiosRequestConfig } from "axios";
import { ElMessage } from "element-plus";
import { clearAiSessionState } from "./aiSession";
import { API_BASE } from "./config";
import { setPermissionUser } from "./permissions";
import type { AdminUser } from "./types";

const TOKEN_KEY = "inventory_admin_token";
const USER_KEY = "inventory_admin_user";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setSession(token: string, user: unknown) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user || {}));
  setPermissionUser(user as AdminUser);
}

export function getSessionUser<T = unknown>(): T | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  clearAiSessionState();
  setPermissionUser(null);
}

export async function refreshSessionUser(): Promise<AdminUser> {
  const user = await apiGet<AdminUser>("/admin/me");
  setSession(getToken(), user);
  return user;
}

const client = axios.create({
  baseURL: API_BASE,
  timeout: 10000,
});

client.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

client.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = readableErrorDetail(error?.response?.data?.detail, error?.message || "请求失败");
    if (error?.response?.status === 401) {
      clearSession();
      ElMessage.error(String(detail));
      window.setTimeout(() => window.location.reload(), 600);
    } else {
      ElMessage.error(String(detail));
    }
    return Promise.reject(error);
  },
);

function readableErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return fallback;
  const messages = detail.flatMap((issue) => {
    if (!issue || typeof issue !== "object" || typeof issue.msg !== "string") return [];
    const location = Array.isArray(issue.loc)
      ? issue.loc.filter((part: unknown) => (typeof part === "string" || typeof part === "number") && part !== "body").join(".")
      : "";
    return [location ? `${location}：${issue.msg}` : issue.msg];
  });
  return messages.join("；") || fallback;
}

export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await client.get<T>(url, config);
  return response.data;
}

export async function apiPost<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const response = await client.post<T>(url, data, config);
  return response.data;
}

export async function apiPatch<T>(url: string, data?: unknown): Promise<T> {
  const response = await client.patch<T>(url, data);
  return response.data;
}

export async function apiDelete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await client.delete<T>(url, config);
  return response.data;
}

export async function downloadBlob(url: string, filename: string, config?: AxiosRequestConfig) {
  const response = await client.get<Blob>(url, { ...config, responseType: "blob" });
  const blobUrl = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(blobUrl);
}

export default client;
