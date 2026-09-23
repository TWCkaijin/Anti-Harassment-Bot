/**
 * 性騷擾防治智能 AI — 對話記錄 Hook
 * 使用 localStorage 在本地保存對話記錄，保護使用者隱私。
 * 後端不保存任何對話，所有歷史由前端管理並在每次請求時傳送。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  sendChat,
  type ActionButton,
  type ChatGuidance,
  type ChatProgress,
  type ChatResponse,
  type DebugToolCall,
  type RagInfo,
} from "../services/api";
import {
  createChatRequest,
  getUserMessageValidationError,
} from "./conversationHistory";

// ── 型別定義 ──────────────────────────────────────────────────────────────

/** Local display metadata; content remains the complete model-visible message. */
export interface ReplyContext {
  answers: Array<{ question: string; answer: string }>;
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
}

export interface ConversationSession {
  id: string;
  createdAt: number;
  messages: ConversationMessage[];
  title?: string;
}

// ── 常數 ─────────────────────────────────────────────────────────────────

const STORAGE_KEY = "harass_bot_conversations";
const MAX_SESSIONS = 10;
const MAX_MESSAGES_PER_SESSION = 100;
const MAX_RETRYABLE_CHAT_ATTEMPTS = 2;
const RETRY_MESSAGE = "伺服器回傳錯誤，正在重試中";
const WAITING_MESSAGE = "正在等待伺服器回應";
const CHAT_PROGRESS_LABELS: Record<ChatProgress["phase"], string> = {
  anonymizing: "正在匿名化",
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

function loadSessions(): ConversationSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as ConversationSession[]) : [];
    // 過濾掉沒有訊息的空對話，避免重新載入時留下一堆空對話
    return parsed
      .map(s => ({ ...s, messages: s.messages.filter(message => !message.isStreaming) }))
      .filter(s => s.messages.length > 0);
  } catch {
    return [];
  }
}

function saveSessions(sessions: ConversationSession[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  } catch {
    // localStorage 可能已滿，清除最舊的 session
    const trimmed = sessions.slice(-MAX_SESSIONS + 2);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  }
}

// ── Hook ─────────────────────────────────────────────────────────────────

