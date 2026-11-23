import { env, InferenceSession, Tensor } from "onnxruntime-web/wasm";
import wasmURL from "onnxruntime-web/ort-wasm-simd-threaded.wasm?url";
import mjsURL from "onnxruntime-web/ort-wasm-simd-threaded.mjs?url";
import { MaskingError } from "./extraction";
import { TINY_ASSET_PATH, TINY_REVISION } from "./config";
import { compactSpans, detectRuleSpans, prioritizeRules } from "./rules";
import { TinyTokenizer } from "./wordpiece";
import type { EntitySpan, WorkerCommand, WorkerEvent } from "./types";

const LABEL_COUNT = 73;
const people = [14, 32, 50, 68];
const organizations = [12, 30, 48, 66];

let model: { session: InferenceSession; tokenizer: TinyTokenizer } | undefined;

const reply = (event: WorkerEvent) => self.postMessage(event);
const sameOrigin = (url: string) => new URL(url, self.location.origin).href;

async function loadTiny() {
  const base = sameOrigin(import.meta.env.BASE_URL + TINY_ASSET_PATH);
  const response = await fetch(new URL("model.onnx", base), { mode: "same-origin", redirect: "error" });
  if (!response.ok || response.headers.get("content-type")?.includes("text/html")) throw new MaskingError("tiny_assets_missing");
  const fetchAsset = async (url: string) => {
    const result = await fetch(url, { mode: "same-origin", redirect: "error" });
    if (!result.ok || result.headers.get("content-type")?.includes("text/html")) throw new MaskingError("tiny_assets_missing");
    return result.arrayBuffer();
  };
  const [weights, vocab, wasm] = await Promise.all([
    response.arrayBuffer(),
    fetchAsset(new URL("vocab.txt", base).href),
    fetchAsset(sameOrigin(wasmURL)),
  ]);
  env.wasm.numThreads = 1;
  env.wasm.proxy = false;
  env.wasm.wasmPaths = { mjs: sameOrigin(mjsURL) };
  env.wasm.wasmBinary = wasm;
  const tokenizer = new TinyTokenizer(new TextDecoder().decode(vocab));
  const session = await InferenceSession.create(weights, { executionProviders: ["wasm"] });
  model = { session, tokenizer };
  const loaderBytes = performance.getEntriesByName(sameOrigin(mjsURL), "resource")
    .reduce((total, entry) => total + (entry as PerformanceResourceTiming).decodedBodySize, 0);
  return weights.byteLength + vocab.byteLength + wasm.byteLength + loaderBytes;
}

async function detectTiny(source: string, { tokenizer, session }: NonNullable<typeof model>) {
  const spans: EntitySpan[] = [];
  const windows = tokenizer.encode(source);
  for (const tokens of windows) {
    const dims = [1, tokens.length];
    const ids = new Tensor("int64", BigInt64Array.from(tokens, token => BigInt(token.id)), dims);
    const mask = new Tensor("int64", new BigInt64Array(tokens.length).fill(1n), dims);
    const types = new Tensor("int64", new BigInt64Array(tokens.length), dims);
    const { logits } = await session.run({ input_ids: ids, attention_mask: mask, token_type_ids: types });
    try {
      const data = logits.data;
      if (!(data instanceof Float32Array) || data.length !== tokens.length * LABEL_COUNT) throw new MaskingError("tiny_invalid_output");
      let current: EntitySpan | undefined;
      const flush = () => { if (current) spans.push(current); current = undefined; };
      for (const [index, token] of tokens.entries()) {
        const offset = index * LABEL_COUNT;
        let label = 0;
        for (let next = 1; next < LABEL_COUNT; next++) {
          if (data[offset + next] > data[offset + label]) label = next;
        }
        const person = people.indexOf(label), organization = organizations.indexOf(label);
        const kind = token.end <= token.start ? null : person >= 0 ? "person" : organization >= 0 ? "organization" : null;
        const tag = kind === "person" ? person : organization;
        if (!kind || tag === 0 || tag === 3 || (current && (current.type !== kind || current.end !== token.start))) flush();
        if (kind) {
          current = { start: current?.start ?? token.start, end: token.end, type: kind };
          if (tag === 2 || tag === 3) flush();
        }
      }
      flush();
    } finally {
      logits.dispose();
      ids.dispose(); mask.dispose(); types.dispose();
    }
  }
  return { spans: compactSpans(spans), chunks: windows.length };
}

self.addEventListener("message", async ({ data }: MessageEvent<WorkerCommand>) => {
  const { id, operation } = data;
  const start = performance.now();
  try {
    if (operation === "load") {
      reply({ id, kind: "progress", progress: 0, text: "正在載入 Tiny NER 與 CPU 執行環境" });
      await model?.session.release();
      model = undefined;
      const bytes = await loadTiny();
      reply({ id, kind: "loaded", metrics: {
        elapsedMs: performance.now() - start, fetchedBytes: bytes, fetches: 4, failedFetches: 0,
        adapter: { executionProvider: "wasm" },
        modelRevision: TINY_REVISION,
      } });
      return;
    }
    if (!model) throw new MaskingError("model_not_ready");
    const rules = detectRuleSpans(data.text);
    const tiny = await detectTiny(data.text, model);
    const spans = prioritizeRules(rules, tiny.spans);
    const entities = [...new Map(spans.map(({ start, end, type }) => {
      const text = data.text.slice(start, end);
      return [JSON.stringify([type, text]), { text, type }] as const;
    })).values()];
    reply({ id, kind: "detected", detection: { spans, entities, chunks: tiny.chunks, elapsedMs: performance.now() - start } });
  } catch (error) {
    reply({ id, kind: "error", code: error instanceof MaskingError ? error.code : operation === "load" ? "tiny_initialization_failed" : "tiny_execution_failed" });
  }
});
