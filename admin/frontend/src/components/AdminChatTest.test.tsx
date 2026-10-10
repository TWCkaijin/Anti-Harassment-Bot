import { RETRIEVAL_DEFAULTS } from "../services/retrievalSettings";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AdminChatTest from "./AdminChatTest";
import { runAdminApplicableLawTest, runAdminChatTest, type AdminApplicableLawTestResult, type AdminChatTestResult, type ApplicableLawName, type ChatRequest, type RuntimeConfig } from "../services/api";
import { DEFAULT_PIPELINE } from "../services/pipeline";
import { preparePrivateRequest } from "../services/privateRequest";
vi.mock("../services/api", async () => ({ ...await vi.importActual<typeof import("../services/api")>("../services/api"), runAdminChatTest: vi.fn(), runAdminApplicableLawTest: vi.fn() }));
vi.mock("../services/privateRequest", () => ({ preparePrivateRequest: vi.fn() }));
const config: RuntimeConfig = { openrouter_model: "synthetic/model", rag_retrieval_top_k: 3, rag_distance_threshold: 0.7, enable_anonymization: true, temperature: 0.2, top_p: 0.9, max_tokens: 1200, reasoning_effort: "low", agent_prompt_sections: { role: "synthetic prompt" }, rag_collections: { law: "laws", judgment: "judgments", remedy: "remedies" }, maintenance_message: "", enable_image_upload: true, development_mode: false, source: "firestore", pipeline: DEFAULT_PIPELINE };
const testResult = (id: number, masking = true): AdminChatTestResult => ({ response: { reply: `合成回答 ${id}`, session_id: "test", anonymized: masking, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [] }, diagnostics: { effective_config: { ...config, enable_anonymization: masking, updated_by: "admin-metadata" }, pii: { message: { changed_values: masking ? 1 : 0 } }, input_stages: { summary: { before: "原始摘要", after: masking ? "遮罩摘要" : "原始摘要", changed: masking } }, history: { removed_messages: 0 }, retrieval: { sources: [] }, rewrites: [] } });
const lawResult = (laws: ApplicableLawName[] = ["性騷法", "性工法"], status: AdminApplicableLawTestResult["prediction"]["decision_status"] = "predicted", masking = true): AdminApplicableLawTestResult => ({ output_contract: "applicable_laws_v1", prediction: { predicted_laws: laws, decision_status: status, decision_reason: status === "predicted" ? null : "缺少當事人關係或適用情境。" }, diagnostics: testResult(0, masking).diagnostics });
beforeEach(() => {
  vi.mocked(runAdminChatTest).mockReset();
  vi.mocked(runAdminApplicableLawTest).mockReset();
  vi.mocked(preparePrivateRequest).mockReset().mockImplementation(async request => request);
  localStorage.clear();
});

