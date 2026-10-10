import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { batchConfig, fixtureWorkbook } from "../test/scenarioFixture";
import { getRuntimeConfig, runAdminApplicableLawTest } from "../services/api";
import { reviewedRequestBody } from "../services/clientPrivacy";
import { downloadLocalFile, parseScenarioWorkbook } from "../services/scenarioWorkbook";
import AdminBatchPrediction from "./AdminBatchPrediction";

vi.mock("../services/api", async () => ({ ...await vi.importActual("../services/api"), getRuntimeConfig: vi.fn(), runAdminApplicableLawTest: vi.fn() }));
vi.mock("../services/scenarioWorkbook", async () => ({ ...await vi.importActual("../services/scenarioWorkbook"), downloadLocalFile: vi.fn() }));

async function upload() {
  const bytes = fixtureWorkbook();
  const file = new File([new Uint8Array(bytes)], "synthetic.xlsx");
  Object.defineProperty(file, "arrayBuffer", { value: async () => bytes.buffer });
  fireEvent.change(screen.getByLabelText("Excel 檔案"), { target: { files: [file] } });
  await screen.findByRole("option", { name: "C · 情境" });
}
beforeEach(() => {
  vi.mocked(getRuntimeConfig).mockReset().mockResolvedValue(batchConfig);
  vi.mocked(runAdminApplicableLawTest).mockReset().mockResolvedValue({ output_contract: "applicable_laws_v1", prediction: { predicted_laws: ["性工法"], decision_status: "predicted", decision_reason: null }, diagnostics: {} });
  vi.mocked(downloadLocalFile).mockClear();
});
describe("admin batch prediction", () => {
  it("selects sheet/column, previews every masked scenario and does not send before the start action", async () => {
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload();
    expect(screen.getByLabelText("情境欄位")).toHaveValue("2");
    fireEvent.click(screen.getByRole("button", { name: "準備預測" }));
    await screen.findByText("合成聯絡人 [電子郵件]");
    expect(runAdminApplicableLawTest).not.toHaveBeenCalled();
    expect(screen.queryByText("gold-never-send")).not.toBeInTheDocument();
    expect(screen.getByLabelText("工作表")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "下載送出預覽" }));
    expect(downloadLocalFile).toHaveBeenCalledWith(expect.not.stringContaining("test@example.com"), "prediction-preview.jsonl", expect.any(String));
    fireEvent.click(screen.getByRole("button", { name: "下載結果 Excel" }));
    const [data] = vi.mocked(downloadLocalFile).mock.calls.at(-1)!;
    const copy = parseScenarioWorkbook(data as Uint8Array, "copy.xlsx");
    expect(copy.sheets[0].rows.get(2)?.get(4)).toBe("待預測");
  });
  it("can select a different sheet and invokes the authenticated API only for its scenario", async () => {
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload();
    fireEvent.change(screen.getByLabelText("工作表"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "準備預測" }));
    fireEvent.click(await screen.findByRole("button", { name: "確認送出並開始預測" }));
    await screen.findByText("完成 1 / 1 · 失敗 0 · 待預測 0");
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1);
    const args = vi.mocked(runAdminApplicableLawTest).mock.calls[0];
    expect(args[0]).toBe("test");
    expect(args[1]).toEqual({ message: "另一工作表情境", history: [], use_rag: true, contract_version: 4 });
    expect(reviewedRequestBody(args[1])).toContain("另一工作表情境");
    expect(args[4]).toBe("a".repeat(64));
  });
  it("gracefully pauses after the active request and exports partial results", async () => {
    let resolve!: (result: Awaited<ReturnType<typeof runAdminApplicableLawTest>>) => void;
    vi.mocked(runAdminApplicableLawTest).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload();
    fireEvent.click(screen.getByRole("button", { name: "準備預測" }));
    fireEvent.click(await screen.findByRole("button", { name: "確認送出並開始預測" }));
    fireEvent.click(screen.getByRole("button", { name: "完成本筆後暫停" }));
    expect(screen.getByRole("button", { name: "等待本筆完成後暫停…" })).toBeDisabled();
    resolve({ output_contract: "applicable_laws_v1", prediction: { predicted_laws: [], decision_status: "no_target_law", decision_reason: "情境僅描述一般財物糾紛，沒有性或性別相關行為。" }, diagnostics: {} });
    await screen.findByRole("button", { name: "繼續未完成預測" });
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "下載結果 Excel" }));
    const [data] = vi.mocked(downloadLocalFile).mock.calls[0];
    const copy = parseScenarioWorkbook(data as Uint8Array, "copy.xlsx");
    expect(copy.sheets[0].rows.get(2)?.get(4)).toBe("不適用三法");
    expect(screen.getByText("無法預測原因：情境僅描述一般財物糾紛，沒有性或性別相關行為。")).toBeVisible();
    expect(copy.sheets[0].rows.get(1)?.get(8)).toBe("無法預測原因");
    expect(copy.sheets[0].rows.get(2)?.get(8)).toBe("情境僅描述一般財物糾紛，沒有性或性別相關行為。");
    expect(copy.sheets[0].rows.get(5)?.get(4)).toBe("待預測");
  });
  it("aborts work on unmount", async () => {
    vi.mocked(runAdminApplicableLawTest).mockImplementationOnce((_token, _request, _config, signal) => new Promise((_resolve, reject) => signal?.addEventListener("abort", () => reject(new Error("aborted")))));
    const view = render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload();
    fireEvent.click(screen.getByRole("button", { name: "準備預測" }));
    fireEvent.click(await screen.findByRole("button", { name: "確認送出並開始預測" }));
    await waitFor(() => expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(runAdminApplicableLawTest).mock.calls[0][3];
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });
});

