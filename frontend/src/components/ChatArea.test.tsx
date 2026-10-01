import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationMessage } from "../hooks/useConversation";
import { I18nProvider } from "../i18n";
import { checkHealth, type OptionsActionButton } from "../services/api";
import ChatArea from "./ChatArea";

vi.mock("../services/api", () => ({ checkHealth: vi.fn() }));

const nextStep: OptionsActionButton = {
  action: "options",
  id: "choose_next_step",
  label: "選擇下一步",
  title: "您想先了解哪一項？",
  options: [
    { label: "看看資源", value: "我想先了解可使用的資源" },
    { label: "繼續說明", value: "我想先繼續描述目前的情況" },
  ],
};

function assistantMessage(overrides: Partial<ConversationMessage> = {}): ConversationMessage {
  return {
    id: "assistant-1",
    role: "assistant",
    content: "我會依照您的需要，協助您了解下一步。",
    timestamp: 1,
    interactionMode: "clarify",
    clarifyingQuestions: [nextStep.title],
    actionButtons: [
      nextStep,
      { action: "tel", label: "撥打諮詢專線", phone_number: "113" },
      { action: "url", label: "查看官方網站", url: "https://www.pthg.gov.tw/" },
    ],
    ...overrides,
  };
}

function chat(messages: ConversationMessage[], onSend = vi.fn(), isLoading = false, retryStatus?: string) {
  return (
    <I18nProvider>
      <ChatArea
        messages={messages}
        onSend={onSend}
        isLoading={isLoading}
        retryStatus={retryStatus}
        onOpenSidebar={vi.fn()}
        onStop={vi.fn()}
        backendConnected
      />
    </I18nProvider>
  );
}

const originalScrollIntoView = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");

