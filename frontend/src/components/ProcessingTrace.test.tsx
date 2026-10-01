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
      "正在連線", "正在等待模型回應", "正在檢索資料庫", "正在等待模型回應",
    ]);
    expect(steps[3]).toHaveAttribute("aria-current", "step");
    expect(steps.slice(0, 3).every(step => !step.hasAttribute("aria-current"))).toBe(true);
    expect(screen.queryByText(/\d+\.\d+ 秒/)).not.toBeInTheDocument();
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
  it("shows public analysis summaries, facts, sources and limits instead of timing rows", () => {
    render(<ProcessingTrace trace={running} analysis={[{ stage: "sources", summary: "找到兩個可供參考的條文", facts: ["與工作場合有關"], source_labels: ["測試法第1條"], limitations: ["尚未確認事發時間"] }]} />, { wrapper: I18nProvider });
    expect(screen.getByRole("button", { name: "分析摘要與依據" })).toBeVisible();
    expect(screen.getByText("找到兩個可供參考的條文")).toBeVisible();
    expect(screen.getByText("與工作場合有關")).toBeVisible();
    expect(screen.getByText("測試法第1條")).toBeVisible();
    expect(screen.getByText("尚未確認事發時間")).toBeVisible();
    expect(screen.queryByText("正在連線")).not.toBeInTheDocument();
    expect(screen.queryByText(/\d+\.\d+ 秒/)).not.toBeInTheDocument();
  });
  it.each([
    ["zh-TW", "回答重點：", "已知條件："],
    ["en", "Answer highlights：", "Confirmed details："],
  ])("labels answer facts separately from known conditions in %s", (locale, highlights, knownFacts) => {
    localStorage.setItem("harass_bot_locale", locale);
    render(<ProcessingTrace trace={running} analysis={[
      { stage: "understanding", summary: "條件整理", facts: ["合成情境"], source_labels: [], limitations: [] },
      { stage: "answer", summary: "答案整理", facts: ["合成法律分析"], source_labels: [], limitations: [] },
    ]} />, { wrapper: I18nProvider });
    const steps = screen.getAllByRole("listitem");
    expect(steps[0]).toHaveTextContent(knownFacts);
    expect(steps[0]).not.toHaveTextContent(highlights);
    expect(steps[1]).toHaveTextContent(highlights);
    expect(steps[1]).not.toHaveTextContent(knownFacts);
  });
});

it("separates transient provider explanations from observed analysis and collapses on completion", () => {
  localStorage.clear();
  const trace = { steps: [], duration_ms: 0, outcome: "running" as const };
  const reasoning = [{ text: "供應商公開回傳的簡短說明。", kind: "summary" as const, stage: "understanding" as const }];
  const view = (complete: boolean) => <I18nProvider><ProcessingTrace trace={{ ...trace, outcome: complete ? "complete" : "running" }} reasoning={reasoning} /></I18nProvider>;
  const { rerender } = render(view(false));
  expect(screen.getByRole("region", { name: "模型提供的推理說明" })).toBeVisible();
  expect(screen.getByText(reasoning[0].text)).toBeVisible();
  expect(screen.getByText(/並非已驗證答案/)).toBeVisible();
  rerender(view(true));
  expect(screen.getByText(reasoning[0].text)).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "處理過程" }));
  expect(screen.getByText(reasoning[0].text)).toBeVisible();
});
