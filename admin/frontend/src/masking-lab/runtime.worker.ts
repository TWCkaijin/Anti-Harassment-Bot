import type { MLCEngine } from "@mlc-ai/web-llm";
import { CONTEXT_WINDOW, ENTITY_SCHEMA, EXTRACTION_EXAMPLES, EXTRACTION_PROMPT, LIBRARY_URL, MODEL_ID, MODEL_URL, isModelAssetUrl, type Entity } from "./config";
import { MaskingError, parseEntities, splitText } from "./extraction";
import type { WorkerCommand, WorkerEvent } from "./types";

const send = (event: WorkerEvent) => self.postMessage(event);
let engine: MLCEngine | null = null;
let busy = false;
let fetchedBytes = 0, fetches = 0, failedFetches = 0;
const measurements: Promise<void>[] = [];
const originalFetch = self.fetch.bind(self);

// Model input never enters a network request. Measure response payload bytes only;
// this deliberately excludes TLS/HTTP overhead and reports IndexedDB hits as 0.
self.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const method = init?.method ?? (input instanceof Request ? input.method : "GET");
  if (method !== "GET" || init?.body || !isModelAssetUrl(url)) throw new MaskingError("network_request_blocked");
  fetches += 1;
  try {
    const response = await originalFetch(input, { ...init, credentials: "omit", referrerPolicy: "no-referrer" });
    if (!response.ok) { failedFetches += 1; return response; }
    measurements.push(response.clone().arrayBuffer().then(buffer => { fetchedBytes += buffer.byteLength; }).catch(() => { failedFetches += 1; }));
    return response;
  } catch (error) { failedFetches += 1; throw error; }
};

async function execute(command: WorkerCommand) {
  if (busy) { send({ id: command.id, kind: "error", code: "busy" }); return; }
  busy = true;
  try {
    if (command.operation === "load") {
      if (engine) { await engine.unload(); engine = null; }
      fetchedBytes = 0; fetches = 0; failedFetches = 0; measurements.length = 0;
      const start = performance.now();
      const gpu = (navigator as Navigator & { gpu?: { requestAdapter(): Promise<{ info?: Record<string, string> } | null> } }).gpu;
      if (!gpu) throw new MaskingError("webgpu_unavailable");
      const adapter = await gpu.requestAdapter();
      if (!adapter) throw new MaskingError("gpu_adapter_unavailable");
      // Import only after installing the network guard: the runtime captures fetch.
      const { MLCEngine } = await import("@mlc-ai/web-llm");
      const candidate = new MLCEngine({
        // IndexedDB uses fetch, allowing one guard to cover all artifact downloads.
        // Cache API's native Cache.add performs downloads outside the fetch wrapper.
        appConfig: { cacheBackend: "indexeddb", model_list: [{
          model: MODEL_URL, model_id: MODEL_ID, model_lib: LIBRARY_URL,
          overrides: { context_window_size: CONTEXT_WINDOW },
        }] },
        logLevel: "SILENT",
        initProgressCallback: report => send({ id: command.id, kind: "progress", progress: report.progress, text: report.text }),
      });
      await candidate.reload(MODEL_ID);
      await Promise.all(measurements);
      engine = candidate;
      const info = adapter.info;
      send({ id: command.id, kind: "loaded", metrics: {
        elapsedMs: performance.now() - start, fetchedBytes, fetches, failedFetches,
        adapter: Object.fromEntries((["vendor", "architecture", "device", "description"] as const).map(key => [key, String(info?.[key] ?? "unavailable")])),
      } });
    } else {
      if (!engine) throw new MaskingError("model_not_ready");
      const start = performance.now();
      const entities: Entity[] = [];
      const chunks = splitText(command.text);
      for (const chunk of chunks) {
        await engine.resetChat();
        const result = await engine.chat.completions.create({
          messages: [{ role: "system", content: EXTRACTION_PROMPT }, ...EXTRACTION_EXAMPLES.flatMap(example => [
            { role: "user" as const, content: JSON.stringify({ text: example.text }) },
            { role: "assistant" as const, content: JSON.stringify({ entities: example.entities }) },
          ]), { role: "user", content: JSON.stringify({ text: chunk }) }],
          temperature: 0, seed: 0, max_tokens: 768,
          extra_body: { enable_thinking: false },
          response_format: { type: "json_object", schema: ENTITY_SCHEMA },
        });
        if (result.choices[0]?.finish_reason !== "stop") throw new MaskingError("incomplete_output");
        const raw = result.choices[0].message.content ?? "";
        const extracted = parseEntities(raw, chunk);
        for (const entity of extracted) if (!entities.some(item => item.text === entity.text)) entities.push(entity);
      }
      send({ id: command.id, kind: "detected", detection: { entities, elapsedMs: performance.now() - start, chunks: chunks.length } });
    }
  } catch (error) {
    // Provider/library exceptions may contain input. Only fixed error codes leave the worker.
    send({ id: command.id, kind: "error", code: error instanceof MaskingError ? error.code : command.operation === "load" ? "model_load_failed" : "model_execution_failed" });
  } finally { busy = false; }
}
self.onmessage = (event: MessageEvent<WorkerCommand>) => { void execute(event.data); };
