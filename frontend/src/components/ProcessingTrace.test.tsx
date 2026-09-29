import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../i18n";
import type { ProcessingTrace as ProcessingTraceData } from "../services/processingTrace";
import ProcessingTrace from "./ProcessingTrace";

const running: ProcessingTraceData = {
  outcome: "running",
  duration_ms: 2450,
  steps: [
    { phase: "connecting", elapsed_ms: 0, attempt: 0 },
    { phase: "waiting_model", elapsed_ms: 800, attempt: 0 },
    { phase: "retrieving", elapsed_ms: 1300, attempt: 0 },
    { phase: "waiting_model", elapsed_ms: 2200, attempt: 0 },
  ],
};

describe("ProcessingTrace", () => {
  beforeEach(() => localStorage.clear());

  it("starts expanded and shows only observed phases in order, including repeated phases", () => {
    render(<ProcessingTrace trace={running} />, { wrapper: I18nProvider });
    expect(screen.getByRole("button", { name: "處理過程" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("status")).toHaveTextContent("處理中");
    expect(screen.getByText("顯示系統處理步驟，不代表完整內部推理或法律正確性驗證。")).toBeVisible();
    const steps = screen.getAllByRole("listitem");
    expect(steps.map(step => step.textContent)).toEqual([
      "正在連線0.0 秒", "正在等待模型回應0.8 秒", "正在檢索資料庫1.3 秒", "正在等待模型回應2.2 秒",
    ]);
    expect(steps[3]).toHaveAttribute("aria-current", "step");
    expect(steps.slice(0, 3).every(step => !step.hasAttribute("aria-current"))).toBe(true);
    expect(screen.getByText("2.5 秒")).toBeVisible();
    expect(screen.queryByText("檢查回覆格式與引用")).not.toBeInTheDocument();
  });

  it("preserves manual collapse during new events and automatically collapses on completion", () => {
    const { rerender } = render(<ProcessingTrace trace={running} />, { wrapper: I18nProvider });
    const toggle = screen.getByRole("button", { name: "處理過程" });
    fireEvent.click(toggle);
    const updated: ProcessingTraceData = {
      ...running, duration_ms: 3000,
      steps: [...running.steps, { phase: "validating", elapsed_ms: 2800, attempt: 0 }],
    };
    rerender(<ProcessingTrace trace={updated} />);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText("檢查回覆格式與引用")).toBeVisible();
    rerender(<ProcessingTrace trace={{ ...updated, outcome: "complete" }} />);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("status")).toHaveTextContent("回覆完成");
    fireEvent.click(toggle);
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    rerender(<ProcessingTrace trace={{ ...updated, outcome: "complete", duration_ms: 3200 }} />);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("offers a focusable native button and linked panel for keyboard activation", () => {
    render(<ProcessingTrace trace={{ ...running, outcome: "complete" }} />, { wrapper: I18nProvider });
    const toggle = screen.getByRole("button", { name: "處理過程" });
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle).toHaveAttribute("type", "button");
    toggle.focus();
    expect(toggle).toHaveFocus();
    const panel = document.getElementById(toggle.getAttribute("aria-controls")!);
    expect(panel).toHaveAttribute("hidden");
    // Native buttons map Enter and Space to click; no custom key handler is needed.
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveAttribute("hidden");
    expect(toggle).toHaveFocus();
  });

  it.each([
    ["cancelled", "已停止"], ["error", "處理中斷"],
  ] as const)("preserves observed steps with a truthful %s outcome", (outcome, label) => {
    const { rerender } = render(<ProcessingTrace trace={running} />, { wrapper: I18nProvider });
    rerender(<ProcessingTrace trace={{ ...running, outcome }} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
    expect(screen.getAllByRole("listitem")).toHaveLength(running.steps.length);
    expect(screen.getAllByRole("listitem").every(step => !step.hasAttribute("aria-current"))).toBe(true);
    expect(screen.queryByText("回覆完成")).not.toBeInTheDocument();
    expect(screen.queryByText(/check|done/)).not.toBeInTheDocument();
  });

  it("shows a zero-based retry attempt of 1 as the second request in Chinese", () => {
    render(<ProcessingTrace trace={{
      ...running, steps: [...running.steps, { phase: "retrying", elapsed_ms: 2400, attempt: 1 }],
    }} />, { wrapper: I18nProvider });
    expect(screen.getByText("第 2 次請求")).toBeVisible();
    expect(screen.queryByText("第 1 次請求")).not.toBeInTheDocument();
  });

  it("renders retry attempts and English labels without exposing arbitrary metadata", () => {
    localStorage.setItem("harass_bot_locale", "en");
    const trace = {
      ...running,
      steps: [
        { phase: "retrying", elapsed_ms: 500, attempt: 1, query: "secret search" },
        { phase: "anonymizing", elapsed_ms: 600, attempt: 1 },
        { phase: "generating", elapsed_ms: 700, attempt: 1 },
        { phase: "validating", elapsed_ms: 800, attempt: 1 },
        { phase: "guidance", elapsed_ms: 900, attempt: 1 },
      ],
      debug_tool_calls: [{ arguments: { question: "secret question" } }],
      facts: { subject_role: "secret fact" },
    } as ProcessingTraceData;
    render(<ProcessingTrace trace={trace} />, { wrapper: I18nProvider });
    expect(screen.getByRole("button", { name: "Processing steps" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Observed processing steps" });
    expect(within(steps).getAllByText("Request attempt 2")).toHaveLength(5);
    expect(screen.getByText("Applying text masking")).toBeVisible();
    expect(screen.getByText("Receiving response content")).toBeVisible();
    expect(screen.getByText("Checking response format and citations")).toBeVisible();
    expect(screen.getByText("Preparing follow-up guidance")).toBeVisible();
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument();
    expect(screen.getByText("Shows system processing steps, not complete internal reasoning or verification of legal accuracy.")).toBeVisible();
  });

  it("does not invent steps when no events were observed", () => {
    render(<ProcessingTrace trace={{ outcome: "error", duration_ms: 0, steps: [] }} />, { wrapper: I18nProvider });
    expect(screen.getByRole("status")).toHaveTextContent("處理中斷");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});
