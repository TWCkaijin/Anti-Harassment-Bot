/**
 * 性騷擾防治智能 AI — 對話記錄 Hook
 * 使用 localStorage 在本地保存對話記錄，保護使用者隱私。
 * 後端不保存任何對話，所有歷史由前端管理並在每次請求時傳送。
 */
import { createChatMetrics } from "../services/chatMetrics";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  assertResponseContract,
  sendChat,
  checkHealth,
  type ActionButton,
  type ChatGuidance,
  type ChatProgress,
  type ChatResponse,
  type ChatRequest,
  type DebugToolCall,
  type RagInfo,
  type CaseContext,
  type Clarification,
  type ClarificationAnswer,
  type FactUpdate,
  type AnalysisEntry,
  type ClientSettings,
  type ReasoningEntry,
} from "../services/api";
import {
  createChatRequest,
  getUserMessageValidationError,
} from "./conversationHistory";
import { applyFactUpdates, applySummaryUpdate, emptySummaryContext, hasCaseContext, isCaseContext, normalizeCaseContext, parseFact, toSummaryContext, type ClarificationDraft, type LegacyCaseContext } from "../services/caseFacts";
import { appendReasoning } from "../services/reasoning";
import { requestPrivacyReview } from "../services/privacyReview";
import { loadConversationStorage, recoverConversationMigration, saveConversationStorage, STORAGE_KEY, type StorageIssue } from "./conversationStorage";
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
  contractVersion?: 2 | 3 | 4;
  superseded?: boolean;
  answerSections?: ChatResponse["answer_sections"];
  processingTrace?: ProcessingTrace;
  analysis?: AnalysisEntry[];
  /** Provider reasoning is ephemeral: never saved, exported or sent as history. */
  reasoning?: ReasoningEntry[];
  clarificationDraft?: ClarificationDraft;
  requestKind?: "request" | "clarification" | "regeneration";
  originRequestId?: string;
}

export interface ConversationSession {
  id: string;
  createdAt: number;
  messages: ConversationMessage[];
  title?: string;
  schemaVersion?: 2 | 3 | 4;
  caseFacts?: CaseContext;
  legacyCaseFacts?: LegacyCaseContext;
  summaryEditedAfterMessageId?: string;
  summaryEditedAt?: number;
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
  const [currentSessionId, setActiveSessionId] = useState<string>(initialState.currentSessionId);
  const currentSessionIdRef = useRef(initialState.currentSessionId);
  const [loadingSessionIds, setLoadingSessionIds] = useState<Set<string>>(() => new Set());
  // Token updates are intentionally transient: persist only completed or explicitly
  // interrupted messages, so a page reload cannot promote partial text to a reply.
  const [streamingBySession, setStreamingBySession] = useState<Record<string, ConversationMessage>>({});
  const [clarificationDrafts, setClarificationDrafts] = useState<Record<string, Record<string, ClarificationDraft>>>({});
  const [error, setError] = useState<string | null>(null);
  const [retryStatusBySession, setRetryStatusBySession] = useState<Record<string, string>>({});
  const [storageIssue, setStorageIssue] = useState<StorageIssue>(initialState.storage.issue);
  const storageWritableRef = useRef(initialState.storage.writable);
  const migrationSnapshotRef = useRef(initialState.storage.migrationSnapshot ?? null);
  const persistedMigrationSessionsRef = useRef<ConversationSession[] | null>(null);
  const [contractVersion, setContractVersion] = useState<1 | 2 | 3 | 4>(1);
  const contractV2 = contractVersion >= 2;
  const [clientSettings, setClientSettings] = useState<ClientSettings>();
  const [isBackendConnected, setIsBackendConnected] = useState<boolean | null>(null);
  const connectionRef = useRef<{ connected: boolean | null; contractVersion: 1 | 2 | 3 | 4; clientSettings?: ClientSettings }>({ connected: null, contractVersion: 1 });
  const healthControllerRef = useRef<AbortController | null>(null);
  const checkBackendConnection = useCallback(() => {
    connectionRef.current = { connected: null, contractVersion: 1 };
    healthControllerRef.current?.abort();
    const controller = new AbortController();
    healthControllerRef.current = controller;
    return checkHealth(controller.signal).then(health => {
      if (controller.signal.aborted || healthControllerRef.current !== controller) return;
      const versions = health.capabilities?.chat_contract_versions;
      if (versions !== undefined && (!Array.isArray(versions) || ![1, 2, 3, 4].some(version => versions.includes(version)))) {
        throw new Error("No supported chat contract");
      }
      const selectedVersion = versions?.includes(4) ? 4 : versions?.includes(3) ? 3 : versions?.includes(2) ? 2 : 1;
      connectionRef.current = { connected: true, contractVersion: selectedVersion, clientSettings: health.client_settings };
      setContractVersion(selectedVersion);
      setClientSettings(health.client_settings);
      setIsBackendConnected(true);
    }).catch(() => {
      if (!controller.signal.aborted && healthControllerRef.current === controller) {
        connectionRef.current = { connected: false, contractVersion: 1 };
        setIsBackendConnected(false);
      }
    });
  }, []);
  const reconnectBackend = useCallback(() => {
    setIsBackendConnected(null);
    setContractVersion(1);
    return checkBackendConnection();
  }, [checkBackendConnection]);
  useEffect(() => {
    void checkBackendConnection();
    return () => { healthControllerRef.current?.abort(); };
  }, [checkBackendConnection]);