describe("admin PII comparison", () => {
  it("accepts the runtime public_dict null maintenance message and normalizes it for comparison", async () => {
    const first = testResult(1);
    first.diagnostics.effective_config = { ...config, maintenance_message: null, environment_document_id: "synthetic-runtime", updated_at: null, updated_by: null };
    vi.mocked(runAdminChatTest).mockResolvedValueOnce(first).mockResolvedValueOnce(testResult(2, false));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    expect(screen.getByRole("combobox", { name: "測試模式" })).toHaveValue("chat");
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成情境" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("合成回答 1");
    const compare = screen.getByRole("button", { name: "只切換後端 PII 再測一次" });
    expect(compare).toBeEnabled();
    fireEvent.click(compare);
    await screen.findByText("合成回答 2");
    const requests = vi.mocked(runAdminChatTest).mock.calls;
    expect(requests[1][1]).toBe(requests[0][1]);
    expect(requests[1][2]).toMatchObject({ maintenance_message: "", enable_anonymization: false, pipeline: DEFAULT_PIPELINE });
    expect(requests[1][2]).not.toHaveProperty("updated_at");
  });

  it("reuses frozen inputs and every writable effective setting while only flipping PII, and retains two runs", async () => {
    vi.mocked(runAdminChatTest).mockResolvedValueOnce(testResult(1)).mockResolvedValueOnce(testResult(2, false)).mockResolvedValueOnce(testResult(3));
    const { unmount } = render(<AdminChatTest csrfToken="admin-token" config={config} />);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成原始訊息" } });
    fireEvent.click(screen.getByText("測試歷史與情境摘要"));
    fireEvent.change(screen.getByRole("textbox", { name: "文字情境摘要" }), { target: { value: "合成原始摘要" } });
    fireEvent.change(screen.getByRole("textbox", { name: "歷史 JSON" }), { target: { value: '[{"role":"user","content":"合成歷史"}]' } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("合成回答 1");
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "尚未執行的新訊息" } });
    fireEvent.change(screen.getByRole("textbox", { name: "文字情境摘要" }), { target: { value: "尚未執行的新摘要" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "本次檢索 Top K" }), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "精簡對話歷史" }));
    fireEvent.click(screen.getByRole("button", { name: "只切換後端 PII 再測一次" }));
    await screen.findByText("合成回答 2");
    const first = vi.mocked(runAdminChatTest).mock.calls[0];
    const second = vi.mocked(runAdminChatTest).mock.calls[1];
    expect(second[1]).toBe(first[1]);
    expect(preparePrivateRequest).toHaveBeenCalledTimes(1);
    expect(second[2]).toEqual({ ...RETRIEVAL_DEFAULTS, openrouter_model: config.openrouter_model, rag_retrieval_top_k: 3, rag_distance_threshold: 0.7, enable_anonymization: false, temperature: 0.2, top_p: 0.9, max_tokens: 1200, reasoning_effort: "low", agent_prompt_sections: config.agent_prompt_sections, rag_collections: config.rag_collections, maintenance_message: "", enable_image_upload: true, development_mode: false, pipeline: DEFAULT_PIPELINE });
    expect(second[2]).not.toHaveProperty("updated_by");
    expect(first[1]).toMatchObject({ contract_version: 4, case_context: { schema_version: 3, summary: "合成原始摘要", facts: {} } });
    expect(screen.getAllByText("生效設定")).toHaveLength(2);
    expect(screen.getAllByText("輸入處理前後")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "只切換後端 PII 再測一次" }));
    await screen.findByText("合成回答 3");
    expect(screen.queryByText("合成回答 1")).not.toBeInTheDocument();
    expect(screen.getByText("合成回答 2")).toBeVisible();
    expect(localStorage.length).toBe(0);
    unmount();
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    expect(screen.queryByText("合成回答 3")).not.toBeInTheDocument();
  });

  it("ignores late completion after stopping a test and permits a new run", async () => {
    let finish!: (value: AdminChatTestResult) => void;
    vi.mocked(runAdminChatTest).mockReturnValueOnce(new Promise(resolve => { finish = resolve; })).mockResolvedValueOnce(testResult(2));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成情境" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(runAdminChatTest).mock.calls[0][3];
    fireEvent.click(screen.getByRole("button", { name: "停止測試" }));
    expect(signal?.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("合成回答 2");
    await act(async () => { finish(testResult(1)); });
    await waitFor(() => expect(screen.queryByText("合成回答 1")).not.toBeInTheDocument());
  });

  it("sends only the approved object and reuses it for backend PII comparison", async () => {
    const approved: ChatRequest = {
      message: "[姓名] 的合成情境",
      history: [{ role: "user", content: "[電子郵件]" }],
      use_rag: true,
      contract_version: 4,
      case_context: { schema_version: 3, revision: 0, facts: {}, summary: "[電話]", summary_origin: "user" },
    };
    Object.freeze(approved.history[0]);
    Object.freeze(approved.history);
    Object.freeze(approved.case_context);
    Object.freeze(approved);
    vi.mocked(preparePrivateRequest).mockResolvedValueOnce(approved);
    vi.mocked(runAdminChatTest).mockResolvedValueOnce(testResult(1, false)).mockResolvedValueOnce(testResult(2));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成測試者的原始情境" } });
    fireEvent.click(screen.getByText("測試歷史與情境摘要"));
    fireEvent.change(screen.getByRole("textbox", { name: "歷史 JSON" }), { target: { value: '[{"role":"user","content":"synthetic@example.com"}]' } });
    fireEvent.change(screen.getByRole("textbox", { name: "文字情境摘要" }), { target: { value: "0912345678" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "後端 PII 遮罩（第二層）" }));
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("合成回答 1");
    expect(preparePrivateRequest).toHaveBeenCalledWith(expect.objectContaining({ message: "合成測試者的原始情境", history: [{ role: "user", content: "synthetic@example.com" }] }), expect.any(AbortSignal));
    const first = vi.mocked(runAdminChatTest).mock.calls[0];
    expect(first[1]).toBe(approved);
    expect(first[2].enable_anonymization).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "未核准的新訊息" } });
    fireEvent.click(screen.getByRole("button", { name: "只切換後端 PII 再測一次" }));
    await screen.findByText("合成回答 2");
    expect(vi.mocked(runAdminChatTest).mock.calls[1][1]).toBe(approved);
    expect(vi.mocked(runAdminChatTest).mock.calls[1][2].enable_anonymization).toBe(true);
    expect(preparePrivateRequest).toHaveBeenCalledTimes(1);
  });

  it("makes no request and creates no result when the privacy review is cancelled", async () => {
    vi.mocked(preparePrivateRequest).mockResolvedValueOnce(null);
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "未核准的合成原文" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "執行本次測試" })).toBeEnabled());
    expect(preparePrivateRequest).toHaveBeenCalledTimes(1);
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("最近兩次測試比較")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "只切換後端 PII 再測一次" })).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0);
  });

  it("requires another review for a new test after any message or summary edits", async () => {
    vi.mocked(runAdminChatTest).mockResolvedValueOnce(testResult(1));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "第一次合成情境" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("合成回答 1");
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "修改後的合成情境" } });
    fireEvent.click(screen.getByText("測試歷史與情境摘要"));
    fireEvent.change(screen.getByRole("textbox", { name: "文字情境摘要" }), { target: { value: "修改後的合成摘要" } });
    vi.mocked(preparePrivateRequest).mockResolvedValueOnce(null);
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "執行本次測試" })).toBeEnabled());
    expect(preparePrivateRequest).toHaveBeenCalledTimes(2);
    expect(vi.mocked(preparePrivateRequest).mock.calls[1][0]).toMatchObject({ message: "修改後的合成情境", case_context: { summary: "修改後的合成摘要" } });
    expect(runAdminChatTest).toHaveBeenCalledTimes(1);
    expect(screen.getByText("合成回答 1")).toBeVisible();
  });

  it("aborts a pending review and ignores a late approval", async () => {
    let approve!: (value: ChatRequest) => void;
    vi.mocked(preparePrivateRequest).mockReturnValueOnce(new Promise(resolve => { approve = resolve; }));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成情境" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    const [request, signal] = vi.mocked(preparePrivateRequest).mock.calls[0];
    fireEvent.click(screen.getByRole("button", { name: "停止測試" }));
    expect(signal?.aborted).toBe(true);
    await act(async () => { approve(request); });
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("最近兩次測試比較")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "執行本次測試" })).toBeEnabled();
  });
});

