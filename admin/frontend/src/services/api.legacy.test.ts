import { afterEach, describe, expect, it, vi } from "vitest";

import {
  checkHealth,
  getRuntimeConfig,
  normalizeApiErrorDetail,
  sendChat as sendApprovedChat,
  parseChatResponse,
  parseAdminApplicableLawTestResult,
  updateRuntimeConfig,
  runAdminChatTest as runApprovedAdminChatTest,
  runAdminApplicableLawTest as runApprovedAdminApplicableLawTest,
  type AdminApplicableLawTestResult,
  type RuntimeConfig,
} from "./api";
import { approvePrivacyDraft, createPrivacyDraft } from "./clientPrivacy";

// These protocol tests start after the explicit privacy confirmation. The separate
// clientPrivacy tests exercise the actual unapproved/approved transport boundary.
const sendChat = (...args: Parameters<typeof sendApprovedChat>) => sendApprovedChat(approvePrivacyDraft(createPrivacyDraft(args[0])), ...args.slice(1) as Tail<Parameters<typeof sendApprovedChat>>);
type Tail<T extends unknown[]> = T extends [unknown, ...infer Rest] ? Rest : never;
const runAdminChatTest = (...args: Parameters<typeof runApprovedAdminChatTest>) => runApprovedAdminChatTest(args[0], approvePrivacyDraft(createPrivacyDraft(args[1])), args[2], args[3], args[4]);
const runAdminApplicableLawTest = (...args: Parameters<typeof runApprovedAdminApplicableLawTest>) => runApprovedAdminApplicableLawTest(args[0], approvePrivacyDraft(createPrivacyDraft(args[1])), args[2], args[3]);

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
  vi.useRealTimers();
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
  it("loads config with the browser session cookie without a static token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(runtimeConfig), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRuntimeConfig("admin-token")).resolves.toEqual(runtimeConfig);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/admin/config"),
      expect.objectContaining({
        credentials: "same-origin",
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
it("validates typed done metadata and rejects malformed facts", () => {
  const response = { ...chatResponse, contract_version: 2, facts_revision: 0, fact_updates: [], clarification: null, answer_sections: [], execution: { route: "tool", model_calls: 1 } };
  expect(parseChatResponse(response)).toEqual(response);
  expect(() => parseChatResponse({ ...response, fact_updates: [{ fact_key: "name", status: "provided", value: "secret", evidence: "secret", kind: "explicit" }] })).toThrow();
  expect(() => parseChatResponse({ ...response, facts_revision: -1 })).toThrow();
});
it("rejects extra fact metadata, absent evidence and unsafe clarification shapes", () => {
  const response = { ...chatResponse, contract_version: 2, facts_revision: 0, fact_updates: [], clarification: null, answer_sections: [], execution: { route: "tool", model_calls: 1 } };
  const update = { fact_key: "other_role", status: "provided", value: "主管", evidence: "我的主管", kind: "confirmation" };
  expect(() => parseChatResponse({ ...response, fact_updates: [{ ...update, person_name: "private" }] })).toThrow();
  expect(() => parseChatResponse({ ...response, fact_updates: [{ ...update, evidence: undefined }] })).toThrow();
  expect(() => parseChatResponse({ ...response, fact_updates: [{ ...update, evidence: "   " }] })).toThrow();
  const clarification = { question_id: "case.other_role.0", fact_key: "other_role", reason: "影響方向", question: "對方是？", options: [{ label: "主管", value: "主管" }] };
  expect(() => parseChatResponse({ ...response, clarification: { ...clarification, private_detail: "private" } })).toThrow();
  expect(() => parseChatResponse({ ...response, clarification: { ...clarification, options: [{ label: "主管", value: "主管", private_detail: "private" }] } })).toThrow();
});
it.each([{}, [null], [{ label: {} }], [{ label: "法規", source_url: {} }], [{ label: "法規", type: "made-up" }]])("rejects malformed retrieved sources before rendering: %j", sources => {
  expect(() => parseChatResponse({ ...chatResponse, rag_used: { status: true, sources } })).toThrow();
});
it("accepts nullable optional source metadata from older APIs", () => {
  const source = { label: "法規", type: "law", collection: null, doc_id: null, distance: null, source_url: null, article: null, version: null };
  expect(parseChatResponse({ ...chatResponse, rag_used: { status: true, sources: [source] } }).rag_used.sources).toEqual([source]);
});
const encoder = new TextEncoder();

describe("negotiated response versions", () => {
  const responseFor = (version: 1 | 2 | 3 | 4 | 5) => version === 1 ? chatResponse : {
    ...chatResponse, contract_version: version, facts_revision: 0, fact_updates: [], clarification: null,
    answer_sections: [], execution: { route: "direct_retrieval", model_calls: 1 }, analysis: [], context_revision: 0, summary_update: null,
  };
  it.each([1, 2, 3, 4] as const)("accepts matching version %i via JSON", async version => {
    const response = responseFor(version);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(response), { headers: { "Content-Type": "application/json" } })));
    expect(await sendChat({ ...chatRequest, ...(version === 1 ? {} : { contract_version: version }) })).toEqual(response);
  });
  it.each([1, 2, 3, 5] as const)("rejects a v4 JSON response with version %i without retry permission", async version => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(responseFor(version)), { headers: { "Content-Type": "application/json" } })));
    await expect(sendChat({ ...chatRequest, contract_version: 4 })).rejects.toMatchObject({ status: 502, retryable: false, message: "回覆協定版本不符" });
  });
  it.each([1, 2, 3, 5] as const)("rejects a v4 SSE done with version %i while retaining already received text", async version => {
    const { controller } = streamResponse();
    const onDelta = vi.fn();
    const request = sendChat({ ...chatRequest, contract_version: 4 }, undefined, onDelta);
    controller.enqueue(encoder.encode(eventText("delta", { text: "尚未完成" }) + eventText("done", responseFor(version))));
    await expect(request).rejects.toMatchObject({ status: 502, retryable: false, message: "回覆協定版本不符" });
    expect(onDelta).toHaveBeenCalledExactlyOnceWith("尚未完成");
  });
  it("rejects a versioned response to an unversioned v1 request", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(responseFor(2)), { headers: { "Content-Type": "application/json" } })));
    await expect(sendChat(chatRequest)).rejects.toMatchObject({ status: 502, retryable: false });
  });
});