beforeAll(() => {
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

afterAll(() => {
  if (originalScrollIntoView) {
    Object.defineProperty(Element.prototype, "scrollIntoView", originalScrollIntoView);
  } else {
    Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  }
});

beforeEach(() => {
  localStorage.clear();
  vi.mocked(checkHealth).mockResolvedValue({
    status: "ok", timestamp: "2026-09-22T00:00:00Z", version: "test", environment: "test",
  });
});

describe("ChatArea follow-up integration", () => {
  it.each([null, false])("blocks the first send while connection is %s and preserves the typed draft", connected => {
    const onSend = vi.fn();
    const props = { messages: [], onSend, onOpenSidebar: vi.fn(), onStop: vi.fn(), isLoading: false };
    const { rerender } = render(<I18nProvider><ChatArea {...props} backendConnected={connected} /></I18nProvider>);
    const composer = screen.getByRole("textbox");
    fireEvent.change(composer, { target: { value: "第一個問題" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(composer).toHaveValue("第一個問題");
    rerender(<I18nProvider><ChatArea {...props} backendConnected contractVersion={4} /></I18nProvider>);
    expect(composer).toHaveValue("第一個問題");
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("第一個問題", undefined, undefined);
  });
  it("offers connection recovery without sending chat and keeps a saved summary safe while checking", () => {
    const onSend = vi.fn();
    const onReconnect = vi.fn();
    const props = { messages: [assistantMessage({ interactionMode: "answer", clarifyingQuestions: [], actionButtons: [] })], onSend, onReconnect, onOpenSidebar: vi.fn(), onStop: vi.fn(), isLoading: false };
    const { rerender } = render(<I18nProvider><ChatArea {...props} backendConnected={false} summaryRequiresConnection /></I18nProvider>);
    expect(screen.getByRole("status")).toHaveTextContent("無法連線到服務");
    expect(screen.queryByText(/目前服務尚未支援這份情境摘要/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("請描述您的狀況或提出問題…"), { target: { value: "接續原本的問題" } });
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新連線" }));
    expect(onReconnect).toHaveBeenCalledOnce();
    expect(onSend).not.toHaveBeenCalled();

    rerender(<I18nProvider><ChatArea {...props} backendConnected={null} summaryRequiresConnection /></I18nProvider>);
    expect(screen.getByRole("status")).toHaveTextContent("正在確認服務連線");
    expect(screen.queryByRole("button", { name: "重新連線" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();

    rerender(<I18nProvider><ChatArea {...props} backendConnected /></I18nProvider>);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeEnabled();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toHaveValue("接續原本的問題");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("explains an incompatible saved summary and disables sending without exposing its editor", () => {
    render(<I18nProvider><ChatArea messages={[assistantMessage({ interactionMode: "answer", clarifyingQuestions: [], actionButtons: [] })]} onSend={vi.fn()} onOpenSidebar={vi.fn()} onStop={vi.fn()} isLoading={false} backendConnected incompatibleSummary /></I18nProvider>);
    expect(screen.getByRole("alert")).toHaveTextContent("目前服務尚未支援這份情境摘要");
    expect(screen.queryByText("目前了解的情況")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("請描述您的狀況或提出問題…"), { target: { value: "不要忽略更正" } });
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
  });
  it("keeps resource actions in the reply while the latest questions replace the footer composer", async () => {
    const message = assistantMessage();
    const { container } = render(chat([message]));
    await screen.findByText("已連線");

    const footer = container.querySelector("footer");
    expect(footer).not.toBeNull();
    const controls = within(footer!);
    expect(controls.getByText(nextStep.title)).toBeInTheDocument();
    expect(controls.getByRole("radio", { name: /看看資源/ })).toBeInTheDocument();
    expect(controls.getByRole("radio", { name: /繼續說明/ })).toBeInTheDocument();
    expect(controls.queryByRole("link")).not.toBeInTheDocument();
    const composer = controls.getByPlaceholderText("請描述您的狀況或提出問題…");
    expect(composer).not.toBeVisible();
    expect(controls.queryByRole("button", { name: "傳送訊息" })).not.toBeInTheDocument();
    expect(controls.queryByRole("button", { name: "上傳圖片" })).not.toBeInTheDocument();

    const messageArea = screen.getByText(message.content).closest("section");
    expect(messageArea).not.toBeNull();
    expect(within(messageArea!).queryByRole("radio")).not.toBeInTheDocument();
    const phone = within(messageArea!).getByRole("link", { name: "撥打諮詢專線" });
    expect(phone).toHaveAttribute("href", "tel:113");
    expect(within(messageArea!).getByRole("link", { name: "查看官方網站（另開新分頁）" }))
      .toHaveAttribute("href", "https://www.pthg.gov.tw/");
    expect(within(messageArea!).queryByText(nextStep.title)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(controls.getByRole("button", { name: "隱藏選項" }));
    expect(phone).toBeVisible();
    expect(controls.queryByRole("link")).not.toBeInTheDocument();
    expect(composer).toBeVisible();
    fireEvent.click(controls.getByRole("button", { name: "顯示選單" }));
    expect(phone).toBeVisible();
    expect(screen.getAllByRole("link", { name: "撥打諮詢專線" })).toHaveLength(1);
  });

  it("preserves earlier resource actions with their replies without showing their old questions", async () => {
    const earlier = assistantMessage({
      id: "assistant-old",
      content: "先前的回覆仍會保留。",
      clarifyingQuestions: ["這是先前的問題？"],
      actionButtons: [{ action: "tel", label: "舊的聯絡方式", phone_number: "110" }],
    });
    render(chat([earlier, assistantMessage()]));
    await screen.findByText("已連線");

    expect(screen.getByText(earlier.content)).toBeInTheDocument();
    expect(screen.queryByText("這是先前的問題？")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "舊的聯絡方式" })).toHaveAttribute("href", "tel:110");
    expect(screen.getAllByRole("radio", { name: /看看資源/ })).toHaveLength(1);
  });

  it("sends the configured option value only after the user submits their selection", async () => {
    const onSend = vi.fn();
    render(chat([assistantMessage()], onSend));
    await screen.findByText("已連線");

    fireEvent.click(screen.getByRole("radio", { name: /看看資源/ }));
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /送出|傳送回覆/ }));

    expect(onSend).toHaveBeenCalledExactlyOnceWith(nextStep.options[0].value, undefined, undefined, {
      answers: [{ question: nextStep.title, answer: nextStep.options[0].value }],
    });
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "顯示選單" })).not.toBeInTheDocument();
  });

  it("accepts an Other answer with its question context through the same send callback", async () => {
    const onSend = vi.fn();
    render(chat([assistantMessage()], onSend));
    await screen.findByText("已連線");

    fireEvent.click(screen.getByRole("radio", { name: /其他/ }));
    fireEvent.change(screen.getByRole("textbox", { name: /其他/ }), {
      target: { value: "  我想先和信任的人談談  " },
    });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /送出|傳送回覆/ }));

    expect(onSend).toHaveBeenCalledExactlyOnceWith(`${nextStep.title}\n我想先和信任的人談談`, undefined, undefined, {
      answers: [{ question: nextStep.title, answer: "我想先和信任的人談談" }],
    });
  });

  it("resets the selected choice and Other draft when a different assistant message becomes current", async () => {
    const onSend = vi.fn();
    const previous = assistantMessage();
    const { rerender } = render(chat([previous], onSend));
    await screen.findByText("已連線");

    fireEvent.click(screen.getByRole("radio", { name: /其他/ }));
    fireEvent.change(screen.getByRole("textbox", { name: /其他/ }), {
      target: { value: "只屬於上一個問題的草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    rerender(chat([previous, assistantMessage({ id: "assistant-2" })], onSend));

    expect(screen.queryByDisplayValue("只屬於上一個問題的草稿")).not.toBeInTheDocument();
    for (const choice of screen.getAllByRole("radio")) expect(choice).not.toBeChecked();
    expect(screen.getByRole("button", { name: /送出|傳送回覆/ })).toBeDisabled();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).not.toBeVisible();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("blocks submitting a selected follow-up while a response is loading", async () => {
    const onSend = vi.fn();
    const messages = [assistantMessage()];
    const { rerender } = render(chat(messages, onSend));
    await screen.findByText("已連線");
    fireEvent.click(screen.getByRole("radio", { name: /看看資源/ }));
    rerender(chat(messages, onSend, true));

    const submit = screen.getByRole("button", { name: /送出|傳送回覆/ });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends ordinary next-step suggestions immediately without creating quoted answer metadata", async () => {
    const onSend = vi.fn();
    render(chat([assistantMessage({
      actionButtons: [], interactionMode: "answer", clarifyingQuestions: [],
      suggestedReplies: ["我想了解申訴流程", "我想先照顧自己"],
    })], onSend));
    await screen.findByText("已連線");

    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("region", { name: "下一步建議" })).getByRole("button", { name: "我想了解申訴流程" }));

    expect(onSend).toHaveBeenCalledExactlyOnceWith("我想了解申訴流程");
  });

  it("keeps answer-mode resources with the reply and next-step choices above the composer", async () => {
    const message = assistantMessage({
      interactionMode: "answer", clarifyingQuestions: [],
    });
    const { container } = render(chat([message]));
    await screen.findByText("已連線");

    const footer = within(container.querySelector("footer")!);
    const messageArea = within(screen.getByText(message.content).closest("section")!);
    expect(footer.queryByRole("link")).not.toBeInTheDocument();
    expect(footer.getByRole("button", { name: "看看資源" })).toBeVisible();
    expect(footer.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(messageArea.getByRole("link", { name: "撥打諮詢專線" })).toHaveAttribute("href", "tel:113");
    expect(messageArea.getByRole("link", { name: "查看官方網站（另開新分頁）" }))
      .toHaveAttribute("href", "https://www.pthg.gov.tw/");
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it.each([
    { kind: "user", role: "user" as const, isError: false, isCancelled: false },
    { kind: "error", role: "assistant" as const, isError: true, isCancelled: false },
    { kind: "cancelled", role: "assistant" as const, isError: false, isCancelled: true },
  ])("clears active follow-ups when the latest message is $kind", async ({ role, isError, isCancelled }) => {
    const previous = assistantMessage();
    const { rerender } = render(chat([previous]));
    await screen.findByText("已連線");
    expect(screen.getByRole("radio", { name: /看看資源/ })).toBeInTheDocument();

    rerender(chat([previous, assistantMessage({
      id: "latest-message", role, isError, isCancelled, content: "新的訊息。",
    })]));

    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByText(nextStep.title)).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "撥打諮詢專線" })).toHaveLength(1);
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeInTheDocument();
  });
});

describe("ChatArea streaming presentation", () => {
  it("renders the first reply character and grows horizontal suggestions before done", async () => {
    const onSend = vi.fn();
    const stream = assistantMessage({
      content: "我", isStreaming: true,
      streamingGuidance: { interaction_mode: "answer", suggested_replies: ["了"] },
    });
    const { rerender } = render(chat([stream], onSend, true));
    expect(screen.getByText("我")).toBeVisible();
    const suggestions = screen.getByRole("region", { name: "下一步建議" });
    const first = within(suggestions).getByRole("button", { name: "了" });
    expect(first).toBeDisabled();
    expect(first.parentElement).toHaveClass("flex-nowrap", "overflow-x-auto");
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.getByRole("button", { name: "停止回覆" })).toBeEnabled();
    fireEvent.click(first);
    expect(onSend).not.toHaveBeenCalled();

    rerender(chat([{ ...stream, content: "我會陪您整理", streamingGuidance: {
      interaction_mode: "answer", suggested_replies: ["了解申訴流程", "整理"],
    } }], onSend, true));
    expect(screen.getByText("我會陪您整理")).toBeVisible();
    expect(screen.getByRole("button", { name: "了解申訴流程" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "整理" })).toBeDisabled();
    expect(screen.queryByText("AI 需要更多您的資訊")).not.toBeInTheDocument();

    rerender(chat([assistantMessage({
      ...stream, isStreaming: false, streamingGuidance: undefined, actionButtons: [],
      interactionMode: "answer", clarifyingQuestions: [], suggestedReplies: ["了解申訴流程", "整理事件"],
    })], onSend));
    fireEvent.click(screen.getByRole("button", { name: "整理事件" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("整理事件");
    await screen.findByText("已連線");
  });

  it("streams a clarification only after its mode and question arrive, then enables the final choices", async () => {
    const onSend = vi.fn();
    const stream = assistantMessage({ content: "我想先確認。", isStreaming: true, streamingGuidance: {
      interaction_mode: "clarify", suggested_replies: ["在學"],
    } });
    const { rerender } = render(chat([stream], onSend, true));
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "下一步建議" })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();

    rerender(chat([{ ...stream, streamingGuidance: {
      interaction_mode: "clarify", clarifying_questions: ["事"],
    } }], onSend, true));
    expect(screen.getByText("事")).toBeVisible();
    expect(screen.getByRole("region", { name: "AI 需要更多您的資訊" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).not.toBeVisible();
    expect(screen.getByRole("button", { name: "停止回覆" })).toBeEnabled();

    rerender(chat([{ ...stream, streamingGuidance: {
      interaction_mode: "clarify", clarifying_questions: ["事情發生在哪裡？"], suggested_replies: ["在學校", "在工作"],
    } }], onSend, true));
    expect(screen.getByText("事情發生在哪裡？")).toBeVisible();
    expect(screen.getByRole("radio", { name: "在工作" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "在學校" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "其他補充" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    expect(screen.getByText("正在產生問題與選項…")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.getByRole("button", { name: "停止回覆" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: "下一步建議" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "顯示選單" }));

    rerender(chat([assistantMessage({
      ...stream, isStreaming: false, streamingGuidance: undefined, actionButtons: [],
      interactionMode: "clarify", clarifyingQuestions: ["事情發生在哪裡？"], suggestedReplies: ["在學校", "在工作場所"],
    })], onSend));
    const choice = screen.getByRole("radio", { name: "在工作場所" });
    expect(choice).toBeEnabled();
    fireEvent.click(choice);
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("事情發生在哪裡？\n在工作場所", undefined, undefined, {
      answers: [{ question: "事情發生在哪裡？", answer: "在工作場所" }],
    });
    await screen.findByText("已連線");
  });

  it.each(["error", "cancelled", "different-session"])("removes the streaming preview after %s", async kind => {
    const stream = assistantMessage({ content: "部分回覆", isStreaming: true, streamingGuidance: {
      interaction_mode: "clarify", clarifying_questions: ["事情發生在"], suggested_replies: ["在學"],
    } });
    const { rerender } = render(chat([stream], vi.fn(), true));
    expect(screen.getByText("事情發生在")).toBeVisible();
    rerender(chat([kind === "different-session"
      ? { id: "other-session-user", role: "user", timestamp: 1, content: "另一個對話" }
      : { ...stream, isStreaming: false, isError: kind === "error", isCancelled: kind === "cancelled" }]));
    expect(screen.queryByText("事情發生在")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    await screen.findByText("已連線");
  });

  it("replaces the initial typing indicator with the growing reply and waits for final metadata", async () => {
    const { rerender, container } = render(chat([{ id: "user-stream", role: "user", content: "請說明", timestamp: 1 }], vi.fn(), true));
    expect(container.querySelectorAll(".typing-dot")).toHaveLength(3);
    const stream = assistantMessage({ content: "正在逐步顯示的回答", isStreaming: true });
    rerender(chat([stream], vi.fn(), true));
    expect(screen.getByText("正在逐步顯示的回答")).toBeInTheDocument();
    expect(container.querySelectorAll(".typing-dot")).toHaveLength(0);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "停止回覆" })).toBeInTheDocument();
    rerender(chat([{ ...stream, isStreaming: false }], vi.fn(), false));
    expect(screen.getByRole("radio", { name: /看看資源/ })).toBeInTheDocument();
    await screen.findByText("已連線");
  });

  it("keeps the actual phase visible in a single status indicator throughout streaming", async () => {
    const user: ConversationMessage = { id: "progress-user", role: "user", content: "請說明", timestamp: 1 };
    const { rerender, container } = render(chat([user], vi.fn(), true, "正在檢索資料庫"));
    expect(screen.getByRole("status")).toHaveTextContent("正在檢索資料庫");
    const stream = assistantMessage({ content: "已收到的回答", isStreaming: true });
    rerender(chat([user, stream], vi.fn(), true, "正在生成回覆"));
    expect(screen.getByRole("status")).toHaveTextContent("正在生成回覆");
    expect(container.querySelectorAll(".typing-dot")).toHaveLength(0);
    rerender(chat([user, stream], vi.fn(), true, "正在整理回覆"));
    expect(screen.getByRole("status")).toHaveTextContent("正在整理回覆");
    expect(screen.getByText("已收到的回答")).toBeVisible();
    rerender(chat([user, { ...stream, isStreaming: false }], vi.fn(), false));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await screen.findByText("已連線");
  });
});

describe("ChatArea v4 layout and current summary", () => {
  it("shows the brand only before the first message and keeps compact connection controls", () => {
    const { rerender } = render(chat([]));
    const title = screen.getByRole("heading", { name: "溫暖守護" }).textContent!;
    rerender(chat([{ id: "u", role: "user", content: "請陪我整理", timestamp: 1 }]));
    expect(screen.queryByRole("heading", { name: title })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "開啟側邊欄" })).toBeVisible();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeVisible();
  });

  it("offers one current summary beneath the latest valid avatar, then keeps edits reachable", () => {
    const stop = vi.fn(), save = vi.fn();
    const first = assistantMessage({ id: "a1", content: "第一則", interactionMode: "answer", clarifyingQuestions: [], actionButtons: [] });
    const second = { ...first, id: "a2", content: "第二則" };
    const caseFacts = { schema_version: 3 as const, revision: 2, facts: {}, summary: "第三人稱例題：甲在公車上遭碰觸。", summary_origin: "user" as const };
    const view = (messages: ConversationMessage[]) => <I18nProvider><ChatArea messages={messages} caseFacts={caseFacts} onSaveCaseFacts={save} onSend={vi.fn()} onOpenSidebar={vi.fn()} onStop={stop} isLoading={false} backendConnected /></I18nProvider>;
    const { rerender } = render(view([first, second]));
    const button = screen.getByRole("button", { name: "查看目前摘要" });
    expect(screen.queryByText(caseFacts.summary)).not.toBeInTheDocument();
    button.focus();
    fireEvent.click(button);
    expect(screen.getByRole("dialog")).toHaveTextContent(caseFacts.summary);
    expect(stop).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "修改摘要" }));
    expect(stop).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "儲存摘要" }));
    expect(save).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
    rerender(view([{ ...first, superseded: true }, { ...second, superseded: true }]));
    fireEvent.click(screen.getByRole("button", { name: "查看目前摘要" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(caseFacts.summary);
  });
});


it("returns summary focus to the replacement control when saving supersedes the avatar", () => {
  function Harness() {
    const [saved, setSaved] = useState(false);
    return <I18nProvider><ChatArea messages={[assistantMessage({ interactionMode: "answer", clarifyingQuestions: [], actionButtons: [], superseded: saved })]} caseFacts={{ schema_version: 3, revision: 0, facts: {}, summary: "甲是同事", summary_origin: "user" }} onSaveCaseFacts={() => setSaved(true)} onSend={vi.fn()} onOpenSidebar={vi.fn()} onStop={vi.fn()} isLoading={false} backendConnected /></I18nProvider>;
  }
  render(<Harness />);
  const avatarTrigger = screen.getByRole("button", { name: "查看目前摘要" });
  avatarTrigger.focus(); fireEvent.click(avatarTrigger);
  fireEvent.click(screen.getByRole("button", { name: "修改摘要" }));
  fireEvent.click(screen.getByRole("button", { name: "儲存摘要" }));
  expect(avatarTrigger).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看目前摘要" })).toHaveFocus();
});
