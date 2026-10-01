import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConversationMessage } from "../hooks/useConversation";
import { I18nProvider } from "../i18n";
import MessageItem from "./MessageItem";

const base: ConversationMessage = { id: "reply-1", role: "user", content: "事情發生在哪裡？\n在學校", timestamp: 1 };
const renderMessage = (message: ConversationMessage) => render(<I18nProvider><MessageItem message={message} /></I18nProvider>);

describe("MessageItem natural reply rendering", () => {
  const sections: NonNullable<ConversationMessage["answerSections"]> = [
    { kind: "direction", text: "舊方向段落", source_ids: [] },
    { kind: "basis", text: "舊依據段落", source_ids: [] },
    { kind: "next_steps", text: "舊步驟段落", source_ids: [] },
  ];
  it.each([undefined, 2, 3, 4] as const)("prefers reply content over saved sections for contract %s", contractVersion => {
    const message = { ...base, role: "assistant" as const, contractVersion, content: "可以先保留相關紀錄，再依您的需要討論處理方式。", answerSections: sections };
    const snapshot = structuredClone(message);
    renderMessage(message);
    expect(screen.getByText(message.content)).toBeVisible();
    expect(screen.queryByText("舊方向段落")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(message).toEqual(snapshot);
  });
  it.each([undefined, 2, 3, 4] as const)("uses section text without inserted headings only when reply is empty for contract %s", contractVersion => {
    renderMessage({ ...base, role: "assistant", contractVersion, content: "  \n ", answerSections: sections });
    for (const section of sections) expect(screen.getByText(section.text)).toBeVisible();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });
  it("preserves headings explicitly present in the model reply", () => {
    renderMessage({ ...base, role: "assistant", content: "## 您詢問的程序\n\n可以向受理窗口確認。", answerSections: sections });
    expect(screen.getByRole("heading", { name: "您詢問的程序" })).toBeVisible();
    expect(screen.queryByText("舊方向段落")).not.toBeInTheDocument();
  });
});

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

  it("places the observed processing trace below the answer without duplicating the old streaming label", () => {
    render(<I18nProvider><MessageItem streamingStatus="舊進度文字" message={{
      ...base, role: "assistant", content: "部分答案", isStreaming: true,
      processingTrace: { outcome: "running", duration_ms: 300, steps: [{ phase: "generating", elapsed_ms: 300, attempt: 1 }] },
    }} /></I18nProvider>);
    const toggle = screen.getByRole("button", { name: "處理過程" });
    expect(screen.getByText("部分答案").compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("正在接收回覆內容")).toBeVisible();
    expect(screen.queryByText("舊進度文字")).not.toBeInTheDocument();
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("keeps the no-trace streaming fallback and does not fabricate history for older messages", () => {
    const { rerender } = render(<I18nProvider><MessageItem streamingStatus="舊進度文字" message={{ ...base, role: "assistant", isStreaming: true }} /></I18nProvider>);
    expect(screen.getByRole("status")).toHaveTextContent("舊進度文字");
    expect(screen.queryByRole("button", { name: "處理過程" })).not.toBeInTheDocument();
    rerender(<I18nProvider><MessageItem message={{ ...base, role: "assistant" }} /></I18nProvider>);
    expect(screen.queryByRole("button", { name: "處理過程" })).not.toBeInTheDocument();
  });

  it("does not render a processing trace on user messages", () => {
    renderMessage({ ...base, processingTrace: { outcome: "complete", duration_ms: 500, steps: [] } });
    expect(screen.queryByRole("button", { name: "處理過程" })).not.toBeInTheDocument();
  });

  it("keeps the same processing panel through empty and growing replies, respecting a manual collapse", () => {
    const message: ConversationMessage = {
      ...base, role: "assistant", content: "", isStreaming: true,
      processingTrace: { outcome: "running", duration_ms: 0, steps: [{ phase: "waiting_model", elapsed_ms: 0, attempt: 0 }] },
    };
    const { rerender } = renderMessage(message);
    const toggle = screen.getByRole("button", { name: "處理過程" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("正在等待模型回應")).toBeVisible();
    const panelId = toggle.getAttribute("aria-controls");
    fireEvent.click(toggle);
    rerender(<I18nProvider><MessageItem message={{ ...message, content: "逐步出現的回答" }} /></I18nProvider>);
    expect(screen.getAllByRole("button", { name: "處理過程" })).toEqual([toggle]);
    expect(toggle).toHaveAttribute("aria-controls", panelId);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("逐步出現的回答").compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("status")).toHaveTextContent("處理中");
  });

  it.each(["cancelled", "error"] as const)("keeps partial content and the %s warning above analysis", outcome => {
    const partial = "先保留這段尚未完成的說明。";
    const message: ConversationMessage = {
      ...base, role: "assistant", content: partial, isStreaming: true,
      processingTrace: { outcome: "running", duration_ms: 100, steps: [{ phase: "generating", elapsed_ms: 100, attempt: 0 }] },
      analysis: [{ stage: "understanding", summary: "合成分析資訊", facts: [], source_labels: [], limitations: [] }],
    };
    const { rerender } = renderMessage(message);
    const toggle = screen.getByRole("button", { name: "分析摘要與依據" });
    rerender(<I18nProvider><MessageItem message={{
      ...message, isStreaming: false, isCancelled: outcome === "cancelled", isError: outcome === "error",
      interruptionReason: outcome === "error" ? "連線中斷，以上內容尚未完成。" : undefined,
      processingTrace: { ...message.processingTrace!, outcome },
    }} /></I18nProvider>);
    const answer = screen.getByText(partial);
    const warning = outcome === "error" ? screen.getByRole("alert") : screen.getByText("使用者已終止回覆，以上內容尚未完成");
    expect(answer).toBeVisible();
    expect(screen.getAllByText(partial)).toHaveLength(1);
    expect(answer.compareDocumentPosition(warning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(warning.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "分析摘要與依據" })).toEqual([toggle]);
    expect(screen.getByRole("status")).toHaveTextContent(outcome === "error" ? "處理中斷" : "已停止");
    expect(screen.getByText("合成分析資訊")).toBeVisible();
  });
});

describe("MessageItem Markdown and analysis ordering", () => {
  it.each(["ordered", "unordered"] as const)("preserves %s list structure and bold labels from streaming through completion", listType => {
    const labels = ["條件甲", "條件乙", "條件丙"];
    const units = labels.map((label, index) => `${listType === "ordered" ? `${index + 1}.` : "-"} **${label}**：合成條件說明。`);
    const message: ConversationMessage = {
      ...base, role: "assistant", contractVersion: 4, isStreaming: true,
      content: ["可以先依情境確認方向。", ...units.slice(0, 2)].join("\n\n"),
      processingTrace: { outcome: "running", duration_ms: 100, steps: [{ phase: "generating", elapsed_ms: 100, attempt: 0 }] },
    };
    const { container, rerender } = renderMessage(message);
    const markdown = () => container.querySelector<HTMLElement>(".markdown-message")!;
    expect(within(markdown()).getAllByRole("listitem")).toHaveLength(2);
    const content = ["可以先依情境確認方向。", ...units, "下一步可以先整理已知資訊。"].join("\n\n");
    rerender(<I18nProvider><MessageItem message={{ ...message, content }} /></I18nProvider>);
    const list = within(markdown()).getByRole("list");
    expect(list.tagName).toBe(listType === "ordered" ? "OL" : "UL");
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    if (listType === "ordered") expect((list as HTMLOListElement).start).toBe(1);
    for (const label of labels) expect(within(list).getByText(label).tagName).toBe("STRONG");
    const streamedMarkup = markdown().innerHTML;
    const toggle = screen.getByRole("button", { name: "處理過程" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    rerender(<I18nProvider><MessageItem message={{ ...message, content, isStreaming: false, processingTrace: { ...message.processingTrace!, outcome: "complete" } }} /></I18nProvider>);
    expect(markdown().innerHTML).toBe(streamedMarkup);
    expect(screen.getByRole("button", { name: "處理過程" })).toBe(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(markdown()).queryByRole("heading")).not.toBeInTheDocument();
  });

  it("places one collapsed analysis and reasoning panel after the answer and before sources", () => {
    renderMessage({
      ...base, role: "assistant", contractVersion: 4, content: "可以先依實際情境確認處理方向。",
      analysis: [{ stage: "sources", summary: "合成來源分析", facts: [], source_labels: ["合成資料"], limitations: [] }],
      reasoning: [{ text: "合成公開推理摘要", kind: "summary", stage: "answer" }],
      ragUsed: { status: true, sources: [{ type: "law", label: "合成資料" }] },
    });
    const answer = screen.getByText("可以先依實際情境確認處理方向。");
    const toggle = screen.getByRole("button", { name: "分析摘要與依據" });
    const source = screen.getByRole("button", { name: "本次引用來源：法律條文" });
    expect(screen.getAllByRole("button", { name: "分析摘要與依據" })).toHaveLength(1);
    expect(answer.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toggle.compareDocumentPosition(source) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("合成來源分析")).not.toBeVisible();
    expect(screen.getByText("合成公開推理摘要")).not.toBeVisible();
    fireEvent.click(toggle);
    expect(screen.getByText("合成來源分析")).toBeVisible();
    expect(screen.getByText("合成公開推理摘要")).toBeVisible();
    expect(answer).toBeVisible();
  });
});


const resourceActions: ConversationMessage["actionButtons"] = [
  { action: "tel", label: "撥打 113 保護專線", phone_number: "113" },
  { action: "url", label: "前往官方網站", url: "https://example.org/" },
];

describe("MessageItem resource action placement", () => {
  it("labels v3 sources as actual citations and dates as law-version metadata", () => {
    renderMessage({ ...base, role: "assistant", content: "依所引條文說明。", contractVersion: 3, ragUsed: { status: true, sources: [{ label: "測試法第1條", type: "law", article: "合成條文", effective_date: "2024-03-08", promulgated_date: "2023-08-16", checked_at: "2026-09-30", effective_date_scope: "law_version_not_article_specific", promulgated_date_scope: "law_latest_amendment_not_article_specific", effective_note: "此日期並非逐條施行資訊" }] } });
    fireEvent.click(screen.getByRole("button", { name: "本次引用來源：法律條文" }));
    expect(screen.getByText("法規版本施行日期：2024-03-08")).toBeVisible();
    expect(screen.getByText("法規版本公布日期：2023-08-16")).toBeVisible();
    expect(screen.getByText("此日期並非逐條施行資訊")).toBeVisible();
    expect(screen.queryByText(/law_version_not_article_specific/)).not.toBeInTheDocument();
  });
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
