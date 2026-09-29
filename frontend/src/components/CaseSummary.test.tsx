import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import CaseSummary from "./CaseSummary";
beforeEach(() => localStorage.clear());
it("lets the user explicitly keep unknown facts and choose save without another model call", () => {
  const save = vi.fn();
  render(<I18nProvider><CaseSummary context={{ schema_version: 1, revision: 3, facts: { city: { status: "declined" } } }} pending={[]} onSave={save} /></I18nProvider>);
  fireEvent.click(screen.getByText("目前了解的情況"));
  fireEvent.click(screen.getByRole("button", { name: "修改摘要" }));
  fireEvent.change(screen.getByLabelText("雙方關係"), { target: { value: "unknown" } });
  fireEvent.click(screen.getByRole("button", { name: "儲存摘要" }));
  expect(save).toHaveBeenCalledWith({ schema_version: 1, revision: 3, facts: { city: { status: "declined" }, relationship: { status: "unknown" } } }, false);
});
it("requires review and explicit save to accept a conflicting proposal", () => {
  const save = vi.fn();
  render(<I18nProvider><CaseSummary canRegenerate context={{ schema_version: 1, revision: 1, facts: { other_role: { status: "provided", value: "主管" } } }} pending={[{ fact_key: "other_role", status: "provided", value: "同事", evidence: "同事", kind: "confirmation" }]} onSave={save} /></I18nProvider>);
  fireEvent.click(screen.getByText(/目前了解的情況/));
  fireEvent.click(screen.getByRole("button", { name: "採用此內容" }));
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByLabelText("對方的角色 內容")).toHaveValue("同事");
  fireEvent.click(screen.getByRole("button", { name: "儲存並重新回答" }));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ facts: { other_role: { status: "provided", value: "同事" } } }), true);
});
