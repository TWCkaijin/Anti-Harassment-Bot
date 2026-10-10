import { webcrypto } from "node:crypto";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { behaviorConfig, behaviorFixtureWorkbook, behaviorResult, behaviorRun } from "../test/behaviorFixture";
import { getRuntimeConfig, runAdminChatTest } from "../services/api";
import { exportBehaviorJson, refreshBehaviorEvidence } from "../services/behaviorRecords";
import { downloadLocalFile, parseScenarioWorkbook } from "../services/scenarioWorkbook";
import AdminBehaviorTests from "./AdminBehaviorTests";

vi.mock("../services/api", async () => ({ ...await vi.importActual("../services/api"), getRuntimeConfig: vi.fn(), runAdminChatTest: vi.fn() }));
vi.mock("../services/scenarioWorkbook", async () => ({ ...await vi.importActual("../services/scenarioWorkbook"), downloadLocalFile: vi.fn() }));
vi.mock("../services/behaviorRunner", async () => {
  const actual = await vi.importActual<typeof import("../services/behaviorRunner")>("../services/behaviorRunner");
  return { ...actual, runBehaviorTests: (options: Parameters<typeof actual.runBehaviorTests>[0]) => actual.runBehaviorTests({ ...options, delayMs: 0 }) };
});
beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  vi.mocked(getRuntimeConfig).mockReset().mockResolvedValue(behaviorConfig);
  vi.mocked(runAdminChatTest).mockReset().mockImplementation(async (_token, request) => behaviorResult(request));
  vi.mocked(downloadLocalFile).mockClear();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function loadJSON(run = behaviorRun()) {
  const data = exportBehaviorJson(await refreshBehaviorEvidence(run));
  const file = new File([data], "behavior.json", { type: "application/json" });
  Object.defineProperty(file, "text", { value: async () => data });
  fireEvent.change(screen.getByLabelText("匯入執行紀錄 JSON"), { target: { files: [file] } });
  await screen.findByText(/已離線載入紀錄/);
}
function confirmSnapshot() { fireEvent.click(screen.getByRole("checkbox", { name: /已確認本次設定與腳本/ })); }