  // 同步到 localStorage
  const sessionsRef = useRef(sessions);
  const loadingSessionIdsRef = useRef<Set<string>>(new Set());
  const abortControllersRef = useRef(new Map<string, AbortController>());
  const ownersRef = useRef(new Map<string, symbol>());
  const reviewingSessionIdsRef = useRef(new Set<string>());
  const setCurrentSessionId = useCallback((id: string) => {
    for (const pendingId of reviewingSessionIdsRef.current) {
      if (pendingId !== id) abortControllersRef.current.get(pendingId)?.abort();
    }
    currentSessionIdRef.current = id;
    setActiveSessionId(id);
  }, []);
  useEffect(() => {
    sessionsRef.current = sessions;
  });

  useEffect(() => {
    if (persistedMigrationSessionsRef.current === sessions) {
      persistedMigrationSessionsRef.current = null;
      return;
    }
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
    () => {
      const drafts = clarificationDrafts[currentSessionId] ?? {};
      const saved = (currentSession?.messages ?? []).map(message => drafts[message.id] ? { ...message, clarificationDraft: drafts[message.id] } : message);
      return streamMessage ? [...saved, streamMessage] : saved;
    },
    [currentSession?.messages, streamMessage, clarificationDrafts, currentSessionId]
  );
  const isLoading = loadingSessionIds.has(currentSessionId);
  const retryStatus = retryStatusBySession[currentSessionId] ?? null;

  // ── 建立新 Session ─────────────────────────────────────────────────────

  const createNewSession = useCallback(() => {
    if (reviewingSessionIdsRef.current.has(currentSessionId)) abortControllersRef.current.get(currentSessionId)?.abort();
    // 若當前 Session 已經是空的，就不需再建立新對話
    const current = sessionsRef.current.find(s => s.id === currentSessionId);
    if (current && current.messages.length === 0 && !hasCaseContext(normalizeCaseContext(current.caseFacts))) {
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
      const filtered = prev.filter(s => s.messages.length > 0 || hasCaseContext(normalizeCaseContext(s.caseFacts)) || s.id === currentSessionId);
      const trimmed = migrationSnapshotRef.current === null && filtered.length >= MAX_SESSIONS ? filtered.slice(filtered.length - MAX_SESSIONS + 1) : filtered;
      if (trimmed.some(s => s.id === newId)) return trimmed;
      return [...trimmed, newSession];
    });
    
    setCurrentSessionId(newId);
    setError(null);
    return newId;
  }, [currentSessionId, setCurrentSessionId]);

  // ── 傳送訊息 ──────────────────────────────────────────────────────────

