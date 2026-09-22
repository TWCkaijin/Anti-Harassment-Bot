import { render, screen, within } from "@testing-library/react";
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
