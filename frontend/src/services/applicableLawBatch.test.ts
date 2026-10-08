import { beforeEach, describe, expect, it, vi } from "vitest";
import { batchConfig } from "../test/scenarioFixture";
import { ApiError, runAdminApplicableLawTest } from "./api";
import { approveLawBatch, prepareLawBatch, runLawBatch, type BatchRow } from "./applicableLawBatch";
import { reviewedRequestBody } from "./clientPrivacy";

vi.mock("./api", async () => ({ ...await vi.importActual("./api"), runAdminApplicableLawTest: vi.fn() }));
const scenarios = [{ row: 2, text: "聯絡 test@example.com，甲公司" }, { row: 5, text: "另一合成情境" }];
const prediction = { output_contract: "applicable_laws_v1" as const, prediction: { predicted_laws: ["性工法" as const], decision_status: "predicted" as const, decision_reason: null }, diagnostics: {} };
function setup() {
  const batch = prepareLawBatch(scenarios, batchConfig, ["甲公司"]);
  return { rows: approveLawBatch(batch.rows), snapshot: batch.snapshot, token: "test", signal: new AbortController().signal, shouldPause: () => false, onUpdate: vi.fn(), delayMs: 0 };
}
beforeEach(() => vi.mocked(runAdminApplicableLawTest).mockReset().mockResolvedValue(prediction));
describe("batch prediction transport", () => {
  it("prepares all masked previews with no network and requires explicit approval", () => {
    const batch = prepareLawBatch(scenarios, batchConfig, ["甲公司"]);
    expect(batch.rows[0].draft.request).toEqual({ message: "聯絡 [電子郵件]，[已隱藏資訊1]", history: [], use_rag: true, contract_version: 4 });
    expect(() => reviewedRequestBody(batch.rows[0].draft.request)).toThrow();
    expect(approveLawBatch(batch.rows)[0].request).toBeDefined();
    expect(runAdminApplicableLawTest).not.toHaveBeenCalled();
  });
  it("freezes complete settings and sends the expected contract on every row", async () => {
    const options = setup();
    await runLawBatch(options);
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(2);
    expect(runAdminApplicableLawTest).toHaveBeenLastCalledWith("test", options.rows[1].request, options.snapshot.overrides, options.signal, "a".repeat(64));
    expect(options.snapshot.overrides).not.toHaveProperty("source");
  });
  it("pauses after the in-flight request and resumes without repeating completed rows", async () => {
    const options = setup(); let shouldPause = false;
    options.shouldPause = () => shouldPause;
    vi.mocked(runAdminApplicableLawTest).mockImplementationOnce(async () => { shouldPause = true; return prediction; });
    const paused = await runLawBatch(options);
    expect(paused.rows.map(row => row.status)).toEqual(["predicted", "pending"]);
    shouldPause = false;
    await runLawBatch({ ...options, rows: paused.rows });
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(2);
  });
  it("keeps errors separate, never auto-retries and retries only failed rows explicitly", async () => {
    const options = setup();
    vi.mocked(runAdminApplicableLawTest).mockRejectedValueOnce(new ApiError(500, "private provider body"));
    const result = await runLawBatch(options);
    expect(result.rows.map(row => row.status)).toEqual(["error", "predicted"]);
    expect(JSON.stringify(result.rows[0].error)).not.toContain("private");
    await runLawBatch({ ...options, rows: result.rows });
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(2);
    const retried = await runLawBatch({ ...options, rows: [...result.rows, { ...options.rows[1], row: 9 }], retryFailed: true });
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(3);
    expect(retried.rows.map(row => row.status)).toEqual(["predicted", "predicted", "pending"]);
  });
  it.each([401, 403, 409, 429])("stops remaining calls on HTTP %s", async status => {
    vi.mocked(runAdminApplicableLawTest).mockRejectedValueOnce(new ApiError(status, "failure"));
    const result = await runLawBatch(setup());
    expect(result.stoppedReason).toBeTruthy();
    expect(result.rows.map(row => row.status)).toEqual(["error", "pending"]);
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1);
  });
  it("counts valid empty predictions as complete and skips them on resume", async () => {
    vi.mocked(runAdminApplicableLawTest).mockResolvedValue({ ...prediction, prediction: { predicted_laws: [], decision_status: "insufficient_facts", decision_reason: "未提供會影響適用方向的當事人身分與關係。" } });
    const options = setup(); const result = await runLawBatch(options);
    await runLawBatch({ ...options, rows: result.rows });
    expect(result.rows.every(row => row.status === "insufficient_facts")).toBe(true);
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(2);
  });
  it("sends nothing if cancelled, paused, or unapproved", async () => {
    const options = setup(); const controller = new AbortController(); controller.abort();
    await runLawBatch({ ...options, signal: controller.signal });
    await runLawBatch({ ...options, shouldPause: () => true });
    await expect(runLawBatch({ ...options, rows: prepareLawBatch(scenarios, batchConfig, []).rows })).rejects.toThrow("確認");
    expect(runAdminApplicableLawTest).not.toHaveBeenCalled();
  });
  it("marks an interrupted in-flight request as failed so resume cannot silently charge twice", async () => {
    const controller = new AbortController();
    vi.mocked(runAdminApplicableLawTest).mockImplementationOnce(async () => { controller.abort(); return prediction; });
    const result = await runLawBatch({ ...setup(), signal: controller.signal });
    expect(result.rows[0]).toMatchObject({ status: "error", error: "請求已中斷，可能已呼叫模型。" });
    expect(result.rows[1].status).toBe("pending");
  });
  it("rejects missing settings and overlong scenarios without truncating", () => {
    expect(() => prepareLawBatch(scenarios, { ...batchConfig, applicable_laws_contract_sha256: undefined }, [])).toThrow("後端");
    expect(() => prepareLawBatch([{ row: 12, text: "長".repeat(2001) }], batchConfig, [])).toThrow("12");
    expect(() => prepareLawBatch([], batchConfig, [])).toThrow("沒有");
  });
  it("publishes only immutable row snapshots for each state change", async () => {
    const snapshots: BatchRow[][] = [];
    await runLawBatch({ ...setup(), onUpdate: rows => snapshots.push(rows) });
    expect(snapshots[0][0].status).toBe("running");
    expect(snapshots.at(-1)![0].status).toBe("predicted");
  });
});