  const sendMessage = useCallback(
    async (userInput: string, imageBase64?: string, imageUrl?: string, replyContext?: ReplyContext, contextOverride?: CaseContext, regenerationOriginId?: string) => {
      // UI work can retain an older callback while a
      // reconnect is in flight; consult the current completed handshake.
      const { connected: isBackendConnected, contractVersion, clientSettings: settings } = connectionRef.current;
      const privacyReviewEnabled = settings?.enable_client_privacy_review !== false;
      if (imageBase64 && settings?.enable_image_upload === false) { setError("管理員已暫停圖片上傳，請改用文字描述。"); return false; }
      if (imageUrl && !imageBase64) { setError("圖片資料不完整，請重新選擇圖片。"); return false; }
      const contractV2 = contractVersion >= 2;
      const targetSessionId = currentSessionId;
      if (loadingSessionIdsRef.current.has(targetSessionId)) return false;
      const targetSession = sessionsRef.current.find(session => session.id === targetSessionId);
      if (!targetSession) return false;
      let caseContext = normalizeCaseContext(contextOverride ?? targetSession.caseFacts ?? emptySummaryContext());
      const startingRevision = caseContext.revision;
      if (isBackendConnected !== true) {
        setError(isBackendConnected === null ? "正在確認服務連線，請稍候再傳送。" : "請先重新連線，再傳送訊息。");
        return false;
      }
      if (!contractV2 && hasCaseContext(caseContext)) {
        setError("目前服務尚未支援這份情境摘要，請改開新對話。");
        return false;
      }
      if (contractVersion < 4 && caseContext.schema_version === 3 && caseContext.summary.trim()) {
        setError("目前服務尚未支援這份文字情境摘要，請重新連線或開啟新對話。");
        return false;
      }
      if (contractVersion < 3 && Object.values(caseContext.facts).some(fact => Array.isArray(fact?.value))) {
        setError("目前服務尚未支援這份複選情境摘要，請重新連線或開啟新對話。");
        return false;
      }
      try {
        caseContext = contractVersion === 4 ? toSummaryContext(caseContext)
          : { schema_version: contractVersion === 3 ? 2 : 1, revision: caseContext.revision, facts: caseContext.facts };
      } catch {
        setError("舊摘要超過可轉換長度，請先整理摘要內容；原始資料仍保留。");
        return false;
      }
      const typedAnswer = replyContext?.clarificationAnswer;
      if (typedAnswer && (!contractV2 || replyContext.factsRevision !== caseContext.revision || !parseFact(typedAnswer)
        || (contractVersion === 4 ? typedAnswer.fact_key !== undefined || typedAnswer.context_revision !== caseContext.revision : typedAnswer.fact_key === undefined)
        || (contractVersion < 3 && Array.isArray(typedAnswer.value)))) {
        setError("情境摘要已更新，請使用最新問題或直接輸入。");
        return false;
      }
      if (typedAnswer?.fact_key && typedAnswer.context_scope !== "scenario" && caseContext.schema_version !== 3) caseContext = { ...caseContext, revision: caseContext.revision + 1, facts: { ...caseContext.facts, [typedAnswer.fact_key]: parseFact(typedAnswer)! } };

      const normalizedUserInput = userInput.trim() || (imageBase64 ? "請協助說明這張圖片。" : "");
      if (!normalizedUserInput && !imageBase64) return false;

      const validationError = getUserMessageValidationError(userInput);
      if (validationError) {
        setError(validationError);
        return false;
      }

      setError(null);
      setSessionRetryStatus(targetSessionId, privacyReviewEnabled ? "等待您確認送出的內容" : "準備送出內容");
      setSessionLoading(targetSessionId, true);
      const abortController = new AbortController();
      abortControllersRef.current.set(targetSessionId, abortController);
      const owner = Symbol(targetSessionId);
      ownersRef.current.set(targetSessionId, owner);
      const isOwner = () => ownersRef.current.get(targetSessionId) === owner;
      let finished = false;
      // Prepare every outbound field before adding a new local message. The
      // review returns the exact approved snapshot expected by the transport.
      const editBoundaryIndex = targetSession.summaryEditedAfterMessageId
        ? targetSession.messages.findIndex(message => message.id === targetSession.summaryEditedAfterMessageId) : -1;
      const historyAfterEdit = editBoundaryIndex >= 0 ? targetSession.messages.slice(editBoundaryIndex + 1)
        : targetSession.summaryEditedAt !== undefined ? targetSession.messages.filter(message => message.timestamp >= targetSession.summaryEditedAt!) : targetSession.messages;
      const candidate = createChatRequest(historyAfterEdit.filter(message => !message.superseded && (contractVersion !== 4 || message.requestKind !== "regeneration")), normalizedUserInput, imageBase64, contractVersion === 1 ? undefined : contractVersion);
      if (contractV2) {
        candidate.contract_version = contractVersion === 4 ? 4 : contractVersion === 3 ? 3 : 2;
        candidate.case_context = structuredClone(caseContext);
        if (contractVersion === 4 && regenerationOriginId) candidate.regenerate_from_summary = true;
        if (typedAnswer) candidate.clarification_answer = typedAnswer;
      }

      let request: ChatRequest;
      let reviewAccepted = false;
      reviewingSessionIdsRef.current.add(targetSessionId);
      try {
        const approved = await requestPrivacyReview(candidate, abortController.signal, { enabled: privacyReviewEnabled });
        if (!approved || !isOwner() || abortController.signal.aborted || currentSessionIdRef.current !== targetSessionId) return false;
        const current = sessionsRef.current.find(session => session.id === targetSessionId);
        if (!current || normalizeCaseContext(current.caseFacts ?? emptySummaryContext()).revision !== startingRevision) return false;
        if (connectionRef.current.connected !== true || connectionRef.current.contractVersion !== contractVersion
          || (connectionRef.current.clientSettings?.enable_client_privacy_review !== false) !== privacyReviewEnabled
          || (approved.image_base64 && connectionRef.current.clientSettings?.enable_image_upload === false)) {
          setError("服務連線已變更，請重新檢查送出內容。");
          return false;
        }
        request = approved;
        if (contractV2 && request.case_context) caseContext = normalizeCaseContext(request.case_context);
        reviewAccepted = true;
      } catch (failure) {
        if (isOwner() && !abortController.signal.aborted) setError(failure instanceof Error ? failure.message : "無法準備送出內容，請重新檢查。");
        return false;
      } finally {
        reviewingSessionIdsRef.current.delete(targetSessionId);
        if (!reviewAccepted && isOwner()) {
          setSessionRetryStatus(targetSessionId, null);
          setSessionLoading(targetSessionId, false);
          abortControllersRef.current.delete(targetSessionId);
          ownersRef.current.delete(targetSessionId);
        }
      }

      const userMsg: ConversationMessage = {
        id: generateId(), role: "user", content: request.message, timestamp: Date.now(),
        ...(request.image_base64 ? { imageUrl: request.image_base64 } : {}),
        ...(request.clarification_answer ? { replyContext: { answers: [], factsRevision: startingRevision, clarificationAnswer: request.clarification_answer } } : {}),
        requestKind: regenerationOriginId ? "regeneration" : typedAnswer ? "clarification" : "request",
      };
      userMsg.originRequestId = regenerationOriginId ?? (typedAnswer ? latestUserRequest(targetSession.messages)?.id : userMsg.id);
      setSessions(previous => previous.map(session => session.id === targetSessionId && isOwner() ? {
        ...session,
        ...(contractV2 ? { caseFacts: caseContext } : {}),
        ...(contractVersion === 4 && session.caseFacts && session.caseFacts.schema_version !== 3 ? { legacyCaseFacts: session.legacyCaseFacts ?? session.caseFacts } : {}),
        pendingFacts: [], messages: [...session.messages, userMsg].slice(-MAX_MESSAGES_PER_SESSION),
      } : session));
      setSessionRetryStatus(targetSessionId, WAITING_MESSAGE);

      const metrics = createChatMetrics(false, request.use_rag);
      const assistantId = generateId();
      const assistantTimestamp = Date.now();
      const processingStarted = performance.now();
      let processingTrace: ProcessingTrace = { steps: [], duration_ms: 0, outcome: "running" };
      let processingAttempt = 0;
      const processingElapsed = () => Math.max(0, Math.round(performance.now() - processingStarted));
      let partialText = "";
      let partialAnalysis: AnalysisEntry[] = [];
      let partialReasoning: ReasoningEntry[] = [];
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
            analysis: partialAnalysis,
            reasoning: partialReasoning,
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
          const currentContext = normalizeCaseContext(session.caseFacts ?? emptySummaryContext());
          const sameRevision = currentContext.revision === caseContext.revision;
          const accepted = response?.contract_version === 4 && sameRevision && response.context_revision === caseContext.revision && caseContext.schema_version === 3
            ? { context: regenerationOriginId ? currentContext : applySummaryUpdate(caseContext, response.summary_update ?? null), pending: [] }
            : (response?.contract_version === 2 || response?.contract_version === 3) && sameRevision && response.facts_revision === caseContext.revision
              ? applyFactUpdates(caseContext, response.fact_updates ?? []) : { context: currentContext, pending: session.pendingFacts ?? [] };
          return { ...session, ...(contractV2 ? { caseFacts: accepted.context, pendingFacts: accepted.pending } : {}),
            messages: [...updated, { ...assistant, analysis: response?.analysis ?? assistant.analysis ?? partialAnalysis, reasoning: partialReasoning, processingTrace: finalTrace, ...(response?.contract_version === 2 || response?.contract_version === 3 || response?.contract_version === 4 ? { factsRevision: accepted.context.revision } : {}) }].slice(-MAX_MESSAGES_PER_SESSION) };
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
            }, entry => {
              if (!isOwner() || finished || abortController.signal.aborted) return;
              const previousIndex = partialAnalysis.findIndex(previous => previous.stage === entry.stage);
              partialAnalysis = previousIndex < 0 ? [...partialAnalysis, entry] : partialAnalysis.map((previous, index) => index === previousIndex ? entry : previous);
              publishStream();
            }, entry => {
              if (!isOwner() || finished || abortController.signal.aborted) return;
              partialReasoning = appendReasoning(partialReasoning, entry);
              publishStream();
            });
            break;
          } catch (err) {
            if (
              isOwner() && !abortController.signal.aborted && !partialText && !hasVisibleGuidance && partialAnalysis.length === 0 && partialReasoning.length === 0 &&
              err instanceof ApiError && err.retryable &&
              // Never automatically replay after reply, guidance or analysis was shown,
              // or a rate-limited request before its Retry-After window.
              err.status !== 429 && attempt < MAX_RETRYABLE_CHAT_ATTEMPTS
            ) {
              partialGuidance = undefined;
              partialAnalysis = [];
              partialReasoning = [];
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
        assertResponseContract(request, response);
        if ((response.contract_version === 2 || response.contract_version === 3) && response.facts_revision !== caseContext.revision) throw new ApiError(502, "情境摘要版本不符", "請重新送出訊息", false);
        if (response.contract_version === 4 && response.context_revision !== caseContext.revision) throw new ApiError(502, "情境摘要版本不符", "請重新送出訊息", false);
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
          analysis: response.analysis,
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
      return true;
    },
    [currentSessionId, setSessionLoading, setSessionRetryStatus]
  );

  const saveCaseFacts = useCallback(async (context: CaseContext, answerAgain = false) => {
    const session = sessionsRef.current.find(item => item.id === currentSessionId);
    if (!session || !isCaseContext(context)) return;
    ownersRef.current.delete(currentSessionId);
    abortControllersRef.current.get(currentSessionId)?.abort();
    abortControllersRef.current.delete(currentSessionId);
    setSessionLoading(currentSessionId, false);
    setSessionRetryStatus(currentSessionId, null);
    setStreamingBySession(previous => { const next = { ...previous }; delete next[currentSessionId]; return next; });
    const current = normalizeCaseContext(session.caseFacts);
    const nextFacts: CaseContext = context.schema_version === 3
      ? { ...context, facts: {}, revision: current.revision + 1, summary_origin: "user" }
      : { ...normalizeCaseContext(context), revision: current.revision + 1 };
    const editedSummary = nextFacts.schema_version === 3;
    const next = sessionsRef.current.map(item => item.id === currentSessionId ? {
      ...item, caseFacts: nextFacts, pendingFacts: [],
      ...(editedSummary ? { summaryEditedAfterMessageId: session.messages.at(-1)?.id, summaryEditedAt: Date.now() } : {}),
      ...(editedSummary && current.schema_version !== 3 ? { legacyCaseFacts: item.legacyCaseFacts ?? current } : {}),
      messages: item.messages.map(message => message.role === "assistant" ? { ...message, superseded: true } : message),
    } : item);
    if (editedSummary && migrationSnapshotRef.current !== null) {
      const recovered = recoverConversationMigration(next, migrationSnapshotRef.current);
      if (recovered === "saved") {
        storageWritableRef.current = true;
        migrationSnapshotRef.current = null;
        persistedMigrationSessionsRef.current = next;
        setStorageIssue(null);
      } else {
        setStorageIssue(recovered === "pending" ? "migration" : recovered);
        if (recovered !== "pending") migrationSnapshotRef.current = null;
      }
    }
    sessionsRef.current = next;
    setSessions(next);
    const originalRequest = latestUserRequest(session.messages);
    if (answerAgain && originalRequest) await sendMessage(originalRequest.content, undefined, undefined, undefined, nextFacts, originalRequest.id);
  }, [currentSessionId, sendMessage, setSessionLoading, setSessionRetryStatus]);

  const stopCurrentResponse = useCallback(() => {
    abortControllersRef.current.get(currentSessionId)?.abort();
  }, [currentSessionId]);

  const saveClarificationDraft = useCallback((messageId: string, draft: ClarificationDraft) => {
    // Unconfirmed free text stays in memory; it must not enter the persisted record.
    setClarificationDrafts(previous => ({ ...previous, [currentSessionId]: { ...previous[currentSessionId], [messageId]: draft } }));
  }, [currentSessionId]);

  // ── 清除當前 Session ──────────────────────────────────────────────────

  const clearCurrentSession = useCallback(() => {
    setClarificationDrafts(previous => { const next = { ...previous }; delete next[currentSessionId]; return next; });
    ownersRef.current.delete(currentSessionId);
    abortControllersRef.current.get(currentSessionId)?.abort();
    setSessions((prev) =>
      prev.map((s) =>
        s.id === currentSessionId ? { ...s, messages: [], caseFacts: emptySummaryContext(), legacyCaseFacts: undefined, summaryEditedAfterMessageId: undefined, summaryEditedAt: undefined, pendingFacts: [] } : s
      )
    );
    setSessionLoading(currentSessionId, false);
    setSessionRetryStatus(currentSessionId, null);
    setStreamingBySession(previous => { const next = { ...previous }; delete next[currentSessionId]; return next; });
  }, [currentSessionId, setSessionLoading, setSessionRetryStatus]);

  // ── 刪除 Session ──────────────────────────────────────────────────────

  const deleteSession = useCallback(
    (id: string) => {
      setClarificationDrafts(previous => { const next = { ...previous }; delete next[id]; return next; });
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
    setClarificationDrafts({});
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
      migrationSnapshotRef.current = null;
      persistedMigrationSessionsRef.current = null;
      setStorageIssue(null);
    } catch {
      storageWritableRef.current = false;
      migrationSnapshotRef.current = null;
      persistedMigrationSessionsRef.current = null;
      setStorageIssue("unavailable");
    }
  }, [setCurrentSessionId]);

  const caseFacts = normalizeCaseContext(currentSession?.caseFacts ?? emptySummaryContext());
  const hasCaseFacts = hasCaseContext(caseFacts);

  return {
    sessions,
    currentSession,
    currentSessionId,
    messages,
    isLoading,
    error,
    retryStatus,
    contractV2,
    contractVersion,
    clientSettings,
    isBackendConnected,
    reconnectBackend,
    storageIssue,
    caseFacts,
    incompatibleSummary: isBackendConnected === true && ((!contractV2 && hasCaseFacts)
      || (contractVersion < 4 && caseFacts.schema_version === 3 && Boolean(caseFacts.summary.trim()))
      || (contractVersion < 3 && Object.values(caseFacts.facts).some(fact => Array.isArray(fact?.value)))),
    summaryRequiresConnection: isBackendConnected !== true && hasCaseFacts,
    canRegenerate: Boolean(latestUserRequest(currentSession?.messages ?? [])),
    pendingFacts: currentSession?.pendingFacts ?? [],
    saveCaseFacts,
    saveClarificationDraft,
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
