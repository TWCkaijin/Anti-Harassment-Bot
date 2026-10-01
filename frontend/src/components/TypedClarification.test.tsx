import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

it("limits unique choices while preserving uncertainty, privacy, and a way to supply an omitted role", () => {
  const send = vi.fn();
  const hide = vi.fn();
  const options = [
    { label: "主管", value: "主管" },
    { label: "主管", value: "另一個主管值" },
    { label: "同事", value: "同事" },
    { label: "同仁", value: " 同事 " },
    { label: "朋友", value: "朋友" },
    { label: "客戶", value: "客戶" },
    { label: "不確定", value: "不確定" },
    { label: "暫不提供", value: "暫不提供" },
    { label: "自行補充", value: "自行補充" },
  ];
  render(<I18nProvider><TypedClarification clarification={{ ...question, options }} revision={2} isLoading={false} onSend={send} onHide={hide} /></I18nProvider>);
  expect(screen.getAllByRole("radio")).toHaveLength(6);
  for (const label of ["主管", "同事", "朋友", "不確定", "暫不提供", "自行補充"]) expect(screen.getByRole("radio", { name: label })).toBeInTheDocument();
  expect(screen.queryByRole("radio", { name: "客戶" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
  expect(hide).toHaveBeenCalledOnce();
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("radio", { name: "自行補充" }));
  fireEvent.change(screen.getByRole("textbox", { name: "自行補充" }), { target: { value: "客戶" } });
  fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
  expect(send).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ clarificationAnswer: expect.objectContaining({ status: "provided", value: "客戶" }) }));
});

it("keeps the hide control available while submission is disabled", () => {
  const hide = vi.fn();
  render(<I18nProvider><TypedClarification clarification={question} revision={2} isLoading onSend={vi.fn()} onHide={hide} /></I18nProvider>);
  fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
  expect(hide).toHaveBeenCalledOnce();
});

const multiQuestion = { ...question, selection_mode: "multiple" as const, max_selections: 2, validation_token: "signed-token", context_scope: "scenario" as const, options: [{ label: "主管", value: "主管" }, { label: "同事", value: "同事" }, { label: "朋友", value: "朋友" }] };
it.each([
  ["synchronous cancellation", (): boolean => false],
  ["asynchronous cancellation", (): Promise<boolean> => Promise.resolve(false)],
  ["rejected confirmation", (): Promise<boolean> => Promise.reject(new Error("Confirmation unavailable"))],
] as const)("retains multiple choices and free text after %s and accepts a retry", async (_name, cancel) => {
  const send = vi.fn<() => boolean | Promise<boolean>>().mockImplementationOnce(cancel).mockResolvedValue(true);
  render(<I18nProvider><TypedClarification clarification={multiQuestion} revision={2} isLoading={false} onSend={send} onHide={vi.fn()} /></I18nProvider>);
  fireEvent.click(screen.getByRole("checkbox", { name: "主管" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "自行補充" }));
  fireEvent.change(screen.getByRole("textbox", { name: "自行補充" }), { target: { value: "  客戶  " } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "送出回覆" })); });

  expect(send).toHaveBeenCalledOnce();
  expect(screen.getByRole("checkbox", { name: "主管" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "自行補充" })).toBeChecked();
  expect(screen.getByRole("textbox", { name: "自行補充" })).toHaveValue("  客戶  ");
  await waitFor(() => expect(screen.getByRole("button", { name: "送出回覆" })).toBeEnabled());

  fireEvent.change(screen.getByRole("textbox", { name: "自行補充" }), { target: { value: "外部客戶" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "送出回覆" })); });
  expect(send).toHaveBeenCalledTimes(2);
  expect(send).toHaveBeenLastCalledWith(expect.stringContaining("主管、外部客戶"), expect.objectContaining({
    clarificationAnswer: expect.objectContaining({ status: "provided", value: ["主管", "外部客戶"], validation_token: "signed-token", context_scope: "scenario" }),
  }));
  await waitFor(() => expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled());
});

it("submits a concrete option plus other as a bounded array with the original token and scope", () => {
  const send = vi.fn();
  const draft = vi.fn();
  render(<I18nProvider><TypedClarification clarification={multiQuestion} revision={2} isLoading={false} onSend={send} onHide={vi.fn()} onDraftChange={draft} /></I18nProvider>);
  expect(screen.getAllByRole("checkbox")).toHaveLength(6);
  fireEvent.click(screen.getByRole("checkbox", { name: "主管" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "自行補充" }));
  expect(screen.getByRole("checkbox", { name: "同事" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "自行補充" }), { target: { value: "客戶" } });
  expect(draft).toHaveBeenLastCalledWith({ selectedValues: ["主管"], includeOther: true, other: "客戶", special: null });
  fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
  expect(send).toHaveBeenCalledWith(expect.stringContaining("主管、客戶"), expect.objectContaining({ clarificationAnswer: { question_id: question.question_id, fact_key: "other_role", status: "provided", value: ["主管", "客戶"], selection_mode: "multiple", max_selections: 2, validation_token: "signed-token", context_scope: "scenario" } }));
});

it("keeps unknown and declined exclusive with provided selections after restoring a draft", () => {
  const send = vi.fn();
  render(<I18nProvider><TypedClarification clarification={multiQuestion} revision={2} isLoading={false} onSend={send} onHide={vi.fn()} draft={{ selectedValues: ["主管"], includeOther: true, other: "客戶", special: null }} /></I18nProvider>);
  expect(screen.getByRole("checkbox", { name: "主管" })).toBeChecked();
  expect(screen.getByRole("textbox", { name: "自行補充" })).toHaveValue("客戶");
  fireEvent.click(screen.getByRole("checkbox", { name: "不確定" }));
  expect(screen.getByRole("checkbox", { name: "主管" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "自行補充" })).not.toBeChecked();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "暫不提供" }));
  expect(screen.getByRole("checkbox", { name: "不確定" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
  const answer = send.mock.calls[0][1].clarificationAnswer;
  expect(answer.status).toBe("declined");
  expect(answer).not.toHaveProperty("value");
});

it("does not submit duplicate concrete and free-form values", () => {
  render(<I18nProvider><TypedClarification clarification={multiQuestion} revision={2} isLoading={false} onSend={vi.fn()} onHide={vi.fn()} draft={{ selectedValues: ["主管"], includeOther: true, other: "主管", special: null }} /></I18nProvider>);
  expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
});
it("returns a v4 freeform question's signed revision without a legacy fact key", () => {
  const send = vi.fn();
  const clarification = { question_id: "open.2", context_revision: 2, question: "哪些情況符合？", reason: "釐清情境", selection_mode: "multiple" as const, max_selections: 2, validation_token: "signed-v4", options: [{ label: "校外", value: "校外" }] };
  render(<I18nProvider><TypedClarification clarification={clarification} revision={2} isLoading={false} onSend={send} onHide={vi.fn()} /></I18nProvider>);
  fireEvent.click(screen.getByRole("checkbox", { name: "校外" }));
  fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
  expect(send.mock.calls[0][1].clarificationAnswer).toEqual({ question_id: "open.2", context_revision: 2, status: "provided", value: ["校外"], selection_mode: "multiple", max_selections: 2, validation_token: "signed-v4" });
});
