import { afterEach, describe, expect, it, vi } from "vitest";
import { clearReadCache } from "./readCache";
import { adminFetch, currentSession, login, logout, restoreSession, SESSION_EXPIRED_EVENT } from "./session";
import { updateRuntimeConfig, updateScenarioSkill, deleteScenarioSkill } from "./api";

const session = { authenticated: true, csrf_token: "synthetic-csrf", expires_at: "2099-01-01T00:00:00Z", environment: "preview" };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { clearReadCache(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("administrator cookie session transport", () => {
  it("sends the password only to login and keeps credentials out of browser storage", async () => {
    localStorage.clear();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(login("synthetic-password")).resolves.toEqual(session);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/admin/auth/login");
    expect(options.credentials).toBe("same-origin");
    expect(JSON.parse(options.body)).toEqual({ password: "synthetic-password" });
    expect(new Headers(options.headers).has("Authorization")).toBe(false);
    expect(new Headers(options.headers).has("X-CSRF-Token")).toBe(false);
    expect(localStorage.length).toBe(0);
    await adminFetch("/config", { method: "PUT", body: JSON.stringify({ temperature: 0.3 }) });
    const headers = new Headers(fetchMock.mock.calls[1][1].headers);
    expect(headers.get("X-CSRF-Token")).toBe("synthetic-csrf");
    expect(headers.has("Authorization")).toBe(false);
    expect(headers.has("Cookie")).toBe(false);
    expect(JSON.stringify(fetchMock.mock.calls[1])).not.toContain("synthetic-password");
  });
  it("checks the backend session on reload and expires the whole admin UI on 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({ detail: "Expired" }, 401)));
    const expired = vi.fn(); window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    try {
      await restoreSession();
      await expect(adminFetch("/config")).rejects.toMatchObject({ status: 401 });
      expect(currentSession()).toBeNull(); expect(expired).toHaveBeenCalledOnce();
    } finally { window.removeEventListener(SESSION_EXPIRED_EVENT, expired); }
  });
  it("revokes through authenticated logout and refuses later mutations before transport", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(session)).mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await login("password"); await logout();
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/admin/auth/logout");
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("X-CSRF-Token")).toBe(session.csrf_token);
    expect(currentSession()).toBeNull();
    await expect(adminFetch("/config", { method: "PUT", body: "{}" })).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("binds settings mutations to the loaded revisions without leaking revisions into overrides", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ revision: "old-config-revision" })).mockResolvedValueOnce(json({}))
      .mockResolvedValueOnce(json({ revision: 7, skills: [] })).mockResolvedValueOnce(json({}))
      .mockResolvedValueOnce(json({ revision: 8, skills: [] })).mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await updateRuntimeConfig("csrf", { revision: "old-config-revision", temperature: 0.5 });
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("If-Match")).toBe("old-config-revision");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ temperature: 0.5 });
    await updateScenarioSkill("csrf", "new_skill", { name: "Skill", instruction: "Text", trigger_keywords: [], actions: [], enabled: true, priority: 50, catalog_revision: 7 });
    expect(new Headers(fetchMock.mock.calls[3][1].headers).get("If-Match")).toBe("7");
    expect(JSON.parse(fetchMock.mock.calls[3][1].body)).not.toHaveProperty("catalog_revision");
    await deleteScenarioSkill("csrf", "new_skill", 8);
    expect(new Headers(fetchMock.mock.calls[5][1].headers).get("If-Match")).toBe("8");
  });
});

it("caches repeated list reads but refreshes details and clears cached content on logout", async () => {
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/auth/login')) return json(session);
    if (url.endsWith('/auth/logout')) return new Response(null, { status: 204 });
    return json({ items: [], source_id: 'fixture' });
  });
  vi.stubGlobal('fetch', fetchMock);
  await login('fixture');
  await adminFetch('/corpus/sources?limit=50'); await adminFetch('/corpus/sources?limit=50');
  expect(fetchMock.mock.calls.filter(([url]) => url.includes('/corpus/sources?'))).toHaveLength(1);
  await adminFetch('/corpus/sources/fixture'); await adminFetch('/corpus/sources/fixture');
  expect(fetchMock.mock.calls.filter(([url]) => url.includes('/corpus/sources/fixture'))).toHaveLength(2);
  expect(JSON.stringify(sessionStorage)).not.toContain(session.csrf_token);
  await logout(); expect(sessionStorage.getItem('rag-admin-read-cache-v1')).toBeNull();
});

it("refreshes revisions before a mutation and stops conflicting writes", async () => {
  const fetchMock = vi.fn().mockResolvedValue(json({ revision: 'changed' }));
  vi.stubGlobal('fetch', fetchMock);
  await expect(updateRuntimeConfig('csrf', { revision: 'stale', temperature: 0.5 })).rejects.toMatchObject({ status: 409 });
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls[0][1].method).toBe('GET');
});
