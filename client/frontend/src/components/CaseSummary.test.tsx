import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import CaseSummary from "./CaseSummary";
import type { CaseContext } from "../services/caseFacts";
beforeEach(() => localStorage.clear());
const context: CaseContext = { schema_version: 3, revision: 3, facts: {}, summary: "本人在公車上被碰觸，對方身分不明。", summary_origin: "model" };

it("shows only known summary text and stops generation only when editing begins", () => {
  const save = vi.fn(), stop = vi.fn();
  render(<I18nProvider><CaseSummary context={context} onSave={save} onClose={vi.fn()} onStartEdit={stop} /></I18nProvider>);
  expect(screen.getByRole("dialog", { name: "目前摘要" })).toBeVisible();
  expect(screen.getByText(context.summary)).toBeVisible();
  expect(screen.queryByText("實習身分")).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(stop).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "修改摘要" }));
  expect(stop).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByLabelText("已知情境"), { target: { value: "第三人稱例題：甲在公車上遇到陌生人。" } });
  fireEvent.click(screen.getByRole("button", { name: "儲存摘要" }));
  expect(save).toHaveBeenCalledWith({ ...context, summary: "第三人稱例題：甲在公車上遇到陌生人。", summary_origin: "user" }, false);
});

it("keeps an edit on its captured revision when a late model update arrives", () => {
  const save = vi.fn(), close = vi.fn();
  const view = (value: CaseContext) => <I18nProvider><CaseSummary canRegenerate context={value} onSave={save} onClose={close} /></I18nProvider>;
  const { rerender } = render(view(context));
  fireEvent.click(screen.getByRole("button", { name: "修改摘要" }));
  fireEvent.change(screen.getByLabelText("已知情境"), { target: { value: "已刪去不正確資訊" } });
  rerender(view({ ...context, revision: 4, summary: "晚到的模型摘要" }));
  expect(screen.getByLabelText("已知情境")).toHaveValue("已刪去不正確資訊");
  fireEvent.click(screen.getByRole("button", { name: "儲存並重新回答" }));
  expect(save).toHaveBeenCalledWith({ ...context, summary: "已刪去不正確資訊", summary_origin: "user" }, true);
  expect(close).toHaveBeenCalledOnce();
});

it("migrates only existing legacy facts into text and cancels without saving", () => {
  const save = vi.fn();
  render(<I18nProvider><CaseSummary context={{ schema_version: 2, revision: 2, facts: { city: { status: "declined" }, relationship: { status: "unknown" }, other_role: { status: "provided", value: ["主管", "同事"] } } }} onSave={save} onClose={vi.fn()} /></I18nProvider>);
  fireEvent.click(screen.getByRole("button", { name: "修改摘要" }));
  const text = (screen.getByLabelText("已知情境") as HTMLTextAreaElement).value;
  expect(text).toContain("主管"); expect(text).toContain("同事");
  expect(text).toMatch(/不確定|不知道|未知/); expect(text).toMatch(/不想|拒答|不願|暫不提供/);
  fireEvent.change(screen.getByLabelText("已知情境"), { target: { value: "未儲存" } });
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(save).not.toHaveBeenCalled();
});

it("lets a user shorten an oversized legacy summary while preserving the full initial text", () => {
  const save = vi.fn();
  const large = Array.from({ length: 4 }, (_, index) => `${index}${"甲".repeat(299)}`);
  render(<I18nProvider><CaseSummary context={{ schema_version: 2, revision: 2, facts: { subject_role: { status: "provided", value: large }, other_role: { status: "provided", value: large }, behavior: { status: "provided", value: large }, desired_help: { status: "provided", value: large } } }} onSave={save} onClose={vi.fn()} /></I18nProvider>);
  expect(screen.getByRole("alert")).toHaveTextContent("4000");
  fireEvent.click(screen.getByRole("button", { name: "修改摘要" }));
  expect((screen.getByLabelText("已知情境") as HTMLTextAreaElement).value.length).toBeGreaterThan(4000);
  expect(screen.getByRole("button", { name: "儲存摘要" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("已知情境"), { target: { value: "使用者確認的必要摘要" } });
  fireEvent.click(screen.getByRole("button", { name: "儲存摘要" }));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ schema_version: 3, summary: "使用者確認的必要摘要", summary_origin: "user" }), false);
});