describe("v4 summary and reasoning protocol", () => {
  const response = { ...chatResponse, contract_version: 4, context_revision: 2, summary_update: { base_revision: 2, summary: "使用者為學生。", evidence: ["我是學生"] }, clarification: null, analysis: [], execution: { route: "direct_retrieval", model_calls: 1 } };
  it("accepts text summary output without legacy fact fields, and binds open questions to a revision", () => {
    expect(parseChatResponse(response)).toEqual(response);
    const question = { question_id: "open.3", context_revision: 3, reason: "需要釐清場合", question: "事情發生在哪種場合？", options: [{ label: "校外", value: "校外" }], selection_mode: "single", max_selections: 1, validation_token: "signed" };
    expect(parseChatResponse({ ...response, interaction_mode: "clarify", clarification: question }).clarification).toEqual(question);
    expect(() => parseChatResponse({ ...response, interaction_mode: "clarify", clarification: { ...question, context_revision: undefined } })).toThrow();
    expect(() => parseChatResponse({ ...response, interaction_mode: "clarify", clarification: { ...question, fact_key: "city" } })).toThrow();
    expect(() => parseChatResponse({ ...response, summary_update: { ...response.summary_update, summary: "x".repeat(4001) } })).toThrow();
  });
  it("delivers successive provider text chunks without turning them into reply text", async () => {
    const { controller } = streamResponse();
    const onReasoning = vi.fn(); const onDelta = vi.fn();
    const request = sendChat({ ...chatRequest, contract_version: 4 }, undefined, onDelta, undefined, undefined, undefined, onReasoning);
    const first = { text: "公開片段一", kind: "summary", stage: "understanding" };
    const second = { text: "公開片段二", kind: "text", stage: "answer" };
    controller.enqueue(encoder.encode(eventText("reasoning", first) + eventText("reasoning", second) + eventText("done", response)));
    expect(await request).toEqual(response);
    expect(onReasoning.mock.calls).toEqual([[first], [second]]);
    expect(onDelta).not.toHaveBeenCalled();
  });
  it("rejects unrecognized provider metadata in a reasoning event", async () => {
    const { controller } = streamResponse();
    const request = sendChat({ ...chatRequest, contract_version: 4 });
    controller.enqueue(encoder.encode(eventText("reasoning", { text: "公開片段", kind: "text", stage: "answer", encrypted_content: "private" })));
    await expect(request).rejects.toMatchObject({ status: 502 });
  });
});

