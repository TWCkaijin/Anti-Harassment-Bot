import { useEffect, useRef, useState } from "react";
import { ApiError, assertResponseContract, runAdminApplicableLawTest, runAdminChatTest, type AdminApplicableLawTestResult, type AdminChatTestResult, type ChatRequest, type RuntimeConfig, type RuntimeConfigUpdate } from "../services/api";
import { applySummaryUpdate, emptySummaryContext, isCaseContext, parseFact, toSummaryContext, type CaseContext, type ClarificationDraft, type SummaryCaseContext } from "../services/caseFacts";
import { createChatRequest } from "@rag/shared/conversationHistory";
import { preparePrivateRequest } from "../services/privateRequest";
import { temporaryOverrides } from "../services/runtimeConfig";
import type { ConversationMessage, ReplyContext } from "../../../../client/frontend/src/hooks/useConversation";

export type TestMode = "chat" | "applicable_laws_v1";
export interface TestRun { id: number; mode: TestMode; request: ChatRequest; result: AdminChatTestResult | AdminApplicableLawTestResult; comparison: boolean }
type Message = ConversationMessage & { diagnosticMode?: TestMode };
type ActiveRequest = { controller: AbortController; userId?: string };
const describe = (error: unknown) => error instanceof ApiError ? error.detail ?? error.message : error instanceof Error ? error.message : "測試失敗";
export function predictionText(result: AdminApplicableLawTestResult) {
  return result.prediction.decision_status === "predicted" ? result.prediction.predicted_laws.join("、")
    : result.prediction.decision_status === "insufficient_facts" ? "資訊不足，無法判斷適用法規。" : "不適用三法。";
}