it("scores against a selected reference locally and exports the same numerator and denominator", async () => {
  render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
  const bytes = fixtureWorkbook('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>適用法規</t></is></c><c r="B1" t="inlineStr"><is><t>情境</t></is></c></row><row r="7"><c r="A7" t="inlineStr"><is><t>性平法、性工法</t></is></c><c r="B7" t="inlineStr"><is><t>合成測試</t></is></c></row></sheetData></worksheet>');
  const file = new File([new Uint8Array(bytes)], "scored.xlsx");
  Object.defineProperty(file, "arrayBuffer", { value: async () => bytes.buffer });
  fireEvent.change(screen.getByLabelText("Excel 檔案"), { target: { files: [file] } });
  await screen.findByRole("option", { name: "B · 情境" });
  expect(screen.getByLabelText("參考答案欄位")).toHaveValue("0");
  fireEvent.click(screen.getByRole("button", { name: "準備預測" }));
  fireEvent.click(await screen.findByRole("button", { name: "確認送出並開始預測" }));
  await screen.findByText("完成 1 / 1 · 失敗 0 · 待預測 0");
  expect(screen.getByText("命中：性工法")).toBeVisible();
  const args = vi.mocked(runAdminApplicableLawTest).mock.calls[0];
  expect(JSON.stringify(args[1])).not.toContain("性平法");
  fireEvent.click(screen.getByRole("button", { name: "下載結果 Excel" }));
  const [output] = vi.mocked(downloadLocalFile).mock.calls.at(-1)!;
  const exported = parseScenarioWorkbook(output as Uint8Array, "result.xlsx");
  expect(exported.sheets[0].rows.get(7)?.get(5)).toBe("正確");
  const summary = exported.sheets.find(sheet => sheet.name === "預測統計")!;
  const total = [...summary.rows.values()].find(cells => cells.get(0) === "總正確率")!;
  expect([total.get(1), total.get(2), total.get(3)]).toEqual(["100.00%", "1", "1"]);
});


it("shows the specific missing facts and includes them in the Excel copy", async () => {
  const reason = "未說明雙方是否為同校師生，無法判斷校園處理程序是否適用。";
  vi.mocked(runAdminApplicableLawTest).mockResolvedValue({ output_contract: "applicable_laws_v1", prediction: { predicted_laws: [], decision_status: "insufficient_facts", decision_reason: reason }, diagnostics: {} });
  render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
  await upload();
  fireEvent.change(screen.getByLabelText("工作表"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "準備預測" }));
  fireEvent.click(await screen.findByRole("button", { name: "確認送出並開始預測" }));
  expect(await screen.findByText(`無法預測原因：${reason}`)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "下載結果 Excel" }));
  const [bytes] = vi.mocked(downloadLocalFile).mock.calls[0];
  const sheet = parseScenarioWorkbook(bytes as Uint8Array, "result.xlsx").sheets[1];
  const reasonColumn = [...sheet.rows.get(1)!.entries()].find(([, label]) => label === "無法預測原因")![0];
  expect(sheet.rows.get(2)?.get(reasonColumn)).toBe(reason);
});
