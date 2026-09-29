import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getRuntimeConfig,
  normalizeApiErrorDetail,
  sendChat,
  updateRuntimeConfig,
  type RuntimeConfig,
} from "./api";

const runtimeConfig: RuntimeConfig = {
  openrouter_model: "test/model",
  rag_retrieval_top_k: 3,
  rag_distance_threshold: null,
  enable_anonymization: true,
  temperature: 0.2,
  top_p: 1,
  max_tokens: 1200,
  reasoning_effort: "none",
  agent_prompt_sections: {},
  rag_collections: { law: "laws", judgment: "judgments", remedy: "remedies" },
  maintenance_message: "",
  enable_image_upload: true,
  development_mode: false,
  source: "firestore",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("normalizeApiErrorDetail", () => {
  it("formats the current field/message validation envelope", () => {
    expect(normalizeApiErrorDetail([
      {
        field: "message",
        message: "String should have at most 2000 characters",
        type: "string_too_long",
      },
    ])).toBe("message: String should have at most 2000 characters");
  });

  it("keeps compatibility with legacy Pydantic loc/msg issues", () => {
    expect(normalizeApiErrorDetail([
      {
        loc: ["body", "message"],
        msg: "String should have at least 1 character",
        type: "string_too_short",
      },
    ])).toBe("body.message: String should have at least 1 character");
  });
});

describe("sendChat", () => {
  it("exposes a normalized detail on 422 responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "Invalid request payload",
      errors: [{ field: "message", message: "Field required", type: "missing" }],
      retryable: false,
    }), {
      status: 422,
      headers: { "Content-Type": "application/json" },
    })));

    const request = sendChat({ message: "test", history: [], use_rag: true });

    await expect(request).rejects.toMatchObject({
      status: 422,
      detail: "Invalid request payload: message: Field required",
      retryable: false,
    });
  });
});

describe("runtime config API", () => {
  it("loads config with the verified bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(runtimeConfig), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRuntimeConfig("admin-token")).resolves.toEqual(runtimeConfig);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/admin/config"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer admin-token" }),
      }),
    );
  });

  it("normalizes config validation errors on save", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "Invalid request payload",
      errors: [{
        field: "rag_distance_threshold",
        message: "Must be between 0 and 2",
        type: "value_error",
      }],
      retryable: false,
    }), {
      status: 422,
      headers: { "Content-Type": "application/json" },
    })));

    const request = updateRuntimeConfig("admin-token", { rag_distance_threshold: 3 });

    await expect(request).rejects.toMatchObject({
      status: 422,
      detail: "Invalid request payload: rag_distance_threshold: Must be between 0 and 2",
    });
  });
});

const chatResponse = {
  reply: "您好，我會陪您。",
  session_id: "stream-session",
  anonymized: true,
  rag_used: { status: false, sources: [] },
  emotion: "擔心",
  suggested_replies: ["繼續", "查看資源"],
  action_buttons: [],
  interaction_mode: "answer",
  clarifying_questions: [],
};
const chatRequest = { message: "你好", history: [], use_rag: true };
const encoder = new TextEncoder();
const eventText = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

