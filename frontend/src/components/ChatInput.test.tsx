import { fireEvent, render, screen, within } from "@testing-library/react";
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
  interactionMode: "clarify",
  clarifyingQuestions: ["您想先了解哪一項？"],
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
    expect(screen.queryByRole("region", { name: "下一步建議" })).not.toBeInTheDocument();
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

  it.each([
    {
      name: "answer with suggestions",
      data: { interactionMode: "answer", actionButtons: [], clarifyingQuestions: [], suggestedReplies: ["了解申訴流程", "查看求助資源"] },
      label: "了解申訴流程",
    },
    {
      name: "answer with configured options",
      data: { interactionMode: "answer", actionButtons: replyPrompt.actionButtons, clarifyingQuestions: [], suggestedReplies: ["不應優先顯示的建議"] },
      label: "了解資源",
    },
    {
      name: "clarify without valid questions",
      data: { interactionMode: "clarify", actionButtons: [], clarifyingQuestions: [" "], suggestedReplies: ["了解申訴流程", "查看求助資源"] },
      label: "了解申訴流程",
    },
    {
      name: "answer with leftover question metadata",
      data: { interactionMode: "answer", actionButtons: [], clarifyingQuestions: ["不該顯示的追問？"], suggestedReplies: ["了解申訴流程", "查看求助資源"] },
      label: "了解申訴流程",
    },
  ] satisfies { name: string; data: Partial<ConversationMessage>; label: string }[])(
    "keeps the composer and horizontal next-step chips for $name",
    ({ data, label }) => {
      render(<I18nProvider><ChatInput onSend={vi.fn()} replyPrompt={{ ...replyPrompt, ...data }} /></I18nProvider>);
      const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
      const chips = screen.getByRole("region", { name: "下一步建議" });
      expect(composer).toBeVisible();
      expect(chips).toBeVisible();
      expect(chips.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(chips.firstElementChild).toHaveClass("flex-nowrap", "overflow-x-auto");
      expect(within(chips).getByRole("button", { name: label })).toHaveClass("whitespace-nowrap", "shrink-0");
      expect(screen.getByRole("button", { name: "上傳圖片" })).toBeVisible();
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.queryByRole("textbox", { name: "其他補充" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "隱藏" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "顯示選單" })).not.toBeInTheDocument();
      expect(screen.queryByText("不該顯示的追問？")).not.toBeInTheDocument();
    },
  );

  it("sends a configured next-step value directly without reply context and prevents repeated clicks", () => {
    const onSend = vi.fn();
    const prompt = { ...replyPrompt, interactionMode: "answer" as const, suggestedReplies: ["不應優先顯示的建議"] };
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={prompt} /></I18nProvider>);
    expect(screen.queryByRole("button", { name: "不應優先顯示的建議" })).not.toBeInTheDocument();
    const suggestion = screen.getByRole("button", { name: "了解資源" });
    fireEvent.click(suggestion);
    fireEvent.click(suggestion);
    fireEvent.click(screen.getByRole("button", { name: "繼續說明" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("我想先了解可使用的資源");
    expect(suggestion).toBeDisabled();

    rerender(<I18nProvider><ChatInput onSend={onSend} replyPrompt={{ ...prompt, id: "assistant-2" }} /></I18nProvider>);
    expect(screen.getByRole("button", { name: "了解資源" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "繼續說明" }));
    expect(onSend).toHaveBeenLastCalledWith("我想繼續說明");
    expect(onSend).toHaveBeenCalledTimes(2);
  });

  it("disables next-step submission during loading and retains the ordinary stop control", () => {
    const onSend = vi.fn();
    const onStop = vi.fn();
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} onStop={onStop} suggestedReplies={["查看資源"]} isLoading /></I18nProvider>);
    const suggestion = screen.getByRole("button", { name: "查看資源" });
    expect(suggestion).toBeDisabled();
    fireEvent.click(suggestion);
    fireEvent.click(screen.getByRole("button", { name: "停止回覆" }));
    expect(onSend).not.toHaveBeenCalled();
    expect(onStop).toHaveBeenCalledOnce();

    rerender(<I18nProvider><ChatInput onSend={onSend} onStop={onStop} suggestedReplies={["查看資源"]} /></I18nProvider>);
    fireEvent.click(screen.getByRole("button", { name: "查看資源" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("查看資源");
  });

  it("places ordinary resource links above the available composer without creating questions", () => {
    const prompt: ConversationMessage = {
      ...replyPrompt, interactionMode: "answer", clarifyingQuestions: [], suggestedReplies: ["查看其他資源"],
      actionButtons: [{ action: "tel", label: "撥打諮詢專線", phone_number: "113" }, { action: "url", label: "官方網站", url: "https://example.org/" }],
    };
    render(<I18nProvider><ChatInput onSend={vi.fn()} replyPrompt={prompt} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    const phone = screen.getByRole("link", { name: "撥打諮詢專線" });
    expect(phone).toHaveAttribute("href", "tel:113");
    expect(phone.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("link", { name: "官方網站（另開新分頁）" })).toHaveAttribute("href", "https://example.org/");
    expect(composer).toBeVisible();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});
