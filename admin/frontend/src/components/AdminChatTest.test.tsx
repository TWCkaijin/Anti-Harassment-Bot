import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AdminChatTest from "./AdminChatTest";
import { getRerankModels, runAdminApplicableLawTest, runAdminChatTest, updateRuntimeConfig, type AdminApplicableLawTestResult, type AdminChatTestResult, type ChatRequest, type RuntimeConfig } from "../services/api";
import { DEFAULT_PIPELINE } from "../services/pipeline";
import { preparePrivateRequest } from "../services/privateRequest";

vi.mock("../services/api", async () => ({ ...await vi.importActual<typeof import("../services/api")>("../services/api"), getRerankModels: vi.fn(), runAdminChatTest: vi.fn(), runAdminApplicableLawTest: vi.fn(), updateRuntimeConfig: vi.fn() }));
vi.mock("../services/privateRequest", () => ({ preparePrivateRequest: vi.fn() }));
const config: RuntimeConfig = { openrouter_model: "synthetic/model", rag_retrieval_top_k: 3, rag_distance_threshold: 0.7, enable_anonymization: true, temperature: 0.2, top_p: 0.9, max_tokens: 1200, reasoning_effort: "low", agent_prompt_sections: { core_mission: "synthetic prompt" }, rag_collections: { law: "laws", judgment: "judgments", remedy: "remedies" }, maintenance_message: "", enable_image_upload: true, development_mode: false, source: "firestore", pipeline: DEFAULT_PIPELINE, environment_document_id: "app_dev" };
const result = (request?: ChatRequest, reply = "合成回答", extra: Partial<AdminChatTestResult["response"]> = {}): AdminChatTestResult => ({ response: { reply, session_id: "test", anonymized: true, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [], contract_version: 4, context_revision: request?.case_context?.revision ?? 0, summary_update: null, clarification: null, ...extra }, diagnostics: { effective_config: { ...config, maintenance_message: null, updated_by: "metadata" }, pii: { masked: true } } });
const lawResult = (status: AdminApplicableLawTestResult["prediction"]["decision_status"] = "predicted"): AdminApplicableLawTestResult => ({ output_contract: "applicable_laws_v1", prediction: { predicted_laws: status === "predicted" ? ["性工法", "性騷法"] : [], decision_status: status, decision_reason: status === "predicted" ? null : "合成原因" }, diagnostics: { effective_config: config } });
const timeline = () => within(screen.getByLabelText("診斷對話訊息"));
const send = (text = "合成情境") => { fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: text } }); fireEvent.click(screen.getByRole("button", { name: "傳送訊息" })); };
const diagnostics = () => fireEvent.click(screen.getByRole("tab", { name: /診斷結果/ }));
const editSummary = () => { fireEvent.click(screen.getByRole("button", { name: "顯示及更改摘要" })); fireEvent.click(screen.getByRole("button", { name: "修改摘要" })); };