describe("v3 protocol and isolated admin requests", () => {
  const analysis = { stage: "sources", summary: "已檢查引用來源", facts: [], source_labels: ["測試法第1條"], limitations: ["尚未确认適用關係"] };
  const v3Response = { ...chatResponse, contract_version: 3, facts_revision: 2, fact_updates: [], clarification: null, answer_sections: [], execution: { route: "direct_retrieval", model_calls: 1 }, analysis: [analysis] };
  it("accepts official law metadata and array facts without dropping their structure", () => {
    const source = { label: "測試法第1條", type: "law", law_name: "測試法", article_number: "1", checked_at: "2026-09-30", effective_date: "2024-03-08", promulgated_date: "2023-08-16", effective_note: "日期屬法規版本資料", promulgated_date_scope: "law_latest_amendment_not_article_specific", effective_date_scope: "law_version_not_article_specific", source_url: "https://law.moj.gov.tw/", article: "合成測試條文" };
    const response = { ...v3Response, fact_updates: [{ fact_key: "subject_role", status: "provided", value: ["學生", "員工"], evidence: "我是學生也是員工", kind: "explicit" }], rag_used: { status: true, sources: [source] } };
    expect(parseChatResponse(response)).toEqual(response);
    expect(() => parseChatResponse({ ...response, contract_version: 2 })).toThrow();
  });
  it("only displays typed questions for clarify responses while preserving signed scenario metadata", () => {
    const clarification = { question_id: "scenario.role.2", fact_key: "subject_role", reason: "釐清情境", question: "朋友的身分？", options: [{ label: "學生", value: "學生" }], selection_mode: "multiple", max_selections: 2, validation_token: "signed", context_scope: "scenario" };
    expect(parseChatResponse({ ...v3Response, interaction_mode: "clarify", clarification }).clarification).toEqual(clarification);
    expect(parseChatResponse({ ...v3Response, clarification }).clarification).toBeNull();
  });
  it("rejects invalid analysis rather than exposing arbitrary model metadata", () => {
    expect(() => parseChatResponse({ ...v3Response, analysis: [{ ...analysis, reasoning: "private" }] })).toThrow();
    expect(() => parseChatResponse({ ...v3Response, analysis: [{ ...analysis, facts: "invalid" }] })).toThrow();
    expect(() => parseChatResponse({ ...v3Response, analysis: undefined })).toThrow();
  });
  it("delivers validated analysis events and final analysis through the same stream", async () => {
    const { controller } = streamResponse();
    const onAnalysis = vi.fn();
    const request = sendChat({ ...chatRequest, contract_version: 3 }, undefined, undefined, undefined, undefined, onAnalysis);
    controller.enqueue(encoder.encode(eventText("analysis", analysis) + eventText("done", v3Response)));
    expect(await request).toEqual(v3Response);
    expect(onAnalysis).toHaveBeenCalledExactlyOnceWith(analysis);
  });
  it("uses only the authenticated diagnostic endpoint for one-request partial overrides", async () => {
    const response = { response: v3Response, diagnostics: { history: { removed_messages: 2 }, effective_config: { pipeline: { trim_history: false } } } };
    const fetchMock = queuedResult(response);
    vi.stubGlobal("fetch", fetchMock);
    const overrides = { pipeline: { trim_history: false } };
    expect(await runAdminChatTest("admin-token", { ...chatRequest, contract_version: 3 }, overrides)).toEqual(response);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/v1/admin/test-jobs"), expect.objectContaining({ method: "POST", credentials: "same-origin", body: JSON.stringify({ request: { ...chatRequest, contract_version: 3 }, overrides }) }));
  });
});
const eventText = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

