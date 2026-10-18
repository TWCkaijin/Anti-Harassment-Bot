import { GENERATION_TIMEOUT_MS } from "./config";
import { MaskingError } from "./extraction";
import type { Detection, LoadMetrics, WorkerCommand, WorkerEvent } from "./types";

export class BrowserMaskingRuntime {
  private worker: Worker | null = null;
  private sequence = 0;
  private pending: { id: number; resolve: (event: WorkerEvent) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout>; progress?: (fraction: number, text: string) => void } | null = null;

  private request(operation: "load" | "extract", text?: string, progress?: (fraction: number, text: string) => void): Promise<WorkerEvent> {
    if (this.pending) return Promise.reject(new MaskingError("busy"));
    if (!this.worker) {
      this.worker = new Worker(new URL("./runtime.worker.ts", import.meta.url), { type: "module" });
      this.worker.onmessage = (event: MessageEvent<WorkerEvent>) => {
        const message = event.data, pending = this.pending;
        if (!pending || message.id !== pending.id) return;
        if (message.kind === "progress") { pending.progress?.(message.progress, message.text); return; }
        clearTimeout(pending.timer); this.pending = null;
        if (message.kind === "error") {
          const failure = new MaskingError(message.code); failure.rounds = message.rounds ?? [];
          pending.reject(failure);
        }
        else pending.resolve(message);
      };
      this.worker.onerror = () => this.dispose("worker_failed");
    }
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => this.dispose("model_timeout"), operation === "load" ? 600_000 : GENERATION_TIMEOUT_MS);
      this.pending = { id, resolve, reject, timer, progress };
      const command: WorkerCommand = operation === "load" ? { id, operation } : { id, operation, text: text ?? "" };
      this.worker!.postMessage(command);
    });
  }

  async load(progress?: (fraction: number, text: string) => void): Promise<LoadMetrics> {
    const response = await this.request("load", undefined, progress);
    if (response.kind !== "loaded") throw new MaskingError("invalid_worker_response");
    return response.metrics;
  }
  async detect(text: string): Promise<Detection> {
    const response = await this.request("extract", text);
    if (response.kind !== "detected") throw new MaskingError("invalid_worker_response");
    return response.detection;
  }
  dispose(code = "cancelled") {
    this.worker?.terminate(); this.worker = null;
    if (this.pending) { clearTimeout(this.pending.timer); this.pending.reject(new MaskingError(code)); this.pending = null; }
  }
}
