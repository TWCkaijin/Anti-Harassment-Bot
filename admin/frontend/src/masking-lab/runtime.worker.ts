import type { MLCEngine } from "@mlc-ai/web-llm";
import { CONTEXT_WINDOW, LIBRARY_URL, MODEL_ID, MODEL_REVISION, modelUrl, isModelAssetUrl, type Entity } from "./config";
import { MaskingError, splitText } from "./extraction";
import { extractInStages, type ExtractionRound } from "./pipeline";
import type { WorkerCommand, WorkerEvent } from "./types";

const send = (event: WorkerEvent) => self.postMessage(event);
let engine: MLCEngine | null = null;
let busy = false;
let activeRevision = MODEL_REVISION;
let forceDownload = false;
let fetchedBytes = 0, fetches = 0, failedFetches = 0;
const measurements: Promise<void>[] = [];
const originalFetch = self.fetch.bind(self);

// Model input never enters a network request. Measure response payload bytes only;
// this deliberately excludes TLS/HTTP overhead and reports IndexedDB hits as 0.
self.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const method = init?.method ?? (input instanceof Request ? input.method : "GET");
  if (method !== "GET" || init?.body || !isModelAssetUrl(url, activeRevision)) throw new MaskingError("network_request_blocked");
  fetches += 1;
  try {
    const response = await originalFetch(input, { ...init, ...(forceDownload ? { cache: "reload" as const } : {}), credentials: "omit", referrerPolicy: "no-referrer" });
    if (!response.ok) { failedFetches += 1; return response; }
    measurements.push(response.clone().arrayBuffer().then(buffer => { fetchedBytes += buffer.byteLength; }).catch(() => { failedFetches += 1; }));
    return response;
  } catch (error) { failedFetches += 1; throw error; }
};

async function execute(command: WorkerCommand) {
  if (busy) { send({ id: command.id, kind: "error", code: "busy" }); return; }
  busy = true;
  const rounds: ExtractionRound[] = [];
  try {
    if (command.operation === "load") {
      if (engine) { await engine.unload(); engine = null; }
      activeRevision = command.revision ?? MODEL_REVISION;
      const url = modelUrl(activeRevision);
      forceDownload = command.forceDownload === true;
      fetchedBytes = 0; fetches = 0; failedFetches = 0; measurements.length = 0;
      const start = performance.now();
      const gpu = (navigator as Navigator & { gpu?: { requestAdapter(): Promise<{ info?: Record<string, string> } | null> } }).gpu;
      if (!gpu) throw new MaskingError("webgpu_unavailable");
      const adapter = await gpu.requestAdapter();
      if (!adapter) throw new MaskingError("gpu_adapter_unavailable");
      // Import only after installing the network guard: the runtime captures fetch.
      const { MLCEngine, deleteModelAllInfoInCache } = await import("@mlc-ai/web-llm");
      const appConfig = { cacheBackend: "indexeddb" as const, model_list: [{
        model: url, model_id: MODEL_ID, model_lib: LIBRARY_URL,
        overrides: { context_window_size: CONTEXT_WINDOW },
      }] };
      if (forceDownload) {
        send({ id: command.id, kind: "progress", progress: 0, text: "正在清除目標版本快取並重新下載" });
        await deleteModelAllInfoInCache(MODEL_ID, appConfig);
      }
      const candidate = new MLCEngine({
        appConfig,
        logLevel: "SILENT",
        initProgressCallback: report => send({ id: command.id, kind: "progress", progress: report.progress, text: report.text }),
      });
      await candidate.reload(MODEL_ID);
      await Promise.all(measurements);
      engine = candidate;
      const info = adapter.info;
      send({ id: command.id, kind: "loaded", metrics: {
        modelRevision: activeRevision, elapsedMs: performance.now() - start, fetchedBytes, fetches, failedFetches,
        adapter: Object.fromEntries((["vendor", "architecture", "device", "description"] as const).map(key => [key, String(info?.[key] ?? "unavailable")])),
      } });
    } else {
      if (!engine) throw new MaskingError("model_not_ready");
      const start = performance.now();
      const entities: Entity[] = [];
      const chunks = splitText(command.text);
      for (const [index, chunk] of chunks.entries()) {
        const extracted = await extractInStages(chunk, async (messages, schema, maxTokens) => {
          await engine!.resetChat();
          const result = await engine!.chat.completions.create({
            messages, temperature: 0, seed: 0, max_tokens: maxTokens,
            extra_body: { enable_thinking: false },
            response_format: { type: "json_object", schema },
          });
          if (result.choices[0]?.finish_reason !== "stop") {
            const error = new MaskingError("incomplete_output");
            error.generatedOutput = result.choices[0]?.message.content ?? "";
            throw error;
          }
          return result.choices[0].message.content ?? "";
        }, index);
        rounds.push(...extracted.rounds);
        for (const entity of extracted.entities) if (!entities.some(item => item.text === entity.text)) entities.push(entity);
      }
      send({ id: command.id, kind: "detected", detection: { entities, rounds, elapsedMs: performance.now() - start, chunks: chunks.length } });
    }
  } catch (error) {
    // Never forward library exception messages. Stage outputs are local lab evidence.
    send({ id: command.id, kind: "error", rounds: [...rounds, ...(error instanceof MaskingError ? error.rounds : [])], code: error instanceof MaskingError ? error.code : command.operation === "load" ? "model_load_failed" : "model_execution_failed" });
  } finally { busy = false; }
}
self.onmessage = (event: MessageEvent<WorkerCommand>) => { void execute(event.data); };