export function useConversation(sessionId?: string) {
  const [initialState] = useState(() => {
    const loaded = loadSessions();
    const id = sessionId ?? generateId();
    if (!loaded.some(s => s.id === id)) {
      loaded.push({
        id,
        createdAt: Date.now(),
        messages: [],
      });
    }
    return { sessions: loaded, currentSessionId: id };
  });

  const [sessions, setSessions] = useState<ConversationSession[]>(initialState.sessions);
  const [currentSessionId, setCurrentSessionId] = useState<string>(initialState.currentSessionId);
  const [loadingSessionIds, setLoadingSessionIds] = useState<Set<string>>(() => new Set());
  // Token updates are intentionally transient: persist only completed or explicitly
  // interrupted messages, so a page reload cannot promote partial text to a reply.
  const [streamingBySession, setStreamingBySession] = useState<Record<string, ConversationMessage>>({});
  const [error, setError] = useState<string | null>(null);
  const [retryStatusBySession, setRetryStatusBySession] = useState<Record<string, string>>({});

  // 同步到 localStorage
  const sessionsRef = useRef(sessions);
  const loadingSessionIdsRef = useRef<Set<string>>(new Set());
  const abortControllersRef = useRef(new Map<string, AbortController>());
  useEffect(() => {
    sessionsRef.current = sessions;
  });

  useEffect(() => {
    saveSessions(sessions);
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
    if (current && current.messages.length === 0) {
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
      const filtered = prev.filter(s => s.messages.length > 0 || s.id === currentSessionId);
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
    async (userInput: string, imageBase64?: string, imageUrl?: string, replyContext?: ReplyContext) => {
      const targetSessionId = currentSessionId;
      if (loadingSessionIdsRef.current.has(targetSessionId)) return;

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

      // 建立使用者訊息
      const userMsg: ConversationMessage = {
        id: generateId(),
        role: "user",
        content: normalizedUserInput,
        timestamp: Date.now(),
        imageUrl: imageUrl, // 加入圖片預覽 URL
        ...(replyContext ? { replyContext } : {}),
      };

      // 先將使用者訊息加入畫面
      setSessions((prev) =>
        prev.map((s) =>
          s.id === targetSessionId
            ? {
                ...s,
                messages: [...s.messages, userMsg].slice(-MAX_MESSAGES_PER_SESSION),
              }
            : s
        )
      );

      // 取得 API-safe 歷史（不含剛加入的使用者訊息）
      const request = createChatRequest(messages, normalizedUserInput, imageBase64);

      const assistantId = generateId();
      const assistantTimestamp = Date.now();
      let partialText = "";
      let partialGuidance: ChatGuidance | undefined;
      let hasVisibleGuidance = false;
      const publishStream = () => {
        setStreamingBySession(previous => ({
          ...previous,
          [targetSessionId]: {
            id: assistantId, role: "assistant", content: partialText,
            timestamp: assistantTimestamp, isStreaming: true,
            ...(partialGuidance ? { streamingGuidance: partialGuidance } : {}),
          },
        }));
      };
      const commitAssistant = (assistant: ConversationMessage, response?: ChatResponse) => {
        setSessions(prev => prev.map(session => {
          // Clearing/deleting a conversation must not resurrect an in-flight turn.
          if (session.id !== targetSessionId || !session.messages.some(message => message.id === userMsg.id)) return session;
          const updated = session.messages.map(message => message.id === userMsg.id && response?.emotion
            ? { ...message, emotion: response.emotion, emotionColor: response.emotion_color }
            : message);
          return { ...session, messages: [...updated, assistant].slice(-MAX_MESSAGES_PER_SESSION) };
        }));
      };

      try {
        let response: ChatResponse | undefined;
        for (let attempt = 0; attempt <= MAX_RETRYABLE_CHAT_ATTEMPTS; attempt += 1) {
          try {
            abortController.signal.throwIfAborted();
            response = await sendChat(request, abortController.signal, text => {
              if (abortController.signal.aborted || !text) return;
              partialText += text;
              // A received delta proves generation even with an older backend
              // that does not yet send progress events.
              setSessionRetryStatus(targetSessionId, CHAT_PROGRESS_LABELS.generating);
              publishStream();
            }, guidance => {
              if (abortController.signal.aborted) return;
              partialGuidance = guidance;
              setSessionRetryStatus(targetSessionId, CHAT_PROGRESS_LABELS.guidance);
              hasVisibleGuidance ||= guidance.interaction_mode === "clarify"
                ? Boolean(guidance.clarifying_questions?.some(question => question.trim()))
                : guidance.interaction_mode === "answer" && Boolean(guidance.suggested_replies?.some(reply => reply.trim()));
              publishStream();
            }, progress => {
              if (abortController.signal.aborted) return;
              setSessionRetryStatus(targetSessionId, CHAT_PROGRESS_LABELS[progress.phase]);
            });
            break;
          } catch (err) {
            if (
              !abortController.signal.aborted && !partialText && !hasVisibleGuidance &&
              err instanceof ApiError && err.retryable &&
              // Never automatically replay after reply or guidance text was shown,
              // or a rate-limited request before its Retry-After window.
              err.status !== 429 && attempt < MAX_RETRYABLE_CHAT_ATTEMPTS
            ) {
              partialGuidance = undefined;
              setStreamingBySession(previous => {
                const next = { ...previous };
                delete next[targetSessionId];
                return next;
              });
              setSessionRetryStatus(targetSessionId, RETRY_MESSAGE);
              await delay(500 * (attempt + 1), abortController.signal);
              setSessionRetryStatus(targetSessionId, WAITING_MESSAGE);
              continue;
            }
            throw err;
          }
        }

        abortController.signal.throwIfAborted();
        if (!response) throw new Error("Chat response is missing after retry attempts");
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
        }, response);
      } catch (err) {
        if (abortController.signal.aborted) {
          commitAssistant({
            id: assistantId, role: "assistant", content: partialText,
            timestamp: assistantTimestamp, isCancelled: true,
          });
          return;
        }
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
        setStreamingBySession(previous => {
          const next = { ...previous };
          delete next[targetSessionId];
          return next;
        });
        setSessionRetryStatus(targetSessionId, null);
        setSessionLoading(targetSessionId, false);
        abortControllersRef.current.delete(targetSessionId);
      }
    },
    [currentSessionId, messages, setSessionLoading, setSessionRetryStatus]
  );

  const stopCurrentResponse = useCallback(() => {
    abortControllersRef.current.get(currentSessionId)?.abort();
  }, [currentSessionId]);

  // ── 清除當前 Session ──────────────────────────────────────────────────

  const clearCurrentSession = useCallback(() => {
    abortControllersRef.current.get(currentSessionId)?.abort();
    setSessions((prev) =>
      prev.map((s) =>
        s.id === currentSessionId ? { ...s, messages: [] } : s
      )
    );
  }, [currentSessionId]);

  // ── 刪除 Session ──────────────────────────────────────────────────────

  const deleteSession = useCallback(
    (id: string) => {
      abortControllersRef.current.get(id)?.abort();
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
    } catch {
      // ignore
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
