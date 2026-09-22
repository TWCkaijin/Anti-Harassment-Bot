import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n";
import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "../hooks/conversationHistory";
import ChatInput from "./ChatInput";
import type { ConversationMessage } from "../hooks/useConversation";

const replyPrompt: ConversationMessage = {
  id: "assistant-1",
  role: "assistant",
  content: "我會陪您一起了解接下來的選擇。",
  timestamp: 1,
  actionButtons: [{
    action: "options", id: "choose_next", label: "選擇下一步", title: "您想先了解哪一項？",
    options: [{ label: "了解資源", value: "我想先了解可使用的資源" }, { label: "繼續說明", value: "我想繼續說明" }],
  }],
};

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("ChatInput", () => {
  it("submits trimmed text through the accessible send control", () => {
    const onSend = vi.fn();
    render(
      <I18nProvider>
        <ChatInput onSend={onSend} />
      </I18nProvider>,
    );

    fireEvent.change(screen.getByPlaceholderText("請描述您的狀況或提出問題…"), {
      target: { value: "  我需要協助  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));

    expect(onSend).toHaveBeenCalledWith("我需要協助", undefined, undefined);
  });

  it("keeps overflow visible and only submits at the 2,000-character boundary", () => {
    const onSend = vi.fn();
    render(
      <I18nProvider>
        <ChatInput onSend={onSend} />
      </I18nProvider>,
    );
    const textarea = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    const boundary = "x".repeat(MAX_USER_MESSAGE_CHARACTERS);
    const overflow = `${boundary}x`;

    fireEvent.change(textarea, { target: { value: overflow } });

    expect(textarea).toHaveValue(overflow);
    expect(screen.getByRole("alert")).toHaveTextContent(USER_MESSAGE_TOO_LONG_ERROR);
    expect(sendButton).toBeDisabled();
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: boundary } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);

    expect(onSend).toHaveBeenCalledWith(boundary, undefined, undefined);
  });

  it("covers the ordinary composer and restores its draft and image after hiding the menu", () => {
    const onSend = vi.fn();
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn(() => "blob:test-image");
      static revokeObjectURL = vi.fn();
    });
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    fireEvent.change(composer, { target: { value: "一般輸入框尚未送出的草稿" } });
    fireEvent.change(screen.getByLabelText("選擇圖片"), {
      target: { files: [new File(["image"], "screenshot.png", { type: "image/png" })] },
    });
    rerender(<I18nProvider><ChatInput onSend={onSend} replyPrompt={replyPrompt} /></I18nProvider>);

    expect(composer).not.toBeVisible();
    expect(screen.getByAltText("Preview")).not.toBeVisible();
    expect(screen.queryByRole("button", { name: "上傳圖片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "傳送訊息" })).not.toBeInTheDocument();
    expect(screen.getByText(/\/ 2,000/)).not.toBeVisible();
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("textbox", { name: "其他補充" }), { target: { value: "選單裡的另一份草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "隱藏" }));
    expect(composer).toBeVisible();
    expect(composer).toHaveFocus();
    expect(composer).toHaveValue("一般輸入框尚未送出的草稿");
    expect(screen.getByAltText("Preview")).toBeVisible();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "顯示選單" }));

    expect(screen.getByRole("button", { name: "隱藏" })).toHaveFocus();
    expect(screen.getByRole("radio", { name: "其他" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "其他補充" })).toHaveValue("選單裡的另一份草稿");
    expect(composer).not.toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "了解資源" }));
    fireEvent.click(screen.getByRole("button", { name: "隱藏" }));
    fireEvent.click(screen.getByRole("button", { name: "顯示選單" }));
    expect(screen.getByRole("radio", { name: "了解資源" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "其他補充" })).toHaveValue("選單裡的另一份草稿");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("restores the composer after submission and forwards structured answers alongside model context", () => {
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={replyPrompt} /></I18nProvider>);
    fireEvent.change(screen.getByRole("textbox", { name: "其他補充" }), { target: { value: "  我想先休息  " } });
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));

    expect(onSend).toHaveBeenCalledExactlyOnceWith("您想先了解哪一項？\n我想先休息", undefined, undefined, {
      answers: [{ question: "您想先了解哪一項？", answer: "我想先休息" }],
    });
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "顯示選單" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("keeps stopping available with the loading menu both expanded and hidden", () => {
    const onSend = vi.fn();
    const onStop = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} onStop={onStop} replyPrompt={replyPrompt} isLoading /></I18nProvider>);
    expect(screen.getAllByRole("button", { name: "停止回覆" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "停止回覆" }));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "隱藏" }));
    expect(screen.getAllByRole("button", { name: "停止回覆" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "停止回覆" }));
    expect(onStop).toHaveBeenCalledTimes(2);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("lets Escape hide the panel without sending or losing the selected answer", () => {
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={replyPrompt} /></I18nProvider>);
    const option = screen.getByRole("radio", { name: "了解資源" });
    fireEvent.click(option);
    fireEvent.keyDown(option, { key: "Escape" });
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "顯示選單" }));
    expect(screen.getByRole("radio", { name: "了解資源" })).toBeChecked();
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each([
    { actionButtons: [], suggestedReplies: [], clarifyingQuestions: [] },
    { actionButtons: null, suggestedReplies: null, clarifyingQuestions: null },
    { actionButtons: [{ action: "url", label: "無效連結", url: "javascript:alert(1)" }], suggestedReplies: [" ", "x".repeat(2001)], clarifyingQuestions: [null] },
  ])("keeps the composer available when the latest reply has no usable menu content: %j", (data) => {
    render(<I18nProvider><ChatInput onSend={vi.fn()} replyPrompt={{ ...replyPrompt, ...data } as unknown as ConversationMessage} /></I18nProvider>);
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "顯示選單" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "隱藏" })).not.toBeInTheDocument();
  });
});