describe("behavior suite UI", () => {
  it("requires confirmation of the five scripts, prepares without model calls and separates rubric from outgoing text", async () => {
    render(<AdminBehaviorTests csrfToken="token" onHasDataChange={vi.fn()} />);
    expect(screen.getByText(/共 5 個案例 · 7 個預定對話請求/)).toBeVisible();
    expect(screen.getByRole("button", { name: "準備對話測試" })).toBeDisabled();
    for (const id of ["B01", "H01", "C01", "P01", "S01"]) fireEvent.click(screen.getByRole("checkbox", { name: `確認 ${id} 腳本` }));
    fireEvent.click(screen.getByRole("button", { name: "準備對話測試" }));
    await screen.findByText(/設定已固定/);
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "確認送出並開始對話測試" })).toBeDisabled();
  });
  it("imports a synthetic workbook as drafts without promoting the shorthand or historical results", async () => {
    render(<AdminBehaviorTests csrfToken="token" onHasDataChange={vi.fn()} />);
    const bytes = behaviorFixtureWorkbook();
    const file = new File([Uint8Array.from(bytes).buffer], "synthetic-behavior.xlsx");
    Object.defineProperty(file, "arrayBuffer", { value: async () => Uint8Array.from(bytes).buffer });
    fireEvent.change(screen.getByLabelText("匯入測試表 Excel"), { target: { files: [file] } });
    await screen.findByText(/已匯入草稿/);
    expect(screen.getByLabelText("H01 第 1 輪")).toHaveValue("");
    expect(screen.getByLabelText("C01 第 1 輪")).toHaveValue("");
    expect(screen.getByLabelText("P01 第 1 輪")).toHaveValue("");
    expect(getRuntimeConfig).not.toHaveBeenCalled(); expect(runAdminChatTest).not.toHaveBeenCalled();
  });
  it("replays JSON offline, executes on explicit start, and exports a hash-bound manual review", async () => {
    render(<AdminBehaviorTests csrfToken="token" onHasDataChange={vi.fn()} />);
    await loadJSON(); expect(getRuntimeConfig).not.toHaveBeenCalled();
    confirmSnapshot(); fireEvent.click(screen.getByRole("button", { name: "確認送出並開始對話測試" }));
    await screen.findByText("已完成所有對話請求，請逐項人工覆核。");
    expect(runAdminChatTest).toHaveBeenCalledTimes(1);
    const stats = within(screen.getByRole("region", { name: "對話測試統計" }));
    expect(stats.getByText("—")).toBeVisible();
    fireEvent.change(screen.getByLabelText("人工結論"), { target: { value: "fail" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /重大失敗/ }));
    fireEvent.change(screen.getByLabelText("覆核理由"), { target: { value: "合成回覆未符合此案的預期行為。" } });
    fireEvent.change(screen.getByLabelText("覆核者"), { target: { value: "test-reviewer" } });
    fireEvent.click(screen.getByRole("button", { name: "保存人工覆核" }));
    await screen.findByText(/已保存：失敗/);
    fireEvent.click(screen.getByRole("button", { name: "下載完整 JSON" }));
    const data = JSON.parse(vi.mocked(downloadLocalFile).mock.calls[0][0] as string);
    expect(data.records[0].review).toMatchObject({ verdict: "fail", critical: true, reviewer: "test-reviewer", evidenceSha256: data.records[0].evidenceSha256 });
    expect(JSON.stringify(data)).not.toContain('"token"');
    fireEvent.click(screen.getByRole("button", { name: "下載測試報告 Excel" }));
    const workbook = parseScenarioWorkbook(vi.mocked(downloadLocalFile).mock.calls[1][0] as Uint8Array, "report.xlsx");
    expect(workbook.sheets[0].rows.get(2)?.get(6)).toBe("失敗");
    expect(workbook.sheets[0].rows.get(2)?.get(10)).toBe("是");
    data.records[0].turns[0].result.response.reply = "變更過的回覆";
    await loadJSON(data);
    expect(await screen.findByText(/原覆核失效/)).toBeVisible();
    expect(runAdminChatTest).toHaveBeenCalledTimes(1);
  });
  it("automatically masks dynamic history and summaries on every turn despite legacy review settings", async () => {
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => behaviorResult(request, "前輪回答 test@example.test", { summary_update: { base_revision: request.case_context!.revision, summary: "同事傳來訊息 test@example.test", evidence: [request.message] } }));
    const run = behaviorRun(["C01"]); run.overrides.enable_client_privacy_review = true;
    render(<AdminBehaviorTests csrfToken="token" onHasDataChange={vi.fn()} />);
    await loadJSON(run); confirmSnapshot();
    fireEvent.click(screen.getByRole("button", { name: "確認送出並開始對話測試" }));
    await screen.findByText("已完成所有對話請求，請逐項人工覆核。");
    expect(runAdminChatTest).toHaveBeenCalledTimes(3);
    const request = vi.mocked(runAdminChatTest).mock.calls[1][1];
    expect(request.history[1].content).toBe("前輪回答 [電子郵件]");
    expect(request.case_context).toMatchObject({ summary: "同事傳來訊息 [電子郵件]" });
    expect(screen.queryByRole("dialog", { name: "確認即將送出的內容" })).not.toBeInTheDocument();
  });
  it("blocks an imported stale contract before calling the model", async () => {
    vi.mocked(getRuntimeConfig).mockResolvedValue({ ...behaviorConfig, chat_test_contract_sha256: "c".repeat(64) });
    render(<AdminBehaviorTests csrfToken="token" onHasDataChange={vi.fn()} />);
    await loadJSON(); confirmSnapshot();
    fireEvent.click(screen.getByRole("button", { name: "確認送出並開始對話測試" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("契約已變更");
    expect(runAdminChatTest).not.toHaveBeenCalled();
  });
});
