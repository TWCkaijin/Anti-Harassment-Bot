import { describe, expect, it } from "vitest";

import {
  MAX_ASSISTANT_HISTORY_CHARACTERS,
  MAX_HISTORY_TOTAL_CHARACTERS,
  MAX_USER_MESSAGE_CHARACTERS,
  MAX_USER_HISTORY_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
  buildChatHistory,
  createChatRequest,
  getUserMessageValidationError,
} from "./conversationHistory";

describe("buildChatHistory", () => {
  it("filters UI-only records and blank content", () => {
    const history = buildChatHistory([
      { role: "user", content: "  可保留的問題  " },
      { role: "assistant", content: "API failed", isError: true },
      { role: "assistant", content: "", isCancelled: true },
      { role: "assistant", content: "   " },
      { role: "assistant", content: "可保留的回答" },
    ]);

    expect(history).toEqual([
      { role: "user", content: "可保留的問題" },
      { role: "assistant", content: "可保留的回答" },
    ]);
  });

  it("uses role-specific limits without truncating oversized records", () => {
    const history = buildChatHistory([
      { role: "user", content: "x".repeat(MAX_USER_HISTORY_CHARACTERS + 1) },
      { role: "assistant", content: "y".repeat(MAX_ASSISTANT_HISTORY_CHARACTERS + 1) },
      { role: "user", content: "u".repeat(MAX_USER_HISTORY_CHARACTERS) },
      { role: "assistant", content: "a".repeat(MAX_ASSISTANT_HISTORY_CHARACTERS) },
    ]);

    expect(history).toHaveLength(2);
    expect(history[0].content).toHaveLength(MAX_USER_HISTORY_CHARACTERS);
    expect(history[1].content).toHaveLength(MAX_ASSISTANT_HISTORY_CHARACTERS);
  });

  it("keeps a contiguous newest suffix within the aggregate budget", () => {
    const history = buildChatHistory([
      { role: "user", content: "a".repeat(MAX_USER_HISTORY_CHARACTERS) },
      { role: "assistant", content: "b".repeat(MAX_ASSISTANT_HISTORY_CHARACTERS) },
      { role: "user", content: "c".repeat(MAX_USER_HISTORY_CHARACTERS) },
      { role: "assistant", content: "d".repeat(MAX_ASSISTANT_HISTORY_CHARACTERS) },
    ]);

    expect(history.map(({ content }) => content[0])).toEqual(["c", "d"]);
    expect(history.reduce((total, { content }) => total + content.length, 0))
      .toBeLessThanOrEqual(MAX_HISTORY_TOTAL_CHARACTERS);
  });

  it("drops an image-only turn together with its assistant", () => {
    const history = buildChatHistory([
      { role: "user", content: "", imageUrl: "blob:image-preview" },
      { role: "assistant", content: "只描述了先前圖片" },
      { role: "user", content: "可序列化的下一題" },
      { role: "assistant", content: "下一題的回答" },
    ]);

    expect(history).toEqual([
      { role: "user", content: "可序列化的下一題" },
      { role: "assistant", content: "下一題的回答" },
    ]);
  });

  it("keeps meaningful user text when its response was cancelled", () => {
    expect(buildChatHistory([
      { role: "user", content: "請先幫我整理重點" },
      { role: "assistant", content: "", isCancelled: true },
    ])).toEqual([
      { role: "user", content: "請先幫我整理重點" },
    ]);
  });

  it("never splits a turn or backfills across an aggregate budget gap", () => {
    const history = buildChatHistory([
      { role: "user", content: "舊" },
      { role: "assistant", content: "舊答" },
      { role: "user", content: "中".repeat(4) },
      { role: "assistant", content: "中答".repeat(4) },
      { role: "user", content: "新" },
      { role: "assistant", content: "新答" },
    ], 8);

    expect(history).toEqual([
      { role: "user", content: "新" },
      { role: "assistant", content: "新答" },
    ]);

    expect(buildChatHistory([
      { role: "user", content: "使用者" },
      { role: "assistant", content: "助理回答" },
    ], 5)).toEqual([]);
  });
});

describe("createChatRequest", () => {
  it("preserves the image-only request contract", () => {
    expect(createChatRequest([], "   ", "data:image/png;base64,AAAA")).toEqual({
      message: "",
      history: [],
      use_rag: true,
      image_base64: "data:image/png;base64,AAAA",
    });
  });

  it("accepts exactly 2,000 characters and rejects overflow without truncation", () => {
    const boundary = "x".repeat(MAX_USER_MESSAGE_CHARACTERS);
    const overflow = `${boundary}x`;

    expect(getUserMessageValidationError(boundary)).toBeNull();
    expect(createChatRequest([], boundary).message).toBe(boundary);
    expect(getUserMessageValidationError(overflow)).toBe(USER_MESSAGE_TOO_LONG_ERROR);
    expect(() => createChatRequest([], overflow)).toThrow(USER_MESSAGE_TOO_LONG_ERROR);
    expect(overflow).toHaveLength(MAX_USER_MESSAGE_CHARACTERS + 1);
  });
});
