/**
 * 性騷擾防治智能 AI — 對話記錄 Hook
 * 使用 localStorage 在本地保存對話記錄，保護使用者隱私。
 * 後端不保存任何對話，所有歷史由前端管理並在每次請求時傳送。
 */
import { createChatMetrics } from "../services/chatMetrics";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  sendChat,
  checkHealth,
  type ActionButton,
  type ChatGuidance,
  type ChatProgress,
  type ChatResponse,
  type DebugToolCall,
  type RagInfo,
  type CaseContext,
  type Clarification,
  type ClarificationAnswer,
  type FactUpdate,
} from "../services/api";
import {
  createChatRequest,
  getUserMessageValidationError,
} from "./conversationHistory";
import { applyFactUpdates, emptyCaseContext, normalizeCaseContext, parseFact } from "../services/caseFacts";
import { loadConversationStorage, saveConversationStorage, STORAGE_KEY, type StorageIssue } from "./conversationStorage";
import { appendProcessingStep, type ProcessingPhase, type ProcessingTrace } from "../services/processingTrace";

// ── 型別定義 ──────────────────────────────────────────────────────────────

/** Local display metadata; content remains the complete model-visible message. */
export interface ReplyContext {
  answers: Array<{ question: string; answer: string }>;
  clarificationAnswer?: ClarificationAnswer;
  factsRevision?: number;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  anonymized?: boolean;
  ragUsed?: RagInfo;
  isError?: boolean;
  isCancelled?: boolean;
  isStreaming?: boolean;
  streamingGuidance?: ChatGuidance;
  interruptionReason?: string;
  emotion?: string; // 加入的情緒標籤
  emotionColor?: string; // 情緒對應的顏色
  suggestedReplies?: string[];
  actionButtons?: ActionButton[];
  interactionMode?: "answer" | "clarify";
  clarifyingQuestions?: string[];
  debugToolCalls?: DebugToolCall[];
  imageUrl?: string; // 圖片預覽網址 (僅 frontend 顯示用)
  replyContext?: ReplyContext;
  clarification?: Clarification | null;
  factsRevision?: number;
  contractVersion?: 2;
  superseded?: boolean;
  answerSections?: ChatResponse["answer_sections"];
  processingTrace?: ProcessingTrace;
  requestKind?: "request" | "clarification" | "regeneration";
  originRequestId?: string;
}

export interface ConversationSession {
  id: string;
  createdAt: number;
  messages: ConversationMessage[];
  title?: string;
  schemaVersion?: 2;
  caseFacts?: CaseContext;
  pendingFacts?: FactUpdate[];
}

// ── 常數 ─────────────────────────────────────────────────────────────────

const MAX_SESSIONS = 10;
const MAX_MESSAGES_PER_SESSION = 100;
const MAX_RETRYABLE_CHAT_ATTEMPTS = 2;
const RETRY_MESSAGE = "伺服器回傳錯誤，正在重試中";
const WAITING_MESSAGE = "正在等待伺服器回應";
const CHAT_PROGRESS_LABELS: Record<ChatProgress["phase"], string> = {
  anonymizing: "正在遮蔽文字識別資訊",
  preparing: "正在準備回覆",
  waiting_model: "正在等待 AI 回應",
  retrieving: "正在檢索資料庫",
  generating: "正在生成回覆",
  guidance: "正在產生後續引導",
  validating: "正在整理回覆",
};

