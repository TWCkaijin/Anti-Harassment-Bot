import { ApiError, normalizeApiErrorDetail, requestGuard } from "@rag/shared/api";

export interface AdminSession {
  authenticated: true;
  csrf_token: string;
  expires_at: string;
  environment: string;
}

export const SESSION_EXPIRED_EVENT = "admin-session-expired";
const API_BASE = import.meta.env.VITE_ADMIN_API_BASE_URL ?? "/api/v1/admin";
let activeSession: AdminSession | null = null;

export class AdminRequestError extends ApiError {
  readonly retryAfterSeconds?: number;
  constructor(status: number, detail?: string, retryAfterSeconds?: number) {
    super(status, `管理請求失敗（${status}）`, detail);
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function currentSession(): AdminSession | null { return activeSession; }

function acceptSession(value: unknown): AdminSession {
  if (typeof value !== "object" || value === null || !("authenticated" in value)
    || value.authenticated !== true || !("csrf_token" in value) || typeof value.csrf_token !== "string"
    || !value.csrf_token || !("expires_at" in value) || typeof value.expires_at !== "string"
    || !("environment" in value) || typeof value.environment !== "string") {
    throw new ApiError(502, "管理員登入回應格式不正確");
  }
  activeSession = value as AdminSession;
  return activeSession;
}

/** Only the browser manages the HttpOnly session cookie. Passwords and sessions are never persisted in JS storage. */
export async function adminFetch<T>(path: string, options: RequestInit = {}, csrfToken = activeSession?.csrf_token): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  if (url.origin !== window.location.origin) throw new ApiError(503, "管理 API 必須使用本站的安全連線");
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && path !== "/auth/login") {
    if (!csrfToken) throw new ApiError(401, "請重新登入管理員");
    headers.set("X-CSRF-Token", csrfToken);
  }
  const guard = requestGuard(55_000, "管理請求逾時；操作可能已被接收，請先重新整理狀態。", options.signal ?? undefined);
  try {
  const response = await fetch(`${url.pathname}${url.search}`, {
    ...options, method, headers, credentials: "same-origin", cache: "no-store", signal: guard.signal,
  });
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = await response.json();
      const summary = normalizeApiErrorDetail(body.detail);
      const errors = normalizeApiErrorDetail(body.errors);
      detail = errors ? [summary, errors].filter(Boolean).join(": ") : summary;
    } catch { /* Keep transport errors free of HTML/provider bodies. */ }
    if (response.status === 401 && path !== "/auth/login") {
      activeSession = null;
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    const retryAfter = Number(response.headers.get("Retry-After"));
    throw new AdminRequestError(response.status, detail, retryAfter > 0 && Number.isFinite(retryAfter) ? Math.min(retryAfter, 300) : undefined);
  }
  if (response.status === 204) return undefined as T;
  if (response.headers.get("Content-Disposition")?.includes("attachment")) return await response.blob() as T;
  return await response.json() as T;
  } catch (error) {
    if (guard.signal.aborted) throw guard.signal.reason;
    throw error;
  } finally { guard.dispose(); }
}

export async function login(password: string): Promise<AdminSession> {
  return acceptSession(await adminFetch("/auth/login", { method: "POST", body: JSON.stringify({ password }) }));
}
export async function restoreSession(): Promise<AdminSession> {
  return acceptSession(await adminFetch("/auth/session"));
}
export async function logout(): Promise<void> {
  await adminFetch<void>("/auth/logout", { method: "POST" });
  activeSession = null;
}