beforeEach(() => {
  vi.mocked(getRerankModels).mockReset().mockResolvedValue({ data: [] });
  vi.mocked(runAdminChatTest).mockReset().mockImplementation(async (_token, request) => result(request));
  vi.mocked(runAdminApplicableLawTest).mockReset().mockResolvedValue(lawResult());
  vi.mocked(preparePrivateRequest).mockReset().mockImplementation(async request => request);
  vi.mocked(updateRuntimeConfig).mockReset();
  localStorage.clear(); sessionStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe("diagnostic conversation parity", () => {
  it("sends successive turns with history and frozen temporary settings without saving them", async () => {
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    fireEvent.change(screen.getByLabelText("本次模型"), { target: { value: "temporary/model" } });
    send("第一個情境");
    await timeline().findByText("合成回答");
    send("接著補充");
    await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(2));
    const [token, request, overrides] = vi.mocked(runAdminChatTest).mock.calls[1];
    expect(token).toBe("csrf");
    expect(request.history).toEqual([{ role: "user", content: "第一個情境" }, { role: "assistant", content: "合成回答" }]);
    expect(overrides).toMatchObject({ openrouter_model: "temporary/model", pipeline: DEFAULT_PIPELINE });
    expect(overrides).not.toHaveProperty("revision");
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });
  it("uses shared typed choices and carries their signed answer and summary revision", async () => {
    const question = { question_id: "open.work", context_revision: 1, question: "對方與您是什麼關係？", reason: "了解處理管道", options: [{ label: "主管", value: "主管" }, { label: "同事", value: "同事" }], selection_mode: "single" as const, max_selections: 1, validation_token: "signed-test-token" };
    vi.mocked(runAdminChatTest).mockImplementationOnce(async (_token, request) => result(request, "請補充彼此關係。", { interaction_mode: "clarify", clarification: question, summary_update: { base_revision: 0, summary: "合成工作情境", evidence: [request.message] } }));
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    send();
    fireEvent.click(await screen.findByRole("radio", { name: "主管" }));
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(2));
    expect(vi.mocked(runAdminChatTest).mock.calls[1][1]).toMatchObject({ case_context: { summary: "合成工作情境", revision: 1 }, clarification_answer: { question_id: question.question_id, context_revision: 1, validation_token: "signed-test-token", status: "provided", value: "主管" } });
  });
  it("edits summary, supersedes stale answers and regenerates without old conflicting history", async () => {
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    send("原先描述同事的問題"); await timeline().findByText("合成回答");
    editSummary(); fireEvent.change(screen.getByLabelText("已知情境"), { target: { value: "更正：對方是主管。" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存並重新回答" }));
    await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(2));
    expect(vi.mocked(runAdminChatTest).mock.calls[1][1]).toMatchObject({ message: "原先描述同事的問題", history: [], regenerate_from_summary: true, case_context: { summary: "更正：對方是主管。", revision: 1, summary_origin: "user" } });
  });
  it("keeps current context when the server returns a stale summary revision", async () => {
    vi.mocked(runAdminChatTest).mockImplementationOnce(async (_token, request) => result(request, "不應顯示", { context_revision: 99 }));
    render(<AdminChatTest csrfToken="csrf" config={config} />); send();
    await waitFor(() => expect(screen.getAllByText(/回覆的摘要版本不符/).length).toBeGreaterThan(0));
    expect(timeline().queryByText("不應顯示")).not.toBeInTheDocument();
    editSummary(); expect(screen.getByLabelText("已知情境")).toHaveValue("");
  });
  it("displays emotion and resource actions through the normal message component", async () => {
    vi.mocked(runAdminChatTest).mockImplementationOnce(async (_token, request) => result(request, "可以先看看以下資源。", { emotion: "擔心", emotion_color: "orange", action_buttons: [{ action: "url", label: "合成資源網站", url: "https://example.org/resource" }] }));
    render(<AdminChatTest csrfToken="csrf" config={config} />); send();
    expect(await timeline().findByText("擔心")).toBeVisible();
    expect(timeline().getByRole("link", { name: /合成資源網站/ })).toHaveAttribute("href", "https://example.org/resource");
    fireEvent.click(screen.getByRole("checkbox", { name: "顯示心情分析" }));
    expect(timeline().queryByText("擔心")).not.toBeInTheDocument();
  });
  it("attaches an image locally and sends it with the next turn only", async () => {
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    const bytes = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="), char => char.charCodeAt(0));
    fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [new File([bytes], "fixture.png", { type: "image/png" })] } });
    await screen.findByAltText("已選擇的圖片");
    expect(runAdminChatTest).not.toHaveBeenCalled();
    send("請看這張圖"); await timeline().findByText("合成回答");
    expect(vi.mocked(runAdminChatTest).mock.calls[0][1].image_base64).toMatch(/^data:image\/png;base64,/);
    send("下一個問題"); await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(2));
    expect(vi.mocked(runAdminChatTest).mock.calls[1][1].image_base64).toBeUndefined();
  });
  it("disables image attachments with the temporary setting and restores the environment baseline", async () => {
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "允許圖片附件" }));
    expect(screen.queryByRole("button", { name: "加入圖片" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "還原環境設定" }));
    expect(screen.getByRole("button", { name: "加入圖片" })).toBeEnabled();
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
  });
  it("aborts on leaving and ignores a late completion after stopping and starting another turn", async () => {
    let complete!: (value: AdminChatTestResult) => void;
    vi.mocked(runAdminChatTest).mockReturnValueOnce(new Promise(resolve => { complete = resolve; }));
    const { unmount } = render(<AdminChatTest csrfToken="csrf" config={config} />);
    send("舊問題"); await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledOnce());
    const signal = vi.mocked(runAdminChatTest).mock.calls[0][3];
    fireEvent.click(screen.getByRole("button", { name: "停止回覆" }));
    expect(signal?.aborted).toBe(true);
    send("新問題"); await timeline().findByText("合成回答");
    await act(async () => complete(result(undefined, "過時回答")));
    expect(timeline().queryByText("過時回答")).not.toBeInTheDocument();
    vi.mocked(runAdminChatTest).mockReturnValueOnce(new Promise(() => {}));
    send("離開前問題"); await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(3));
    const pending = vi.mocked(runAdminChatTest).mock.calls[2][3]; unmount(); expect(pending?.aborted).toBe(true);
  });
  it("does not send or clear the draft when local preparation is cancelled", async () => {
    vi.mocked(preparePrivateRequest).mockResolvedValueOnce(null);
    render(<AdminChatTest csrfToken="csrf" config={config} />); send("保留草稿");
    await waitFor(() => expect(screen.getByRole("button", { name: "傳送訊息" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: "測試訊息" })).toHaveValue("保留草稿");
    expect(runAdminChatTest).not.toHaveBeenCalled(); expect(timeline().queryByText("保留草稿")).not.toBeInTheDocument();
  });
  it("only displays approved content and compares the same frozen request without changing conversation", async () => {
    const approved = Object.freeze({ message: "[姓名] 的情境", history: [], use_rag: true, contract_version: 4, case_context: { schema_version: 3, revision: 0, facts: {}, summary: "[電話]", summary_origin: "user" } } satisfies ChatRequest);
    vi.mocked(preparePrivateRequest).mockResolvedValueOnce(approved);
    render(<AdminChatTest csrfToken="csrf" config={config} />); send("未遮罩姓名");
    await timeline().findByText("合成回答");
    expect(timeline().queryByText("未遮罩姓名")).not.toBeInTheDocument();
    expect(vi.mocked(runAdminChatTest).mock.calls[0][1]).toBe(approved);
    fireEvent.change(screen.getByLabelText("本次模型"), { target: { value: "other/model" } });
    diagnostics(); fireEvent.click(screen.getByRole("button", { name: "只切換後端 PII 再測一次" }));
    await waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(2));
    const comparison = vi.mocked(runAdminChatTest).mock.calls[1];
    expect(comparison[1]).toBe(approved); expect(comparison[2]).toMatchObject({ openrouter_model: "synthetic/model", maintenance_message: "", enable_anonymization: false });
    expect(comparison[2]).not.toHaveProperty("updated_by");
    expect(preparePrivateRequest).toHaveBeenCalledOnce();
    expect(timeline().getAllByText("合成回答")).toHaveLength(1);
    expect(screen.getAllByText("生效設定")).toHaveLength(2);
  });
  it("clears conversation and summary while keeping temporary controls", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    fireEvent.change(screen.getByLabelText("本次模型"), { target: { value: "keep/model" } });
    send(); await timeline().findByText("合成回答");
    fireEvent.click(screen.getByRole("button", { name: "新對話" }));
    expect(timeline().queryByText("合成回答")).not.toBeInTheDocument();
    expect(screen.getByLabelText("本次模型")).toHaveValue("keep/model");
    expect(localStorage.length).toBe(0);
  });
});