// ── 輔助函式 ─────────────────────────────────────────────────────────────

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function latestUserRequest(messages: ConversationMessage[]): ConversationMessage | undefined {
  return [...messages].reverse().find(message => message.role === "user" && message.content.trim()
    && message.requestKind !== "clarification" && message.requestKind !== "regeneration" && !message.replyContext?.clarificationAnswer);
}

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => {
      window.clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

// ── Hook ─────────────────────────────────────────────────────────────────

export function useConversation(sessionId?: string) {
  const [initialState] = useState(() => {
    const storage = loadConversationStorage();
    const loaded = storage.sessions;
    const id = sessionId ?? generateId();
    if (!loaded.some(s => s.id === id)) {
      loaded.push({
        id,
        createdAt: Date.now(),
        messages: [],
      });
    }
    return { sessions: loaded, currentSessionId: id, storage };
  });

  const [sessions, setSessions] = useState<ConversationSession[]>(initialState.sessions);
  const [currentSessionId, setCurrentSessionId] = useState<string>(initialState.currentSessionId);
  const [loadingSessionIds, setLoadingSessionIds] = useState<Set<string>>(() => new Set());
  // Token updates are intentionally transient: persist only completed or explicitly
  // interrupted messages, so a page reload cannot promote partial text to a reply.
  const [streamingBySession, setStreamingBySession] = useState<Record<string, ConversationMessage>>({});
  const [error, setError] = useState<string | null>(null);
  const [retryStatusBySession, setRetryStatusBySession] = useState<Record<string, string>>({});
  const [storageIssue, setStorageIssue] = useState<StorageIssue>(initialState.storage.issue);
  const storageWritableRef = useRef(initialState.storage.writable);
  const [contractV2, setContractV2] = useState(false);
  const [isBackendConnected, setIsBackendConnected] = useState<boolean | null>(null);
  useEffect(() => {
    let mounted = true;
    checkHealth().then(health => {
      if (mounted) { setContractV2(health.capabilities?.chat_contract_versions.includes(2) === true); setIsBackendConnected(true); }
    }).catch(() => { if (mounted) setIsBackendConnected(false); });
    return () => { mounted = false; };
  }, []);

  // 同步到 localStorage
  const sessionsRef = useRef(sessions);
  const loadingSessionIdsRef = useRef<Set<string>>(new Set());
  const abortControllersRef = useRef(new Map<string, AbortController>());
  const ownersRef = useRef(new Map<string, symbol>());
  useEffect(() => {
    sessionsRef.current = sessions;
  });

  useEffect(() => {
    if (storageWritableRef.current) {
      const saved = saveConversationStorage(sessions);
      setStorageIssue(saved ? null : "unavailable");
    }
  }, [sessions]);

  useEffect(() => {
    const controllers = abortControllersRef.current;
    return () => {
      controllers.forEach(controller => controller.abort());
    };
  }, []);

  const setSessionLoading = useCallback((id: string, isLoading: boolean) => {
    const next = new Set(loadingSessionIdsRef.current);
    if (isLoading) {
      next.add(id);
    } else {
      next.delete(id);
    }
    loadingSessionIdsRef.current = next;
    setLoadingSessionIds(next);
  }, []);

  const setSessionRetryStatus = useCallback((id: string, status: string | null) => {
    setRetryStatusBySession((previous) => {
      if ((previous[id] ?? null) === status) return previous;
      if (status === null) {
        const next = { ...previous };
        delete next[id];
        return next;
      }
      return { ...previous, [id]: status };
    });
  }, []);

  // ── 取得當前 Session ───────────────────────────────────────────────────

  const currentSession = sessions.find((s) => s.id === currentSessionId);
  const streamMessage = streamingBySession[currentSessionId];
  const messages = useMemo(
    () => streamMessage ? [...(currentSession?.messages ?? []), streamMessage] : currentSession?.messages ?? [],
    [currentSession?.messages, streamMessage]
  );
  const isLoading = loadingSessionIds.has(currentSessionId);
  const retryStatus = retryStatusBySession[currentSessionId] ?? null;

  // ── 建立新 Session ─────────────────────────────────────────────────────

  const createNewSession = useCallback(() => {
    // 若當前 Session 已經是空的，就不需再建立新對話
    const current = sessionsRef.current.find(s => s.id === currentSessionId);
    if (current && current.messages.length === 0 && Object.keys(normalizeCaseContext(current.caseFacts).facts).length === 0) {
      return currentSessionId;
    }

    const newId = generateId();
    const newSession: ConversationSession = {
      id: newId,
      createdAt: Date.now(),
      messages: [],
    };
    
    setSessions((prev) => {
      // 在建立新 Session 時，順便清除其他空的 Session，避免清單被「新的對話」洗版
      const filtered = prev.filter(s => s.messages.length > 0 || Object.keys(normalizeCaseContext(s.caseFacts).facts).length > 0 || s.id === currentSessionId);
      const trimmed = filtered.length >= MAX_SESSIONS ? filtered.slice(filtered.length - MAX_SESSIONS + 1) : filtered;
      if (trimmed.some(s => s.id === newId)) return trimmed;
      return [...trimmed, newSession];
    });
    
    setCurrentSessionId(newId);
    setError(null);
    return newId;
  }, [currentSessionId]);

  // ── 傳送訊息 ──────────────────────────────────────────────────────────

  const sendMessage = useCallback(
    async (userInput: string, imageBase64?: string, imageUrl?: string, replyContext?: ReplyContext, contextOverride?: CaseContext, regenerationOriginId?: string) => {
      const targetSessionId = currentSessionId;
      if (loadingSessionIdsRef.current.has(targetSessionId)) return;
      const targetSession = sessionsRef.current.find(session => session.id === targetSessionId);
      if (!targetSession) return;
      let caseContext = normalizeCaseContext(contextOverride ?? targetSession.caseFacts);
      if (!contractV2 && (caseContext.revision > 0 || Object.keys(caseContext.facts).length > 0)) {
        setError("目前服務尚未支援這份情境摘要，請改開新對話。");
        return;
      }
      const typedAnswer = replyContext?.clarificationAnswer;
      if (typedAnswer && (!contractV2 || replyContext.factsRevision !== caseContext.revision || !parseFact(typedAnswer))) {
        setError("情境摘要已更新，請使用最新問題或直接輸入。");
        return;
      }
      if (typedAnswer) caseContext = { ...caseContext, revision: caseContext.revision + 1, facts: { ...caseContext.facts, [typedAnswer.fact_key]: parseFact(typedAnswer)! } };

      const normalizedUserInput = userInput.trim();
      if (!normalizedUserInput && !imageBase64) return;

      const validationError = getUserMessageValidationError(userInput);
      if (validationError) {
        setError(validationError);
        return;
      }

      setError(null);
      setSessionRetryStatus(targetSessionId, WAITING_MESSAGE);
      setSessionLoading(targetSessionId, true);
      const abortController = new AbortController();
      abortControllersRef.current.set(targetSessionId, abortController);
      const owner = Symbol(targetSessionId);
      ownersRef.current.set(targetSessionId, owner);
      const isOwner = () => ownersRef.current.get(targetSessionId) === owner;
      let finished = false;

      // 建立使用者訊息
      const userMsg: ConversationMessage = {
        id: generateId(),
        role: "user",
        content: normalizedUserInput,
        timestamp: Date.now(),
        imageUrl: imageUrl, // 加入圖片預覽 URL
        ...(replyContext ? { replyContext } : {}),
        requestKind: regenerationOriginId ? "regeneration" : typedAnswer ? "clarification" : "request",
      };
      userMsg.originRequestId = regenerationOriginId ?? (typedAnswer ? latestUserRequest(targetSession.messages)?.id : userMsg.id);

      // 先將使用者訊息加入畫面
      setSessions((prev) =>
        prev.map((s) =>
          s.id === targetSessionId
            ? {
                ...s,
                ...(contractV2 ? { caseFacts: caseContext } : {}),
                pendingFacts: [],
                messages: [...s.messages, userMsg].slice(-MAX_MESSAGES_PER_SESSION),
              }
            : s
        )
      );

      // 取得 API-safe 歷史（不含剛加入的使用者訊息）
      const request = createChatRequest(targetSession.messages.filter(message => !message.superseded), normalizedUserInput, imageBase64, contractV2 ? 2 : undefined);
      if (contractV2) {
        request.contract_version = 2;
        request.case_context = structuredClone(caseContext);
        if (typedAnswer) request.clarification_answer = typedAnswer;
      }

      const metrics = createChatMetrics(Boolean(imageBase64), request.use_rag);
      const assistantId = generateId();
      const assistantTimestamp = Date.now();
      const processingStarted = performance.now();
      let processingTrace: ProcessingTrace = { steps: [], duration_ms: 0, outcome: "running" };
      let processingAttempt = 0;
      const processingElapsed = () => Math.max(0, Math.round(performance.now() - processingStarted));
      let partialText = "";
      let partialGuidance: ChatGuidance | undefined;
      let hasVisibleGuidance = false;
      const publishStream = () => {
        if (!isOwner() || finished) return;
        setStreamingBySession(previous => !isOwner() || finished ? previous : ({
          ...previous,
          [targetSessionId]: {
            id: assistantId, role: "assistant", content: partialText,
            timestamp: assistantTimestamp, isStreaming: true,
            processingTrace,
            ...(partialGuidance ? { streamingGuidance: partialGuidance } : {}),
          },
        }));
      };
      const recordProcessingStep = (phase: ProcessingPhase) => {
        if (!isOwner() || finished || abortController.signal.aborted) return;
        processingTrace = appendProcessingStep(processingTrace, phase, processingElapsed(), processingAttempt);
        publishStream();
      };
      const commitAssistant = (assistant: ConversationMessage, response?: ChatResponse) => {
        if (!isOwner()) return;
        const finalTrace: ProcessingTrace = {
          ...processingTrace,
          duration_ms: Math.max(processingTrace.duration_ms, processingElapsed()),
          outcome: assistant.isCancelled ? "cancelled" : assistant.isError ? "error" : "complete",
        };
        setSessions(prev => prev.map(session => {
          // Clearing/deleting a conversation must not resurrect an in-flight turn.
          if (!isOwner() || session.id !== targetSessionId || !session.messages.some(message => message.id === userMsg.id)) return session;
          const updated = session.messages.map(message => message.id === userMsg.id && response?.emotion
            ? { ...message, emotion: response.emotion, emotionColor: response.emotion_color }
            : message);
          const accepted = response?.contract_version === 2 && response.facts_revision === caseContext.revision
            ? applyFactUpdates(caseContext, response.fact_updates ?? []) : { context: caseContext, pending: [] };
          return { ...session, ...(contractV2 ? { caseFacts: accepted.context, pendingFacts: accepted.pending } : {}),
            messages: [...updated, { ...assistant, processingTrace: finalTrace, ...(response?.contract_version === 2 ? { factsRevision: accepted.context.revision } : {}) }].slice(-MAX_MESSAGES_PER_SESSION) };
        }));
      };

      try {
        let response: ChatResponse | undefined;
        for (let attempt = 0; attempt <= MAX_RETRYABLE_CHAT_ATTEMPTS; attempt += 1) {
          try {
            abortController.signal.throwIfAborted();
            processingAttempt = attempt;
            recordProcessingStep("connecting");
            metrics.beginAttempt(attempt);
            response = await sendChat(request, abortController.signal, text => {
              if (!isOwner() || finished || abortController.signal.aborted || !text) return;
              metrics.firstToken();
              partialText += text;
              // A received delta proves generation even with an older backend
              // that does not yet send progress events.
              setSessionRetryStatus(targetSessionId, CHAT_PROGRESS_LABELS.generating);
              recordProcessingStep("generating");
            }, guidance => {
              if (!isOwner() || finished || abortController.signal.aborted) return;
              partialGuidance = guidance;
              setSessionRetryStatus(targetSessionId, CHAT_PROGRESS_LABELS.guidance);
              hasVisibleGuidance ||= guidance.interaction_mode === "clarify"
                ? Boolean(guidance.clarifying_questions?.some(question => question.trim()))
                : guidance.interaction_mode === "answer" && Boolean(guidance.suggested_replies?.some(reply => reply.trim()));
              if (hasVisibleGuidance) metrics.firstGuidance();
              recordProcessingStep("guidance");
            }, progress => {
              if (!isOwner() || finished || abortController.signal.aborted) return;
              metrics.progress(progress);
              setSessionRetryStatus(targetSessionId, CHAT_PROGRESS_LABELS[progress.phase]);
              recordProcessingStep(progress.phase);
            });
            break;
          } catch (err) {
            if (
              isOwner() && !abortController.signal.aborted && !partialText && !hasVisibleGuidance &&
              err instanceof ApiError && err.retryable &&
              // Never automatically replay after reply or guidance text was shown,
              // or a rate-limited request before its Retry-After window.
              err.status !== 429 && attempt < MAX_RETRYABLE_CHAT_ATTEMPTS
            ) {
              partialGuidance = undefined;
              recordProcessingStep("retrying");
              setSessionRetryStatus(targetSessionId, RETRY_MESSAGE);
              await delay(500 * (attempt + 1), abortController.signal);
              setSessionRetryStatus(targetSessionId, WAITING_MESSAGE);
              continue;
            }
            throw err;
          }
        }

        abortController.signal.throwIfAborted();
        if (!isOwner()) return;
        if (!response) throw new Error("Chat response is missing after retry attempts");
        if (response.contract_version === 2 && response.facts_revision !== caseContext.revision) throw new ApiError(502, "情境摘要版本不符", "請重新送出訊息", false);
        commitAssistant({
          id: assistantId,
          role: "assistant",
          content: response.reply,
          timestamp: assistantTimestamp,
          anonymized: response.anonymized,
          ragUsed: response.rag_used,
          suggestedReplies: response.suggested_replies,
          actionButtons: response.action_buttons,
          interactionMode: response.interaction_mode,
          clarifyingQuestions: response.clarifying_questions,
          debugToolCalls: response.debug_tool_calls,
          contractVersion: response.contract_version,
          clarification: response.clarification,
          answerSections: response.answer_sections,
        }, response);
        metrics.finish("success", response);
      } catch (err) {
        if (!isOwner()) return;
        if (abortController.signal.aborted) {
          metrics.finish("cancelled");
          commitAssistant({
            id: assistantId, role: "assistant", content: partialText,
            timestamp: assistantTimestamp, isCancelled: true,
          });
          return;
        }
        metrics.finish("error", undefined, err instanceof ApiError ? err.status : undefined);
        const errorMsg = err instanceof ApiError
          ? `服務暫時無法使用：${err.debugMessage ?? err.detail ?? err.message}`
          : "網路連線失敗，請稍後再試";
        setError(errorMsg);
        commitAssistant({
          id: assistantId, role: "assistant", content: partialText || errorMsg,
          timestamp: assistantTimestamp, isError: true,
          ...(partialText ? { interruptionReason: `回覆中斷，以上內容尚未完成。${errorMsg}` } : {}),
        });
      } finally {
        finished = true;
        if (isOwner()) {
          setStreamingBySession(previous => {
          if (previous[targetSessionId]?.id !== assistantId) return previous;
          const next = { ...previous };
          delete next[targetSessionId];
          return next;
        });
        setSessionRetryStatus(targetSessionId, null);
        setSessionLoading(targetSessionId, false);
        abortControllersRef.current.delete(targetSessionId);
        }
      }
    },
    [currentSessionId, contractV2, setSessionLoading, setSessionRetryStatus]
  );

  const saveCaseFacts = useCallback(async (context: CaseContext, answerAgain = false) => {
    const session = sessionsRef.current.find(item => item.id === currentSessionId);
    if (!session || !contractV2) return;
    ownersRef.current.delete(currentSessionId);
    abortControllersRef.current.get(currentSessionId)?.abort();
    abortControllersRef.current.delete(currentSessionId);
    setSessionLoading(currentSessionId, false);
    setSessionRetryStatus(currentSessionId, null);
    setStreamingBySession(previous => { const next = { ...previous }; delete next[currentSessionId]; return next; });
    const current = normalizeCaseContext(session.caseFacts);
    const nextFacts = { ...normalizeCaseContext(context), revision: current.revision + 1 };
    const next = sessionsRef.current.map(item => item.id === currentSessionId ? {
      ...item, caseFacts: nextFacts, pendingFacts: [],
      messages: item.messages.map(message => message.role === "assistant" ? { ...message, superseded: true } : message),
    } : item);
    sessionsRef.current = next;
    setSessions(next);
    const originalRequest = latestUserRequest(session.messages);
    if (answerAgain && originalRequest) await sendMessage(originalRequest.content, undefined, undefined, undefined, nextFacts, originalRequest.id);
  }, [currentSessionId, contractV2, sendMessage, setSessionLoading, setSessionRetryStatus]);

  const stopCurrentResponse = useCallback(() => {
    abortControllersRef.current.get(currentSessionId)?.abort();
  }, [currentSessionId]);

  // ── 清除當前 Session ──────────────────────────────────────────────────

  const clearCurrentSession = useCallback(() => {
    ownersRef.current.delete(currentSessionId);
    abortControllersRef.current.get(currentSessionId)?.abort();
    setSessions((prev) =>
      prev.map((s) =>
        s.id === currentSessionId ? { ...s, messages: [], caseFacts: emptyCaseContext(), pendingFacts: [] } : s
      )
    );
    setSessionLoading(currentSessionId, false);
    setSessionRetryStatus(currentSessionId, null);
    setStreamingBySession(previous => { const next = { ...previous }; delete next[currentSessionId]; return next; });
  }, [currentSessionId, setSessionLoading, setSessionRetryStatus]);

  // ── 刪除 Session ──────────────────────────────────────────────────────

  const deleteSession = useCallback(
    (id: string) => {
      ownersRef.current.delete(id);
      abortControllersRef.current.get(id)?.abort();
      sessionsRef.current = sessionsRef.current.filter(session => session.id !== id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      setSessionLoading(id, false);
      setSessionRetryStatus(id, null);
      if (id === currentSessionId) {
        createNewSession();
      }
    },
    [currentSessionId, createNewSession, setSessionLoading, setSessionRetryStatus]
  );

  // ── 重新命名 Session ───────────────────────────────────────────────────

  const renameSession = useCallback((id: string, newTitle: string) => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id === id ? { ...s, title: newTitle } : s
      )
    );
  }, []);

  // ── 清除所有 Session ───────────────────────────────────────────────────

  const clearAllSessions = useCallback(() => {
    ownersRef.current.clear();
    abortControllersRef.current.forEach(controller => controller.abort());
    setStreamingBySession({});
    setSessions([]);
    const newId = generateId();
    const newSession: ConversationSession = {
      id: newId,
      createdAt: Date.now(),
      messages: [],
    };
    setSessions([newSession]);
    setCurrentSessionId(newId);
    setError(null);
    loadingSessionIdsRef.current = new Set();
    setLoadingSessionIds(new Set());
    setRetryStatusBySession({});
    try {
      localStorage.removeItem(STORAGE_KEY);
      storageWritableRef.current = true;
      setStorageIssue(null);
    } catch {
      setStorageIssue("unavailable");
    }
  }, []);

  return {
    sessions,
    currentSession,
    currentSessionId,
    messages,
    isLoading,
    error,
    retryStatus,
    contractV2,
    isBackendConnected,
    storageIssue,
    caseFacts: normalizeCaseContext(currentSession?.caseFacts),
    incompatibleSummary: !contractV2 && (normalizeCaseContext(currentSession?.caseFacts).revision > 0 || Object.keys(normalizeCaseContext(currentSession?.caseFacts).facts).length > 0),
    canRegenerate: Boolean(latestUserRequest(currentSession?.messages ?? [])),
    pendingFacts: currentSession?.pendingFacts ?? [],
    saveCaseFacts,
    stopCurrentResponse,
    sendMessage,
    createNewSession,
    setCurrentSessionId,
    clearCurrentSession,
    deleteSession,
    renameSession,
    clearAllSessions,
  };
}
