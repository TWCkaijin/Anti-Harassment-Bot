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

function chat(messages: ConversationMessage[], onSend = vi.fn(), isLoading = false) {
  return (
    <I18nProvider>
      <ChatArea
        messages={messages}
        onSend={onSend}
        isLoading={isLoading}
        onOpenSidebar={vi.fn()}
        onStop={vi.fn()}
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
  it("replaces the footer composer with the latest questions, choices and resource actions", async () => {
    const message = assistantMessage();
    const { container } = render(chat([message]));
    await screen.findByText("已連線");

    const footer = container.querySelector("footer");
    expect(footer).not.toBeNull();
    const controls = within(footer!);
    expect(controls.getByText(nextStep.title)).toBeInTheDocument();
    expect(controls.getByRole("radio", { name: /看看資源/ })).toBeInTheDocument();
    expect(controls.getByRole("radio", { name: /繼續說明/ })).toBeInTheDocument();
    expect(controls.getByRole("link", { name: "撥打諮詢專線" })).toHaveAttribute("href", "tel:113");
    expect(controls.getByRole("link", { name: "查看官方網站（另開新分頁）" }))
      .toHaveAttribute("href", "https://www.pthg.gov.tw/");
    const composer = controls.getByPlaceholderText("請描述您的狀況或提出問題…");
    expect(composer).not.toBeVisible();
    expect(controls.queryByRole("button", { name: "傳送訊息" })).not.toBeInTheDocument();
    expect(controls.queryByRole("button", { name: "上傳圖片" })).not.toBeInTheDocument();

    const messageArea = screen.getByText(message.content).closest("section");
    expect(messageArea).not.toBeNull();
    expect(within(messageArea!).queryByRole("radio")).not.toBeInTheDocument();
    expect(within(messageArea!).queryByRole("link")).not.toBeInTheDocument();
    expect(within(messageArea!).queryByText(nextStep.title)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps earlier assistant messages readable without showing their old questions or actions", async () => {
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
    expect(screen.queryByRole("link", { name: "舊的聯絡方式" })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "隱藏" }));
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
    expect(screen.queryByRole("link", { name: "撥打諮詢專線" })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeInTheDocument();
  });
});

describe("ChatArea streaming presentation", () => {
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
});
