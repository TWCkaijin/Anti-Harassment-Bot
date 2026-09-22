import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, sendChat, type ChatResponse } from "../services/api";
import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "./conversationHistory";
import { useConversation } from "./useConversation";

vi.mock("../services/api", async () => {
  const actual = await vi.importActual<typeof import("../services/api")>("../services/api");
  return { ...actual, sendChat: vi.fn() };
});

const successfulResponse: ChatResponse = {
  reply: "我會陪你整理下一步。",
  session_id: "server-session",
  anonymized: false,
  rag_used: { status: false, sources: [] },
  suggested_replies: ["繼續", "查看資源"],
  action_buttons: [],
  interaction_mode: "answer",
  clarifying_questions: [],
};

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(sendChat).mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useConversation cancellation", () => {
  it("does not send the cancelled UI record in the next request", async () => {
    vi.mocked(sendChat).mockImplementationOnce((_request, signal) =>
      new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      })
    );
    const { result } = renderHook(() => useConversation("test-session"));

    let cancelledRequest!: Promise<void>;
    act(() => {
      cancelledRequest = result.current.sendMessage("第一個問題");
    });
    act(() => {
      result.current.stopCurrentResponse();
    });
    await act(async () => {
      await cancelledRequest;
    });

    expect(result.current.messages.at(-1)?.isCancelled).toBe(true);

    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    let nextRequest!: Promise<void>;
    act(() => {
      nextRequest = result.current.sendMessage("下一個問題");
    });

    expect(vi.mocked(sendChat).mock.calls[1]?.[0].history).toEqual([
      { role: "user", content: "第一個問題" },
    ]);

    await act(async () => {
      await vi.runAllTimersAsync();
      await nextRequest;
    });
  });
});

describe("useConversation image requests", () => {
  it("does not orphan an image-only assistant in the next request history", async () => {
    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    const { result } = renderHook(() => useConversation("image-session"));

    let request!: Promise<void>;
    act(() => {
      request = result.current.sendMessage("", "data:image/png;base64,AAAA");
    });

    expect(vi.mocked(sendChat).mock.calls[0]?.[0]).toMatchObject({
      message: "",
      history: [],
      image_base64: "data:image/png;base64,AAAA",
    });

    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });

    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    let nextRequest!: Promise<void>;
    act(() => {
      nextRequest = result.current.sendMessage("圖片之後的問題");
    });

    expect(vi.mocked(sendChat).mock.calls[1]?.[0].history).toEqual([]);

    await act(async () => {
      await vi.runAllTimersAsync();
      await nextRequest;
    });
  });
});

describe("useConversation quoted replies", () => {
  it("persists reply presentation while sending complete plain-text context to the API", async () => {
    vi.mocked(sendChat).mockResolvedValue(successfulResponse);
    const { result, unmount } = renderHook(() => useConversation("reply-session"));
    const content = "事情發生在哪裡？\n在學校";
    const replyContext = { answers: [{ question: "事情發生在哪裡？", answer: "在學校" }] };
    let request!: Promise<void>;
    act(() => {
      request = result.current.sendMessage(content, undefined, undefined, replyContext);
    });
    expect(result.current.messages[0]).toMatchObject({ content, replyContext });
    expect(vi.mocked(sendChat).mock.calls[0][0].message).toBe(content);
    expect(vi.mocked(sendChat).mock.calls[0][0]).not.toHaveProperty("replyContext");
    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });
    unmount();

    const restored = renderHook(() => useConversation("reply-session"));
    expect(restored.result.current.messages[0]).toMatchObject({ content, replyContext });
    act(() => {
      request = restored.result.current.sendMessage("下一步呢？");
    });
    expect(vi.mocked(sendChat).mock.calls[1][0].history[0]).toEqual({ role: "user", content });
    expect(restored.result.current.messages.at(-1)).not.toHaveProperty("replyContext");
    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });
  });
});

describe("useConversation retries", () => {
  it("retries a retryable 502 at most twice before succeeding", async () => {
    vi.mocked(sendChat)
      .mockRejectedValueOnce(new ApiError(502, "Bad gateway", "暫時錯誤", true))
      .mockRejectedValueOnce(new ApiError(502, "Bad gateway", "暫時錯誤", true))
      .mockResolvedValueOnce(successfulResponse);
    const { result } = renderHook(() => useConversation("retry-session"));

    let request!: Promise<void>;
    act(() => {
      request = result.current.sendMessage("請重試");
    });

    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });

    expect(sendChat).toHaveBeenCalledTimes(3);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      content: successfulResponse.reply,
    });
  });

  it("does not retry a permanent API error", async () => {
    vi.mocked(sendChat).mockRejectedValueOnce(
      new ApiError(400, "Bad request", "請求無效", false),
    );
    const { result } = renderHook(() => useConversation("permanent-error-session"));

    await act(async () => {
      await result.current.sendMessage("無效請求");
    });

    expect(sendChat).toHaveBeenCalledTimes(1);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      isError: true,
      content: "服務暫時無法使用：請求無效",
    });
  });

  it("does not automatically retry a rate-limit response", async () => {
    vi.mocked(sendChat).mockRejectedValueOnce(
      new ApiError(429, "Too many requests", "請稍後再試", true),
    );
    const { result } = renderHook(() => useConversation("rate-limit-session"));

    let request!: Promise<void>;
    act(() => {
      request = result.current.sendMessage("新的問題");
    });

    await act(async () => {
      await request;
    });

    expect(sendChat).toHaveBeenCalledTimes(1);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      isError: true,
      content: "服務暫時無法使用：請稍後再試",
    });
  });
});

describe("useConversation message limits", () => {
  it("rejects overflow without truncating and accepts exactly 2,000 characters", async () => {
    const boundary = "x".repeat(MAX_USER_MESSAGE_CHARACTERS);
    const overflow = `${boundary}x`;
    const { result } = renderHook(() => useConversation("message-limit-session"));

    await act(async () => {
      await result.current.sendMessage(overflow);
    });

    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.error).toBe(USER_MESSAGE_TOO_LONG_ERROR);
    expect(result.current.messages).toEqual([]);

    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    let request!: Promise<void>;
    act(() => {
      request = result.current.sendMessage(boundary);
    });

    expect(vi.mocked(sendChat).mock.calls[0]?.[0].message).toBe(boundary);

    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });
  });
});
