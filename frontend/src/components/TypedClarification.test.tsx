import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import TypedClarification from "./TypedClarification";
const question = { question_id: "case.other_role.2", fact_key: "other_role" as const, question: "對方與您是什麼關係？", reason: "這有助於了解適合的處理管道。", options: [{ label: "主管", value: "主管" }] };
beforeEach(() => localStorage.clear());
it.each([["不確定", "unknown"], ["暫不提供", "declined"]])("sends %s as an explicit status without a fabricated value", (label, status) => {
  const send = vi.fn();
  render(<I18nProvider><TypedClarification clarification={question} revision={2} isLoading={false} onSend={send} onHide={vi.fn()} /></I18nProvider>);
  fireEvent.click(screen.getByRole("radio", { name: label }));
  fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
  expect(send).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ factsRevision: 2, clarificationAnswer: { question_id: question.question_id, fact_key: "other_role", status } }));
  fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
  expect(send).toHaveBeenCalledOnce();
});
it("does not permit a loading question to submit", () => {
  render(<I18nProvider><TypedClarification clarification={question} revision={2} isLoading onSend={vi.fn()} onHide={vi.fn()} /></I18nProvider>);
  expect(screen.getByRole("radio", { name: "主管" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
});
