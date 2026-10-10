import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserMaskingRuntime } from "./runtime";
import type { WorkerCommand, WorkerEvent } from "./types";

class FakeWorker {
  static instances: FakeWorker[] = [];
  onmessage: ((event: MessageEvent<WorkerEvent>) => void) | null = null;
  onerror: (() => void) | null = null;
  postMessage = vi.fn<(command: WorkerCommand) => void>();
  terminate = vi.fn();
  constructor() { FakeWorker.instances.push(this); }
  emit(event: WorkerEvent) { this.onmessage?.({ data: event } as MessageEvent<WorkerEvent>); }
}
beforeEach(() => { FakeWorker.instances = []; vi.stubGlobal("Worker", FakeWorker); vi.stubGlobal("fetch", vi.fn()); vi.useFakeTimers(); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("browser runtime failure boundary", () => {
  it("keeps extraction text on the worker channel and forwards progress", async () => {
    const runtime = new BrowserMaskingRuntime();
    const progress = vi.fn();
    const pending = runtime.load(progress);
    const worker = FakeWorker.instances[0];
    worker.emit({ id: 1, kind: "progress", progress: 0.5, text: "loading" });
    expect(progress).toHaveBeenCalledWith(0.5, "loading");
    worker.emit({ id: 1, kind: "loaded", metrics: { elapsedMs: 1, fetchedBytes: 1, fetches: 1, failedFetches: 0, adapter: {} } });
    await pending;
    const detection = runtime.detect("synthetic-private-sentinel");
    expect(worker.postMessage).toHaveBeenLastCalledWith({ id: 2, operation: "extract", text: "synthetic-private-sentinel" });
    worker.emit({ id: 2, kind: "detected", detection: { entities: [], chunks: 1, elapsedMs: 10 } });
    await detection;
    expect(fetch).not.toHaveBeenCalled();
    runtime.dispose();
  });
  it("rejects failed checks without network fallback", async () => {
    const runtime = new BrowserMaskingRuntime();
    const result = runtime.detect("synthetic-only");
    const assertion = expect(result).rejects.toMatchObject({ code: "model_execution_failed" });
    FakeWorker.instances[0].emit({ id: 1, kind: "error", code: "model_execution_failed" });
    await assertion;
    expect(fetch).not.toHaveBeenCalled(); runtime.dispose();
  });
  it("terminates a timed-out worker and ignores its late output", async () => {
    const runtime = new BrowserMaskingRuntime();
    const pending = runtime.detect("synthetic-only");
    const assertion = expect(pending).rejects.toMatchObject({ code: "model_timeout" });
    await vi.advanceTimersByTimeAsync(30_000);
    await assertion;
    expect(FakeWorker.instances[0].terminate).toHaveBeenCalledOnce();
    FakeWorker.instances[0].emit({ id: 1, kind: "detected", detection: { entities: [], elapsedMs: 1, chunks: 1 } });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("cancels loading immediately and rejects overlapping work", async () => {
    const runtime = new BrowserMaskingRuntime();
    const pending = runtime.load();
    const assertion = expect(pending).rejects.toMatchObject({ code: "cancelled" });
    await expect(runtime.detect("synthetic-only")).rejects.toMatchObject({ code: "busy" });
    runtime.dispose(); await assertion;
    expect(FakeWorker.instances[0].terminate).toHaveBeenCalledOnce();
    expect(fetch).not.toHaveBeenCalled();
  });
});
