import { afterEach, describe, expect, it, vi } from "vitest";
import { activateReadCache, cachedRead, clearReadCache, invalidateReadCache } from "./readCache";

afterEach(() => { clearReadCache(); sessionStorage.clear(); vi.useRealTimers(); });

describe("session-scoped browser read cache", () => {
  it("reuses downloads across pages and reloads, returning isolated copies", async () => {
    activateReadCache("dev-session-hash");
    const load = vi.fn().mockResolvedValue({ items: [{ title: "original" }] });
    const first = await cachedRead<{ items: { title: string }[] }>("/corpus/sources", load);
    first.items[0].title = "draft";
    expect((await cachedRead<typeof first>("/corpus/sources", load)).items[0].title).toBe("original");
    expect(load).toHaveBeenCalledOnce();
    vi.resetModules();
    const reloaded = await import("./readCache");
    reloaded.activateReadCache("dev-session-hash");
    expect(await reloaded.cachedRead("/corpus/sources", load)).toEqual({ items: [{ title: "original" }] });
    expect(load).toHaveBeenCalledOnce();
    reloaded.clearReadCache();
  });
  it("deduplicates parallel reads without one view aborting another", async () => {
    activateReadCache("dev");
    let complete!: (value: unknown) => void;
    const load = vi.fn(() => new Promise(resolve => { complete = resolve; }));
    const abort = new AbortController();
    const first = cachedRead("/config", load, { signal: abort.signal });
    const second = cachedRead("/config", load);
    abort.abort();
    await expect(first).rejects.toMatchObject({ name: "AbortError" });
    complete({ model: "test" });
    await expect(second).resolves.toEqual({ model: "test" });
    expect(load).toHaveBeenCalledOnce();
  });
  it("isolates sessions and environments and drops cached data after logout", async () => {
    const load = vi.fn().mockResolvedValueOnce("dev").mockResolvedValueOnce("prod").mockResolvedValueOnce("new-dev");
    activateReadCache("dev-first"); expect(await cachedRead("/config", load)).toBe("dev");
    activateReadCache("production"); expect(await cachedRead("/config", load)).toBe("prod");
    clearReadCache(); expect(sessionStorage.length).toBe(0);
    activateReadCache("dev-second"); expect(await cachedRead("/config", load)).toBe("new-dev");
    expect(load).toHaveBeenCalledTimes(3);
  });
  it("refreshes explicitly and after TTL, without caching failures", async () => {
    vi.useFakeTimers();
    activateReadCache("dev");
    const load = vi.fn().mockResolvedValueOnce(1).mockResolvedValueOnce(2).mockRejectedValueOnce(new Error("outage")).mockResolvedValueOnce(3);
    expect(await cachedRead("/config", load)).toBe(1);
    expect(await cachedRead("/config", load, { fresh: true })).toBe(2);
    vi.advanceTimersByTime(10 * 60_000 + 1);
    await expect(cachedRead("/config", load)).rejects.toThrow("outage");
    expect(await cachedRead("/config", load)).toBe(3);
  });
  it("does not resurrect stale responses after a mutation or session switch", async () => {
    activateReadCache("dev");
    let complete!: (value: string) => void;
    const old = cachedRead("/config", () => new Promise<string>(resolve => { complete = resolve; }));
    invalidateReadCache("/config");
    expect(await cachedRead("/config", async () => "new")).toBe("new");
    complete("old"); await old;
    expect(await cachedRead("/config", async () => "unexpected")).toBe("new");
    const departing = cachedRead("/corpus/sources", () => new Promise<string>(resolve => { complete = resolve; }));
    activateReadCache("prod"); complete("private-dev-data");
    await expect(departing).rejects.toMatchObject({ name: "AbortError" });
  });
});
