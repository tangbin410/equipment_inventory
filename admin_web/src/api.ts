import axios, { type AxiosRequestConfig } from "axios";
import { ElMessage } from "element-plus";
import { clearAiSessionState } from "./aiSession";

export const API_BASE = "http://127.0.0.1:8000/api";
const TOKEN_KEY = "inventory_admin_token";
const USER_KEY = "inventory_admin_user";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setSession(token: string, user: unknown) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user || {}));
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
    const detail = error?.response?.data?.detail || error?.message || "请求失败";
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

export async function downloadBlob(url: string, filename: string) {
  const response = await client.get<Blob>(url, { responseType: "blob" });
  const blobUrl = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(blobUrl);
}

export default client;