describe("admin applicable-law prediction", () => {
  const selectPrediction = () => {
    fireEvent.change(screen.getByRole("combobox", { name: "測試模式" }), { target: { value: "applicable_laws_v1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成三法測試情境" } });
  };

  it("uses the independent prediction helper and displays law names in their returned order without a chat reply", async () => {
    vi.mocked(runAdminApplicableLawTest).mockResolvedValueOnce(lawResult(["性工法", "性騷法", "性平法"]));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    selectPrediction();
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("性工法、性騷法、性平法");
    expect(screen.getByRole("heading", { name: "預測的適用法條" })).toBeVisible();
    expect(screen.getByText("由左到右為順位；第一項是主要申訴、調查處理法規。")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "測試回覆" })).not.toBeInTheDocument();
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(runAdminApplicableLawTest).toHaveBeenCalledWith("admin-token", expect.objectContaining({ message: "合成三法測試情境", contract_version: 4 }), expect.objectContaining({ enable_anonymization: true }), expect.any(AbortSignal));
    expect(preparePrivateRequest).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["insufficient_facts", "資訊不足，無法判斷適用法規。"],
    ["no_target_law", "不適用三法。"],
  ] as const)("displays a distinct empty %s result", async (status, text) => {
    vi.mocked(runAdminApplicableLawTest).mockResolvedValueOnce(lawResult([], status));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    selectPrediction();
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    expect(await screen.findByText(text)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "測試回覆" })).not.toBeInTheDocument();
    expect(screen.queryByText("由左到右為順位；第一項是主要申訴、調查處理法規。")).not.toBeInTheDocument();
  });

  it("captures the mode before privacy approval and disables switching during a pending test", async () => {
    let approve!: (request: ChatRequest) => void;
    vi.mocked(preparePrivateRequest).mockReturnValueOnce(new Promise(resolve => { approve = resolve; }));
    vi.mocked(runAdminApplicableLawTest).mockResolvedValueOnce(lawResult());
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    selectPrediction();
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    const selector = screen.getByRole("combobox", { name: "測試模式" });
    expect(selector).toBeDisabled();
    const approved = vi.mocked(preparePrivateRequest).mock.calls[0][0];
    // Even a synthetic change event cannot reinterpret the pending request.
    fireEvent.change(selector, { target: { value: "chat" } });
    await act(async () => { approve(approved); });
    await screen.findByText("性騷法、性工法");
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1);
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(screen.getByText("測試模式：適用法規預測")).toBeVisible();
    expect(selector).toBeEnabled();
  });

  it("compares the original prediction mode and approved request after the selected mode and inputs change", async () => {
    const approved: ChatRequest = Object.freeze({ message: "[姓名] 的已確認情境", history: [], use_rag: true, contract_version: 4 });
    vi.mocked(preparePrivateRequest).mockResolvedValueOnce(approved);
    vi.mocked(runAdminApplicableLawTest).mockResolvedValueOnce(lawResult(["性工法", "性騷法"])).mockResolvedValueOnce(lawResult(["性平法"], "predicted", false));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    selectPrediction();
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("性工法、性騷法");
    fireEvent.change(screen.getByRole("combobox", { name: "測試模式" }), { target: { value: "chat" } });
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "尚未確認的新一般對話" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "本次檢索 Top K" }), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "只切換後端 PII 再測一次" }));
    await screen.findByText("性平法");
    const calls = vi.mocked(runAdminApplicableLawTest).mock.calls;
    expect(calls[0][1]).toBe(approved);
    expect(calls[1][1]).toBe(approved);
    expect(calls[1][2]).toMatchObject({ enable_anonymization: false, rag_retrieval_top_k: 3, pipeline: DEFAULT_PIPELINE, openrouter_model: config.openrouter_model });
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(preparePrivateRequest).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("heading", { name: "預測的適用法條" })).toHaveLength(2);
    expect(localStorage.length).toBe(0);
  });

  it("cancels prediction privacy review without transport or a result", async () => {
    vi.mocked(preparePrivateRequest).mockResolvedValueOnce(null);
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    selectPrediction();
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "執行本次測試" })).toBeEnabled());
    expect(runAdminApplicableLawTest).not.toHaveBeenCalled();
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("最近兩次測試比較")).not.toBeInTheDocument();
  });

  it("ignores a late stopped prediction and safely runs a new normal test", async () => {
    let finish!: (result: AdminApplicableLawTestResult) => void;
    vi.mocked(runAdminApplicableLawTest).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    vi.mocked(runAdminChatTest).mockResolvedValueOnce(testResult(2));
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    selectPrediction();
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await waitFor(() => expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(runAdminApplicableLawTest).mock.calls[0][3];
    fireEvent.click(screen.getByRole("button", { name: "停止測試" }));
    expect(signal?.aborted).toBe(true);
    fireEvent.change(screen.getByRole("combobox", { name: "測試模式" }), { target: { value: "chat" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("合成回答 2");
    await act(async () => { finish(lawResult()); });
    expect(screen.queryByRole("heading", { name: "預測的適用法條" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "測試回覆" })).toBeVisible();
  });

  it("keeps normal and prediction results distinct in the recent-two comparison", async () => {
    vi.mocked(runAdminChatTest).mockResolvedValueOnce(testResult(1));
    vi.mocked(runAdminApplicableLawTest).mockResolvedValueOnce(lawResult());
    render(<AdminChatTest csrfToken="admin-token" config={config} />);
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成一般對話" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("合成回答 1");
    selectPrediction();
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    await screen.findByText("性騷法、性工法");
    expect(screen.getByRole("heading", { name: "測試回覆" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "預測的適用法條" })).toBeVisible();
    expect(preparePrivateRequest).toHaveBeenCalledTimes(2);
  });
});