describe("independent prediction and diagnostics", () => {
  it.each([["predicted", "性工法、性騷法"], ["insufficient_facts", "資訊不足，無法判斷適用法規。"], ["no_target_law", "不適用三法。"]] as const)("renders %s without issuing a chat request", async (status, text) => {
    vi.mocked(runAdminApplicableLawTest).mockResolvedValue(lawResult(status));
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    fireEvent.change(screen.getByLabelText("測試模式"), { target: { value: "applicable_laws_v1" } }); send();
    expect(await timeline().findByText(text)).toBeVisible(); expect(runAdminChatTest).not.toHaveBeenCalled();
    diagnostics(); expect(screen.getByRole("heading", { name: "預測的適用法條" })).toBeVisible();
  });
  it("uses the original prediction mode for comparison even after switching the composer mode", async () => {
    render(<AdminChatTest csrfToken="csrf" config={config} />);
    fireEvent.change(screen.getByLabelText("測試模式"), { target: { value: "applicable_laws_v1" } }); send(); await timeline().findByText("性工法、性騷法");
    fireEvent.change(screen.getByLabelText("測試模式"), { target: { value: "chat" } }); diagnostics();
    fireEvent.click(screen.getByRole("button", { name: "只切換後端 PII 再測一次" }));
    await waitFor(() => expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(2));
    expect(vi.mocked(runAdminApplicableLawTest).mock.calls[1][1]).toBe(vi.mocked(runAdminApplicableLawTest).mock.calls[0][1]);
    expect(runAdminChatTest).not.toHaveBeenCalled();
  });
});