describe("isolated applicable-law admin contract", () => {
  const result: AdminApplicableLawTestResult = {
    output_contract: "applicable_laws_v1",
    prediction: { predicted_laws: ["性騷法", "性工法", "性平法"], decision_status: "predicted", decision_reason: null },
    diagnostics: { retrieval: { status: "zero_results" } },
  };

  it("preserves the exact predicted order without requiring any chat fields", () => {
    expect(parseAdminApplicableLawTestResult(result)).toEqual(result);
    expect(parseAdminApplicableLawTestResult({ ...result, prediction: { predicted_laws: ["性工法", "性騷法"], decision_status: "predicted", decision_reason: null } }).prediction.predicted_laws).toEqual(["性工法", "性騷法"]);
  });

  it.each(["insufficient_facts", "no_target_law"] as const)("accepts valid empty %s predictions", decision_status => {
    const empty = { ...result, prediction: { predicted_laws: [], decision_status, decision_reason: "缺少當事人關係或適用情境。" } };
    expect(parseAdminApplicableLawTestResult(empty)).toEqual(empty);
  });

  it.each([
    { predicted_laws: ["性騷法", "性騷法"], decision_status: "predicted", decision_reason: null },
    { predicted_laws: ["性騷擾防治法"], decision_status: "predicted", decision_reason: null },
    { predicted_laws: [" 性工法"], decision_status: "predicted", decision_reason: null },
    { predicted_laws: ["刑法"], decision_status: "predicted", decision_reason: null },
    { predicted_laws: ["性騷法", "性工法", "性平法", "性騷法"], decision_status: "predicted", decision_reason: null },
    { predicted_laws: [], decision_status: "predicted", decision_reason: null },
    { predicted_laws: ["性工法"], decision_status: "insufficient_facts", decision_reason: "未提供會影響適用方向的當事人身分與關係。" },
    { predicted_laws: ["性平法"], decision_status: "no_target_law", decision_reason: "情境僅描述一般財物糾紛，沒有性或性別相關行為。" },
    { predicted_laws: [], decision_status: "unknown" },
    { predicted_laws: [], decision_status: ["insufficient_facts"] },
    { predicted_laws: "性工法", decision_status: "predicted", decision_reason: null },
    { predicted_laws: ["性工法"], decision_status: "predicted", decision_reason: null, reply: "額外聊天正文" },
  ])("rejects invalid predictions without repairing them: %j", prediction => {
    expect(() => parseAdminApplicableLawTestResult({ ...result, prediction })).toThrowError("適用法規預測格式不正確");
  });

  it.each([
    null,
    { output_contract: "chat", prediction: result.prediction, diagnostics: {} },
    { ...result, diagnostics: [] },
    { ...result, reply: "額外聊天正文" },
    { ...result, response: chatResponse },
    { ...result, suggested_replies: [] },
  ])("rejects wrong envelopes and extra chat output: %j", payload => {
    expect(() => parseAdminApplicableLawTestResult(payload)).toThrowError("適用法規預測格式不正確");
  });

  it("uses the approved snapshot and outer contract on the authenticated non-streaming endpoint", async () => {
    const fetchMock = queuedResult(result);
    vi.stubGlobal("fetch", fetchMock);
    const request = approvePrivacyDraft(createPrivacyDraft({ ...chatRequest, message: "合成 synthetic@example.com 情境", contract_version: 4 }));
    const overrides = { enable_anonymization: false, pipeline: { trim_history: false } };
    expect(await runApprovedAdminApplicableLawTest("admin-token", request, overrides)).toEqual(result);
    expect(fetchMock).toHaveBeenNthCalledWith(1,expect.stringContaining("/v1/admin/test-jobs"), expect.objectContaining({
      method: "POST", credentials: "same-origin",
      body: JSON.stringify({ request, overrides, output_contract: "applicable_laws_v1" }),
    }));
    const sent = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(sent.request.message).toBe("合成 [電子郵件] 情境");
    expect(sent.request).not.toHaveProperty("stream");
    expect(sent.request).not.toHaveProperty("output_contract");
  });

  it("rejects unapproved snapshots, missing authentication and non-v4 requests before transport", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(runApprovedAdminApplicableLawTest("admin-token", { ...chatRequest, contract_version: 4 }, {})).rejects.toMatchObject({ code: "review_required" });
    await expect(runAdminApplicableLawTest(" ", { ...chatRequest, contract_version: 4 }, {})).rejects.toMatchObject({ status: 401 });
    await expect(runAdminApplicableLawTest("admin-token", { ...chatRequest, contract_version: 3 }, {})).rejects.toMatchObject({ status: 422 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("exposes operation failures without converting them into empty predictions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "檢索失敗", retryable: false }), { status: 503, headers: { "Content-Type": "application/json" } })));
    await expect(runAdminApplicableLawTest("admin-token", { ...chatRequest, contract_version: 4 }, {})).rejects.toMatchObject({ status: 503, detail: "檢索失敗" });
  });
});

function pendingFetch() {
  let signal!: AbortSignal;
  vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => {
    signal = options.signal!;
    return new Promise<Response>((_resolve, reject) => {
      if (signal.aborted) reject(signal.reason);
      else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    });
  }));
  return () => signal;
}

