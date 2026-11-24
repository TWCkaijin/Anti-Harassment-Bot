import { afterEach, describe, expect, it, vi } from "vitest";
import { sendChat } from "./api";
import { approvePrivacyDraft, createPrivacyDraft } from "./clientPrivacy";

const turnId = "12345678-1234-4234-8234-123456789abc";
const request = () => approvePrivacyDraft(createPrivacyDraft({ message: "合成問題", history: [], use_rag: false }));
const frame = (event: string, data: object) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const done = (reply: string) => ({ turn_id: turnId, reply, session_id: "synthetic-session", anonymized: true,
  rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [],
  contract_version: 4, context_revision: 0, summary_update: null, clarification: null, execution: { route: "direct_retrieval", model_calls: 1 }, analysis: [] });
function streamResponse(events: string): Response {
  const bytes = new TextEncoder().encode(events);
  return new Response(new ReadableStream({ start(controller) { controller.enqueue(bytes); controller.close(); } }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } });
}
afterEach(() => vi.unstubAllGlobals());

describe("chat stream retraction", () => {
  it("delivers a turn-scoped retract before the replacement refusal and attaches App Check", async () => {
    const fetch = vi.fn().mockResolvedValue(streamResponse([
      frame("progress", { turn_id: turnId, phase: "generating", elapsed_ms: 1 }),
      frame("delta", { text: "provisional content" }),
      frame("retract", { turn_id: turnId, code: "gate_denied" }),
      frame("delta", { text: "short refusal" }),
      frame("done", done("short refusal")),
    ].join("")));
    vi.stubGlobal("fetch", fetch);
    const deltas: string[] = [], retractions: string[] = [];
    const response = await sendChat(request(), undefined, text => deltas.push(text), undefined, undefined, undefined, undefined,
      retraction => retractions.push(retraction.code), "synthetic-app-check-token");
    expect(deltas).toEqual(["provisional content", "short refusal"]);
    expect(retractions).toEqual(["gate_denied"]);
    expect(response.reply).toBe("short refusal");
    expect(fetch.mock.calls[0][1].headers["X-Firebase-AppCheck"]).toBe("synthetic-app-check-token");
  });
  it("reports a retryable review failure after retract without accepting a partial reply", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse([
      frame("delta", { text: "provisional content" }),
      frame("retract", { turn_id: turnId, code: "review_unavailable" }),
      frame("error", { code: "review_unavailable", status: 503, retryable: true, detail: "請重試" }),
    ].join(""))));
    const retract = vi.fn();
    await expect(sendChat(request(), undefined, undefined, undefined, undefined, undefined, undefined, retract))
      .rejects.toMatchObject({ code: "review_unavailable", status: 503, retryable: true });
    expect(retract).toHaveBeenCalledOnce();
  });
  it("rejects a retract for another turn", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse([
      frame("progress", { turn_id: turnId, phase: "generating", elapsed_ms: 1 }),
      frame("retract", { turn_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", code: "gate_denied" }),
    ].join(""))));
    const retract = vi.fn();
    await expect(sendChat(request(), undefined, undefined, undefined, undefined, undefined, undefined, retract))
      .rejects.toMatchObject({ status: 502 });
    expect(retract).not.toHaveBeenCalled();
  });
});