/** Ephemeral Admin conversation; shares the Client's UI, history and v4 context rules. */
export function useDiagnosticChat(csrfToken: string, config: RuntimeConfig, mode: TestMode) {
  const [messages, setMessages] = useState<Message[]>([]);
  const messagesRef = useRef<Message[]>([]);
  const [context, setContext] = useState<SummaryCaseContext>(emptySummaryContext);
  const contextRef = useRef(context);
  const historyBoundary = useRef(0);
  const [runs, setRuns] = useState<TestRun[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const active = useRef<ActiveRequest | null>(null);
  const updateMessages = (next: Message[]) => { messagesRef.current = next; setMessages(next); };
  const updateContext = (next: SummaryCaseContext) => { contextRef.current = next; setContext(next); };
  useEffect(() => () => { active.current?.controller.abort(); active.current = null; }, []);

  const finish = (owner: ActiveRequest) => { if (active.current === owner) { active.current = null; setRunning(false); } };
  const perform = async (owner: ActiveRequest, request: ChatRequest, overrides: RuntimeConfigUpdate, selectedMode: TestMode, comparison = false) => {
    try {
      const result = selectedMode === "applicable_laws_v1"
        ? await runAdminApplicableLawTest(csrfToken, request, overrides, owner.controller.signal)
        : await runAdminChatTest(csrfToken, request, overrides, owner.controller.signal);
      if (active.current !== owner || owner.controller.signal.aborted) return;
      if ("response" in result) {
        assertResponseContract(request, result.response);
        if (result.response.context_revision !== (request.case_context?.revision ?? 0)) throw new ApiError(502, "情境摘要版本不符", "回覆的摘要版本不符，請重新送出訊息。");
      }
      if (!comparison && owner.userId) {
        const previousContext = toSummaryContext(request.case_context ?? emptySummaryContext());
        if (contextRef.current.revision !== previousContext.revision) return;
        let nextContext = previousContext;
        if ("response" in result && !request.regenerate_from_summary) nextContext = applySummaryUpdate(previousContext, result.response.summary_update ?? null);
        updateContext(nextContext);
        const response = "response" in result ? result.response : null;
        const assistant: Message = {
          id: crypto.randomUUID(), role: "assistant", timestamp: Date.now(), diagnosticMode: selectedMode,
          content: response?.reply ?? predictionText(result as AdminApplicableLawTestResult),
          ...(response ? {
            anonymized: response.anonymized, ragUsed: response.rag_used,
            suggestedReplies: response.suggested_replies, actionButtons: response.action_buttons,
            interactionMode: response.interaction_mode, clarifyingQuestions: response.clarifying_questions,
            contractVersion: response.contract_version, factsRevision: nextContext.revision,
            clarification: response.clarification, answerSections: response.answer_sections,
            analysis: response.analysis, debugToolCalls: response.debug_tool_calls,
          } : {}),
        };
        updateMessages([...messagesRef.current.map(item => item.id === owner.userId && response?.emotion
          ? { ...item, emotion: response.emotion, emotionColor: response.emotion_color } : item), assistant]);
      }
      setRuns(previous => [...previous, { id: ++sequence.current, request, result, mode: selectedMode, comparison }].slice(-2));
    } catch (failure) {
      if (active.current === owner && !owner.controller.signal.aborted) {
        const detail = describe(failure); setError(detail);
        if (owner.userId) updateMessages([...messagesRef.current, { id: crypto.randomUUID(), role: "assistant", content: detail, timestamp: Date.now(), isError: true, diagnosticMode: selectedMode }]);
      }
    } finally { finish(owner); }
  };

  const send = async (text: string, imageBase64?: string, _imageUrl?: string, reply?: ReplyContext, regenerationOrigin?: Message): Promise<boolean> => {
    if (active.current) return false;
    const summary = contextRef.current;
    const answer = reply?.clarificationAnswer;
    if (answer && (reply?.factsRevision !== summary.revision || answer.context_revision !== summary.revision || answer.fact_key !== undefined || !parseFact(answer))) {
      setError("情境摘要已更新，請使用最新問題或直接輸入。"); return false;
    }
    if (imageBase64 && !config.enable_image_upload) { setError("圖片上傳目前已停用。"); return false; }
    const message = text.trim() || (imageBase64 ? "請協助說明這張圖片。" : "");
    if (!message) return false;
    const owner: ActiveRequest = { controller: new AbortController() };
    const selectedMode = mode;
    const overrides = structuredClone(temporaryOverrides(config));
    active.current = owner; setRunning(true); setError("");
    try {
      const history = messagesRef.current.slice(historyBoundary.current).filter(item => !item.superseded && item.requestKind !== "regeneration" && (!item.diagnosticMode || item.diagnosticMode === selectedMode));
      const candidate: ChatRequest = {
        ...createChatRequest(history, message, imageBase64, 4), contract_version: 4,
        use_rag: config.pipeline?.enable_rag ?? true, case_context: structuredClone(summary),
        ...(answer ? { clarification_answer: answer } : {}),
        ...(regenerationOrigin ? { regenerate_from_summary: true } : {}),
      };
      const request = await preparePrivateRequest(candidate, owner.controller.signal);
      if (!request || active.current !== owner || owner.controller.signal.aborted || contextRef.current.revision !== summary.revision) { finish(owner); return false; }
      const user: Message = {
        id: crypto.randomUUID(), role: "user", content: request.message, timestamp: Date.now(), diagnosticMode: selectedMode,
        ...(request.image_base64 ? { imageUrl: request.image_base64 } : {}),
        ...(request.clarification_answer ? { replyContext: { answers: [], factsRevision: summary.revision, clarificationAnswer: request.clarification_answer } } : {}),
        requestKind: regenerationOrigin ? "regeneration" : answer ? "clarification" : "request",
        originRequestId: regenerationOrigin?.id,
      };
      owner.userId = user.id;
      updateContext(toSummaryContext(request.case_context ?? emptySummaryContext()));
      updateMessages([...messagesRef.current, user]);
      void perform(owner, request, overrides, selectedMode);
      return true;
    } catch (failure) {
      if (active.current === owner && !owner.controller.signal.aborted) setError(describe(failure));
      finish(owner); return false;
    }
  };

  const stop = () => {
    const owner = active.current;
    owner?.controller.abort(); active.current = null; setRunning(false);
    if (owner?.userId) updateMessages([...messagesRef.current.map(message => message.id === owner.userId ? { ...message, isCancelled: true } : message), {
      id: crypto.randomUUID(), role: "assistant", content: "已停止這次回覆。", timestamp: Date.now(), isCancelled: true,
    }]);
  };
  const saveSummary = (value: CaseContext, answerAgain = false) => {
    if (!isCaseContext(value)) return;
    stop();
    const original = [...messagesRef.current].reverse().find(item => item.role === "user" && item.requestKind === "request" && item.diagnosticMode === "chat");
    updateContext({ ...toSummaryContext(value), revision: contextRef.current.revision + 1, summary_origin: "user" });
    historyBoundary.current = messagesRef.current.length;
    updateMessages(messagesRef.current.map(message => message.role === "assistant" ? { ...message, superseded: true } : message));
    if (answerAgain && original) void send(original.content, undefined, undefined, undefined, original);
  };
  const compare = (run: TestRun, overrides: RuntimeConfigUpdate) => {
    if (active.current) return;
    const owner = { controller: new AbortController() }; active.current = owner; setRunning(true); setError("");
    void perform(owner, run.request, structuredClone(overrides), run.mode, true);
  };
  const clear = () => { stop(); updateMessages([]); updateContext(emptySummaryContext()); historyBoundary.current = 0; setRuns([]); setError(""); };
  const saveClarificationDraft = (id: string, draft: ClarificationDraft) => updateMessages(messagesRef.current.map(message => message.id === id ? { ...message, clarificationDraft: draft } : message));
  return { messages, context, runs, running, error, send, stop, saveSummary, compare, clear, saveClarificationDraft };
}