function streamResponse() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    cancel,
  });
  const fetchMock = vi.fn().mockResolvedValue(new Response(body, {
    headers: { "Content-Type": "text/event-stream; charset=utf-8" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  return { controller, cancel, fetchMock };
}

describe("chat streaming transport", () => {
  it("delivers actual progress before text and strips fields outside the progress contract", async () => {
    const { controller } = streamResponse();
    let notify!: () => void;
    const ready = new Promise<void>(resolve => { notify = resolve; });
    const onProgress = vi.fn(() => notify());
    const onDelta = vi.fn();
    let completed = false;
    const request = sendChat(chatRequest, undefined, onDelta, undefined, onProgress)
      .then(value => { completed = true; return value; });
    controller.enqueue(encoder.encode(eventText("progress", {
      phase: "retrieving", elapsed_ms: 1234.56, percentage: 50, message: "untrusted label",
    })));
    await ready;
    expect(onProgress).toHaveBeenCalledExactlyOnceWith({ phase: "retrieving", elapsed_ms: 1234.56 });
    expect(onDelta).not.toHaveBeenCalled();
    expect(completed).toBe(false);
    controller.enqueue(encoder.encode(eventText("delta", { text: "您好" }) + eventText("done", chatResponse)));
    await expect(request).resolves.toEqual(chatResponse);
    expect(onDelta).toHaveBeenCalledExactlyOnceWith("您好");
  });

  it.each([
    { phase: "estimated", elapsed_ms: 1000 },
    { phase: "generating", elapsed_ms: -1 },
    { phase: "generating", elapsed_ms: "1000" },
    { phase: "generating", elapsed_ms: null },
    { phase: "generating" },
  ])("rejects invalid progress before it reaches the UI", async progress => {
    const { controller } = streamResponse();
    const onProgress = vi.fn();
    const request = sendChat(chatRequest, undefined, undefined, undefined, onProgress);
    controller.enqueue(encoder.encode(eventText("progress", progress)));
    await expect(request).rejects.toMatchObject({ status: 502 });
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("rejects a non-finite parsed elapsed value", async () => {
    const { controller } = streamResponse();
    const onProgress = vi.fn();
    const request = sendChat(chatRequest, undefined, undefined, undefined, onProgress);
    controller.enqueue(encoder.encode('event: progress\ndata: {"phase":"waiting_model","elapsed_ms":1e999}\n\n'));
    await expect(request).rejects.toMatchObject({ status: 502 });
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("delivers text before done, then returns authoritative metadata without awaiting EOF", async () => {
    const { controller, cancel, fetchMock } = streamResponse();
    let resolveDelta!: () => void;
    const deltaReceived = new Promise<void>(resolve => { resolveDelta = resolve; });
    const onDelta = vi.fn(() => resolveDelta());
    let completed = false;
    const request = sendChat(chatRequest, undefined, onDelta).then(value => { completed = true; return value; });
    controller.enqueue(encoder.encode(": heartbeat\n\n" + eventText("delta", { text: "您好，" })));
    await deltaReceived;
    expect(onDelta).toHaveBeenCalledExactlyOnceWith("您好，");
    expect(completed).toBe(false);
    controller.enqueue(encoder.encode(eventText("done", chatResponse)));
    await expect(request).resolves.toEqual(chatResponse);
    expect(cancel).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/v1/chat/"), expect.objectContaining({
      headers: expect.objectContaining({ Accept: "text/event-stream" }),
      body: JSON.stringify({ ...chatRequest, stream: true }),
    }));
  });

  it("preserves Unicode and escaped newlines across byte, field and CRLF boundaries", async () => {
    const { controller } = streamResponse();
    const onDelta = vi.fn();
    const request = sendChat(chatRequest, undefined, onDelta);
    const source = ": ping\r\n\r\n" + eventText("delta", { text: "您好🙂\n第二行" }).replaceAll("\n", "\r\n")
      + `event: delta\rdata: {"text":\rdata: "再來"}\r\r`
      + eventText("done", chatResponse);
    for (const byte of encoder.encode(source)) controller.enqueue(new Uint8Array([byte]));
    controller.close();
    await expect(request).resolves.toEqual(chatResponse);
    expect(onDelta.mock.calls.map(([text]) => text).join("")).toBe("您好🙂\n第二行再來");
  });

  it("delivers cumulative guidance before done and accepts only preview fields", async () => {
    const { controller } = streamResponse();
    let notify!: () => void;
    let ready = new Promise<void>(resolve => { notify = resolve; });
    const onGuidance = vi.fn(() => notify());
    let completed = false;
    const request = sendChat(chatRequest, undefined, undefined, onGuidance)
      .then(value => { completed = true; return value; });
    const first = { interaction_mode: "clarify", clarifying_questions: ["事"] };
    controller.enqueue(encoder.encode(eventText("guidance", {
      ...first, action_buttons: [{ action: "url", url: "https://unvalidated.example" }], emotion: "擔心",
    })));
    await ready;
    expect(onGuidance).toHaveBeenLastCalledWith(first);
    expect(completed).toBe(false);
    ready = new Promise<void>(resolve => { notify = resolve; });
    const next = { interaction_mode: "clarify", clarifying_questions: ["事情發生在哪裡？"], suggested_replies: ["在學"] };
    for (const byte of encoder.encode(eventText("guidance", next))) controller.enqueue(new Uint8Array([byte]));
    await ready;
    expect(onGuidance).toHaveBeenLastCalledWith(next);
    expect(completed).toBe(false);
    controller.enqueue(encoder.encode(eventText("done", chatResponse)));
    await expect(request).resolves.toEqual(chatResponse);
    expect(onGuidance).toHaveBeenCalledTimes(2);
  });

  it.each([
    { interaction_mode: "clar" },
    { suggested_replies: ["合法文字", { label: "非字串" }] },
    { clarifying_questions: "非陣列" },
  ])("rejects invalid guidance shapes before they reach the UI", async guidance => {
    const { controller } = streamResponse();
    const onGuidance = vi.fn();
    const request = sendChat(chatRequest, undefined, undefined, onGuidance);
    controller.enqueue(encoder.encode(eventText("guidance", guidance)));
    await expect(request).rejects.toMatchObject({ status: 502 });
    expect(onGuidance).not.toHaveBeenCalled();
  });

  it("exposes a terminal error and closes the reader without waiting for EOF", async () => {
    const { controller, cancel } = streamResponse();
    const onDelta = vi.fn();
    const request = sendChat(chatRequest, undefined, onDelta);
    controller.enqueue(encoder.encode(eventText("delta", { text: "部分內容" }) + eventText("error", {
      code: "provider_error", detail: "模型暫時無法使用", status: 503,
      retryable: true, debug_message: "Provider failed",
    })));
    await expect(request).rejects.toMatchObject({
      status: 503, detail: "模型暫時無法使用", retryable: true, debugMessage: "Provider failed",
    });
    expect(onDelta).toHaveBeenCalledExactlyOnceWith("部分內容");
    expect(cancel).toHaveBeenCalledOnce();
  });

  it.each([
    eventText("delta", { text: "還沒完成" }),
    "event: done\ndata: {\"reply\":\"未完整傳輸",
    eventText("done", { reply: "缺少必要欄位" }),
    "event: delta\ndata: malformed\n\n",
  ])("rejects missing or invalid terminal responses", async payload => {
    const { controller } = streamResponse();
    const request = sendChat(chatRequest);
    controller.enqueue(encoder.encode(payload));
    controller.close();
    await expect(request).rejects.toMatchObject({ status: 502, detail: "回覆未完整接收，請重新送出訊息" });
  });

  it("aborts a pending read and releases its reader", async () => {
    const { controller, cancel } = streamResponse();
    const abort = new AbortController();
    let deltaReady!: () => void;
    const ready = new Promise<void>(resolve => { deltaReady = resolve; });
    const request = sendChat(chatRequest, abort.signal, deltaReady);
    controller.enqueue(encoder.encode(eventText("delta", { text: "第一段" })));
    await ready;
    abort.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("supports the previous JSON response during backend rollout", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(chatResponse), {
      headers: { "Content-Type": "application/json" },
    })));
    const onDelta = vi.fn();
    await expect(sendChat(chatRequest, undefined, onDelta)).resolves.toEqual(chatResponse);
    expect(onDelta).not.toHaveBeenCalled();
  });
});
