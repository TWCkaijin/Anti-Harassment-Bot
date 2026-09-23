import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConversationMessage } from "../hooks/useConversation";
import { I18nProvider } from "../i18n";
import MessageItem from "./MessageItem";

const base: ConversationMessage = { id: "reply-1", role: "user", content: "事情發生在哪裡？\n在學校", timestamp: 1 };
const renderMessage = (message: ConversationMessage) => render(<I18nProvider><MessageItem message={message} /></I18nProvider>);

describe("MessageItem quoted replies", () => {
  it("renders a muted question above its answer without duplicating the raw message", () => {
    renderMessage({ ...base, replyContext: { answers: [{ question: "事情發生在哪裡？", answer: "在學校" }] } });
    const question = screen.getByRole("blockquote", { name: "回覆的問題" });
    const answer = screen.getByText("在學校");
    expect(within(question).getByText("事情發生在哪裡？")).toBeInTheDocument();
    expect(question).not.toContainElement(answer);
    expect(question.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText("在學校")).toHaveLength(1);
  });

  it("keeps multiple questions paired with their own answers", () => {
    renderMessage({ ...base, replyContext: { answers: [
      { question: "在哪裡？", answer: "在學校" }, { question: "希望怎麼接續？", answer: "先整理資料" },
    ] } });
    const quotes = screen.getAllByRole("blockquote", { name: "回覆的問題" });
    expect(quotes).toHaveLength(2);
    expect(quotes[0].parentElement).toHaveTextContent("在哪裡？在學校");
    expect(quotes[1].parentElement).toHaveTextContent("希望怎麼接續？先整理資料");
  });

  it("keeps legacy text intact and ignores invalid saved reply metadata", () => {
    renderMessage({ ...base, replyContext: { answers: [null] } as unknown as ConversationMessage["replyContext"] });
    expect(screen.queryByRole("blockquote")).not.toBeInTheDocument();
    expect(screen.getByText(/事情發生在哪裡？/)).toHaveTextContent("事情發生在哪裡？ 在學校");
  });
});

describe("MessageItem streaming state", () => {
  it("shows partial text with a progress label while streaming", () => {
    renderMessage({ ...base, role: "assistant", content: "我會陪您", isStreaming: true });
    expect(screen.getByText("我會陪您")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在回覆");
  });

  it("preserves stopped text and clearly marks it incomplete", () => {
    renderMessage({ ...base, role: "assistant", content: "未完成的說明", isCancelled: true });
    expect(screen.getByText("未完成的說明")).toBeInTheDocument();
    expect(screen.getByText("使用者已終止回覆，以上內容尚未完成")).toBeInTheDocument();
  });

  it("shows the error below partial text without replacing or duplicating it", () => {
    renderMessage({ ...base, role: "assistant", content: "部分回覆", isError: true, interruptionReason: "回覆中斷，以上內容尚未完成" });
    expect(screen.getAllByText("部分回覆")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("回覆中斷，以上內容尚未完成");
  });
});


const resourceActions: ConversationMessage["actionButtons"] = [
  { action: "tel", label: "撥打 113 保護專線", phone_number: "113" },
  { action: "url", label: "前往官方網站", url: "https://example.org/" },
];

describe("MessageItem resource action placement", () => {
  it("keeps clickable resource actions directly below source badges and above expanded citations", () => {
    renderMessage({ ...base, role: "assistant", content: "可以使用以下求助資源。", actionButtons: resourceActions,
      ragUsed: { status: true, sources: [{ type: "remedy", label: "求助資源來源" }] },
    });
    const remedy = screen.getByRole("button", { name: /救濟管道/ });
    const actions = screen.getByRole("group", { name: "相關資源" });
    const phone = within(actions).getByRole("link", { name: "撥打 113 保護專線" });
    expect(phone).toBeVisible();
    expect(phone).toHaveAttribute("href", "tel:113");
    expect(remedy.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(remedy);
    const citation = screen.getByText("求助資源來源");
    expect(actions.compareDocumentPosition(citation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(actions).not.toContainElement(citation);
    expect(phone).toBeVisible();
    fireEvent.click(remedy);
    expect(phone).toBeVisible();
  });

  it("also shows generic resource buttons without retrieval metadata, and hides unfinished actions", () => {
    const message: ConversationMessage = { ...base, role: "assistant", content: "這裡有協助管道。", actionButtons: resourceActions };
    const { rerender } = renderMessage(message);
    expect(screen.getByRole("link", { name: "前往官方網站（另開新分頁）" })).toHaveAttribute("href", "https://example.org/");
    rerender(<I18nProvider><MessageItem message={{ ...message, isStreaming: true }} /></I18nProvider>);
    expect(screen.queryByRole("group", { name: "相關資源" })).not.toBeInTheDocument();
    rerender(<I18nProvider><MessageItem message={{ ...message, isCancelled: true }} /></I18nProvider>);
    expect(screen.queryByRole("group", { name: "相關資源" })).not.toBeInTheDocument();
  });
});
