/**
 * 性騷擾防治智能 AI — 對話記錄 Hook
 * 使用 localStorage 在本地保存對話記錄，保護使用者隱私。
 * 後端不保存任何對話，所有歷史由前端管理並在每次請求時傳送。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, sendChat, type ChatResponse, type MessageItem, type RagInfo } from "../services/api";

// ── 型別定義 ──────────────────────────────────────────────────────────────

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  anonymized?: boolean;
  ragUsed?: RagInfo;
  isError?: boolean;
  emotion?: string; // 加入的情緒標籤
  emotionColor?: string; // 情緒對應的顏色
  suggestedReplies?: string[];
  imageUrl?: string; // 圖片預覽網址 (僅 frontend 顯示用)
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
const MAX_HISTORY_TO_SEND = 20; // 每次最多傳送最近 20 輪給後端
const MAX_RETRYABLE_CHAT_ATTEMPTS = 2;
const RETRY_MESSAGE = "伺服器回傳錯誤，正在重試中";

// ── 輔助函式 ─────────────────────────────────────────────────────────────

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function loadSessions(): ConversationSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as ConversationSession[]) : [];
    // 過濾掉沒有訊息的空對話，避免重新載入時留下一堆空對話
    return parsed.filter(s => s.messages.length > 0);
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
  const [error, setError] = useState<string | null>(null);
  const [retryStatusBySession, setRetryStatusBySession] = useState<Record<string, string>>({});

  // 同步到 localStorage
  const sessionsRef = useRef(sessions);
  const loadingSessionIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    sessionsRef.current = sessions;
  });

  useEffect(() => {
    saveSessions(sessions);
  }, [sessions]);

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
  const messages = useMemo(
    () => currentSession?.messages ?? [],
    [currentSession?.messages]
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
    async (userInput: string, imageBase64?: string, imageUrl?: string) => {
      const targetSessionId = currentSessionId;
      if (
        (!userInput.trim() && !imageBase64) ||
        loadingSessionIdsRef.current.has(targetSessionId)
      ) {
        return;
      }

      setError(null);
      setSessionRetryStatus(targetSessionId, null);
      setSessionLoading(targetSessionId, true);

      // 建立使用者訊息
      const userMsg: ConversationMessage = {
        id: generateId(),
        role: "user",
        content: userInput.trim(),
        timestamp: Date.now(),
        imageUrl: imageUrl, // 加入圖片預覽 URL
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

      // 取得最近 N 輪歷史（不含剛加入的使用者訊息）
      const recentHistory: MessageItem[] = messages
        .slice(-MAX_HISTORY_TO_SEND * 2)
        .map(({ role, content }) => ({ role, content }));

      try {
        let response: ChatResponse | undefined;
        for (let attempt = 0; attempt <= MAX_RETRYABLE_CHAT_ATTEMPTS; attempt += 1) {
          try {
            response = await sendChat({
              message: userInput.trim(),
              history: recentHistory,
              use_rag: true,
              image_base64: imageBase64,
            });
            break;
          } catch (err) {
            if (
              err instanceof ApiError &&
              err.retryable &&
              attempt < MAX_RETRYABLE_CHAT_ATTEMPTS
            ) {
              setSessionRetryStatus(targetSessionId, RETRY_MESSAGE);
              await delay(500 * (attempt + 1));
              continue;
            }
            throw err;
          }
        }

        if (!response) {
          throw new Error("Chat response is missing after retry attempts");
        }

        const assistantMsg: ConversationMessage = {
          id: generateId(),
          role: "assistant",
          content: response.reply,
          timestamp: Date.now(),
          anonymized: response.anonymized,
          ragUsed: response.rag_used,
          suggestedReplies: response.suggested_replies,
        };

        setSessions((prev) =>
          prev.map((s) => {
            if (s.id !== targetSessionId) return s;

            // 更新使用者的訊息：加入 emotion 標籤
            const newMessages = [...s.messages];
            const lastUserMsgIdx = newMessages.findLastIndex(m => m.role === "user");
            if (lastUserMsgIdx !== -1 && response.emotion) {
              newMessages[lastUserMsgIdx] = {
                ...newMessages[lastUserMsgIdx],
                emotion: response.emotion,
                emotionColor: response.emotion_color,
              };
            }

            return {
              ...s,
              messages: [...newMessages, assistantMsg].slice(-MAX_MESSAGES_PER_SESSION),
            };
          })
        );
      } catch (err) {
        const errorMsg =
          err instanceof ApiError
            ? `服務暫時無法使用：${err.debugMessage ?? err.detail ?? err.message}`
            : "網路連線失敗，請稍後再試";

        setError(errorMsg);

        // 加入錯誤提示訊息
        const errorBubble: ConversationMessage = {
          id: generateId(),
          role: "assistant",
          content: errorMsg,
          timestamp: Date.now(),
          isError: true,
        };
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSessionId
              ? { ...s, messages: [...s.messages, errorBubble] }
              : s
          )
        );
      } finally {
        setSessionRetryStatus(targetSessionId, null);
        setSessionLoading(targetSessionId, false);
      }
    },
    [currentSessionId, messages, setSessionLoading, setSessionRetryStatus]
  );

  // ── 清除當前 Session ──────────────────────────────────────────────────

  const clearCurrentSession = useCallback(() => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id === currentSessionId ? { ...s, messages: [] } : s
      )
    );
  }, [currentSessionId]);

  // ── 刪除 Session ──────────────────────────────────────────────────────

  const deleteSession = useCallback(
    (id: string) => {
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
    sendMessage,
    createNewSession,
    setCurrentSessionId,
    clearCurrentSession,
    deleteSession,
    renameSession,
    clearAllSessions,
  };
}