describe("request deadlines", () => {
  it("aborts a health request that never returns and clears the deadline", async () => {
    vi.useFakeTimers();
    const getSignal = pendingFetch();
    const request = checkHealth();
    const rejected = expect(request).rejects.toMatchObject({ status: 504, retryable: false });
    await vi.advanceTimersByTimeAsync(10_000);
    await rejected;
    expect(getSignal().aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels health checks on caller cleanup without calling it a timeout", async () => {
    vi.useFakeTimers();
    const getSignal = pendingFetch();
    const controller = new AbortController();
    const request = checkHealth(controller.signal);
    const rejected = expect(request).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await rejected;
    expect(getSignal().aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ends a chat with no response headers instead of remaining in connecting", async () => {
    vi.useFakeTimers();
    const getSignal = pendingFetch();
    const rejected = expect(sendChat(chatRequest)).rejects.toMatchObject({ status: 504, retryable: false });
    await vi.advanceTimersByTimeAsync(30_000);
    await rejected;
    expect(getSignal().aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ends a silent stream, cancels its reader, and does not auto-resend", async () => {
    vi.useFakeTimers();
    const { cancel, fetchMock } = streamResponse();
    const rejected = expect(sendChat(chatRequest)).rejects.toMatchObject({ status: 504, retryable: false });
    await vi.advanceTimersByTimeAsync(45_000);
    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps a live stream open across heartbeats and clears timers when done", async () => {
    vi.useFakeTimers();
    const { controller } = streamResponse();
    const request = sendChat(chatRequest);
    for (let i = 0; i < 3; i += 1) {
      await vi.advanceTimersByTimeAsync(30_000);
      controller.enqueue(encoder.encode(": keep-alive\n\n"));
      await vi.advanceTimersByTimeAsync(0);
    }
    controller.enqueue(encoder.encode(eventText("done", chatResponse)));
    await expect(request).resolves.toEqual(chatResponse);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds total duration even when a server sends heartbeats forever", async () => {
    vi.useFakeTimers();
    const { controller, cancel } = streamResponse();
    const rejected = expect(sendChat(chatRequest)).rejects.toMatchObject({ status: 504, retryable: false });
    for (let i = 0; i < 6; i += 1) {
      await vi.advanceTimersByTimeAsync(30_000);
      controller.enqueue(encoder.encode(": keep-alive\n\n"));
      await vi.advanceTimersByTimeAsync(0);
    }
    await vi.advanceTimersByTimeAsync(10_000);
    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});

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

it.each([undefined, null, "", " \n", "字".repeat(301), 123])("rejects an abstention with an invalid reason: %j", decision_reason => {
  for (const decision_status of ["insufficient_facts", "no_target_law"]) {
    expect(() => parseAdminApplicableLawTestResult({ output_contract: "applicable_laws_v1", diagnostics: {}, prediction: { predicted_laws: [], decision_status, decision_reason } })).toThrow("適用法規預測格式不正確");
  }
});


it("sends the independent chat-test fingerprint only on the authenticated admin request", async () => {
  const { behaviorResult } = await import("../test/behaviorFixture");
  const result = behaviorResult();
  const fetchMock = queuedResult(result);
  vi.stubGlobal("fetch", fetchMock);
  await runAdminChatTest("test-token", { message: "synthetic", history: [], use_rag: true, contract_version: 4 }, {}, undefined, "b".repeat(64));
  const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
  expect(body.expected_chat_test_contract_sha256).toBe("b".repeat(64));
  expect(body).not.toHaveProperty("expected_applicable_laws_contract_sha256");
  expect(body).not.toHaveProperty("authorization");
});

function queuedResult(result: unknown) {
  return vi.fn<(url: string, options: RequestInit) => Promise<Response>>()
    .mockResolvedValueOnce(new Response(JSON.stringify({ job_id: "fixture-job", status: "queued" }), { status: 202, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ job_id: "fixture-job", status: "succeeded", result }), { headers: { "Content-Type": "application/json" } }));
}
