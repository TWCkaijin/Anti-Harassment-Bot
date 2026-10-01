import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteScenarioSkill,
  getRuntimeConfig,
  getScenarioSkills,
  updateRuntimeConfig,
  updateScenarioSkill,
  runAdminChatTest,
  type RuntimeConfig,
  type ScenarioSkill,
} from "../services/api";
import AdminPanel from "./AdminPanel";
import PrivacyReviewDialog from "./PrivacyReviewDialog";

vi.mock("../services/api", async () => {
  const actual = await vi.importActual<typeof import("../services/api")>("../services/api");
  return {
    ...actual,
    getRuntimeConfig: vi.fn(),
    getScenarioSkills: vi.fn(),
    deleteScenarioSkill: vi.fn(),
    updateRuntimeConfig: vi.fn(),
    updateScenarioSkill: vi.fn(),
    runAdminChatTest: vi.fn(),
  };
});

const runtimeConfig: RuntimeConfig = {
  openrouter_model: "test/model",
  rag_retrieval_top_k: 3,
  rag_distance_threshold: null,
  enable_anonymization: true,
  temperature: 0.2,
  top_p: 1,
  max_tokens: 1200,
  reasoning_effort: "none",
  agent_prompt_sections: {},
  rag_collections: { law: "laws", judgment: "judgments", remedy: "remedies" },
  maintenance_message: "",
  enable_image_upload: true,
  development_mode: false,
  source: "firestore",
};

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(getRuntimeConfig).mockReset().mockResolvedValue(runtimeConfig);
  vi.mocked(getScenarioSkills).mockReset().mockResolvedValue({ skills: [] });
  vi.mocked(updateRuntimeConfig).mockReset();
  vi.mocked(updateScenarioSkill).mockReset().mockImplementation(async (_token, id, skill) => ({ ...skill, id }));
  vi.mocked(deleteScenarioSkill).mockReset().mockResolvedValue();
  vi.mocked(runAdminChatTest).mockReset();
});

afterEach(() => vi.restoreAllMocks());

describe("AdminPanel Skills actions", () => {
  const skill: ScenarioSkill = {
    id: "resource_help",
    name: "資源與選擇",
    enabled: true,
    priority: 50,
    trigger_keywords: ["資源"],
    instruction: "依使用者需求提供合適的按鈕。",
    actions: [{ action: "tel", label: "撥打專線", phone_number: "113" }],
  };

  async function openSkills(initialSkill = skill) {
    vi.mocked(getScenarioSkills).mockResolvedValue({ skills: [initialSkill] });
    render(<AdminPanel isOpen onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("輸入 ADMIN_API_KEY"), { target: { value: "admin-token" } });
    fireEvent.click(screen.getByRole("button", { name: /驗證並進入/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Skills 設定/ }));
  }

  it("preserves phone actions and saves configured URL and option actions with Skill instructions", async () => {
    await openSkills();
    expect(screen.getByLabelText("電話號碼")).toHaveValue("113");
    fireEvent.change(screen.getByLabelText("情境指令"), { target: { value: "需要網站時提供連結；需要確認意願時顯示選項。" } });
    fireEvent.change(screen.getByLabelText("觸發詞（以逗號分隔）"), { target: { value: "資源, 選擇" } });
    fireEvent.change(screen.getByLabelText("新增按鈕類型"), { target: { value: "url" } });
    fireEvent.click(screen.getByRole("button", { name: "新增按鈕" }));
    const url = within(screen.getByRole("group", { name: "按鈕 2 · 開啟網頁" }));
    fireEvent.change(url.getByLabelText("按鈕文字"), { target: { value: "前往網站" } });
    fireEvent.change(url.getByLabelText("網頁網址"), { target: { value: "https://www.pthg.gov.tw/" } });

    fireEvent.change(screen.getByLabelText("新增按鈕類型"), { target: { value: "options" } });
    fireEvent.click(screen.getByRole("button", { name: "新增按鈕" }));
    const options = within(screen.getByRole("group", { name: "按鈕 3 · 選項按鈕" }));
    fireEvent.change(options.getByLabelText("按鈕文字"), { target: { value: "選擇需求" } });
    fireEvent.change(options.getByLabelText(/^選項 ID/), { target: { value: "support_choice" } });
    fireEvent.change(options.getByLabelText("問題標題"), { target: { value: "你希望得到哪種協助？" } });
    [
      { label: "了解流程", value: "我希望先了解處理流程。" },
      { label: "尋找資源", value: "請協助我尋找合適的支援資源。" },
    ].forEach((option, index) => {
      const fields = within(options.getByRole("group", { name: `選項 ${index + 1}` }));
      fireEvent.change(fields.getByLabelText("選項文字"), { target: { value: option.label } });
      fireEvent.change(fields.getByLabelText("送出的回覆內容"), { target: { value: option.value } });
    });
    fireEvent.click(screen.getByRole("button", { name: "儲存 Skill" }));

    await waitFor(() => expect(updateScenarioSkill).toHaveBeenCalledWith("admin-token", skill.id, {
      name: skill.name,
      enabled: true,
      priority: 50,
      trigger_keywords: ["資源", "選擇"],
      instruction: "需要網站時提供連結；需要確認意願時顯示選項。",
      actions: [
        skill.actions[0],
        { action: "url", label: "前往網站", url: "https://www.pthg.gov.tw/" },
        {
          action: "options",
          id: "support_choice",
          label: "選擇需求",
          title: "你希望得到哪種協助？",
          options: [
            { label: "了解流程", value: "我希望先了解處理流程。" },
            { label: "尋找資源", value: "請協助我尋找合適的支援資源。" },
          ],
        },
      ],
    }));
  });

  it("loads existing URL and option actions for editing and supports removing actions", async () => {
    await openSkills({ ...skill, actions: [
      { action: "url", label: "網站", url: "https://example.org/old" },
      { action: "options", id: "choice_1", label: "選擇", title: "請選擇", options: [{ label: "甲", value: "選甲" }, { label: "乙", value: "選乙" }] },
    ] });
    fireEvent.change(screen.getByLabelText("網頁網址"), { target: { value: "https://example.org/new" } });
    const firstChoice = within(screen.getByRole("group", { name: "選項 1" }));
    fireEvent.change(firstChoice.getByLabelText("送出的回覆內容"), { target: { value: "新的回覆內容" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存 Skill" }));
    await waitFor(() => expect(updateScenarioSkill).toHaveBeenCalledWith("admin-token", skill.id, expect.objectContaining({
      actions: [
        { action: "url", label: "網站", url: "https://example.org/new" },
        { action: "options", id: "choice_1", label: "選擇", title: "請選擇", options: [{ label: "甲", value: "新的回覆內容" }, { label: "乙", value: "選乙" }] },
      ],
    })));
    await screen.findByText("已儲存至共用的 scenario_scripts collection");
    fireEvent.click(screen.getByRole("button", { name: "移除按鈕 1" }));
    expect(screen.queryByLabelText("網頁網址")).not.toBeInTheDocument();
    expect(screen.getByLabelText("問題標題")).toHaveValue("請選擇");
  });

  it("keeps a deleted built-in Skill visible as disabled when the server retains it", async () => {
    await openSkills();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(getScenarioSkills).mockResolvedValueOnce({ skills: [{ ...skill, enabled: false }] });
    fireEvent.click(screen.getByRole("button", { name: "刪除" }));
    expect(await screen.findByText("已停用內建 Skill，可重新啟用")).toBeInTheDocument();
    expect(deleteScenarioSkill).toHaveBeenCalledWith("admin-token", skill.id);
    expect(screen.getByLabelText("啟用此 Skill")).not.toBeChecked();
    expect(screen.getByLabelText("Skill ID")).toHaveValue(skill.id);
  });
});

describe("AdminPanel runtime config", () => {
  async function openSystemSettings() {
    render(<AdminPanel isOpen onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("輸入 ADMIN_API_KEY"), {
      target: { value: "admin-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: /驗證並進入/ }));
    return screen.findByLabelText("維護訊息");
  }

  it("saves the manual review and image switches independently and refreshes the active client after save", async () => {
    const changed = vi.fn();
    vi.mocked(updateRuntimeConfig).mockImplementation(async (_token, update) => ({ ...runtimeConfig, ...update, pipeline: runtimeConfig.pipeline }));
    render(<AdminPanel isOpen onClose={vi.fn()} onRuntimeConfigChanged={changed} />);
    fireEvent.change(screen.getByPlaceholderText("輸入 ADMIN_API_KEY"), { target: { value: "admin-token" } });
    fireEvent.click(screen.getByRole("button", { name: /驗證並進入/ }));
    const review = await screen.findByRole("checkbox", { name: "送出前遮罩確認" });
    const images = screen.getByRole("checkbox", { name: "允許圖片上傳" });
    expect(review).toBeChecked(); expect(images).toBeChecked();
    fireEvent.click(review);
    expect(updateRuntimeConfig).not.toHaveBeenCalled(); expect(changed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "儲存變更" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(updateRuntimeConfig).toHaveBeenLastCalledWith("admin-token", expect.objectContaining({ enable_client_privacy_review: false, enable_image_upload: true, enable_anonymization: true }));
    expect(review).not.toBeChecked();
    fireEvent.click(review); fireEvent.click(images);
    fireEvent.click(screen.getByRole("button", { name: "儲存變更" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
    expect(updateRuntimeConfig).toHaveBeenLastCalledWith("admin-token", expect.objectContaining({ enable_client_privacy_review: true, enable_image_upload: false, enable_anonymization: true }));
  });

  it("runs the real automatic preparation path in admin diagnostics when manual review is disabled", async () => {
    vi.mocked(getRuntimeConfig).mockResolvedValue({ ...runtimeConfig, enable_client_privacy_review: false });
    vi.mocked(runAdminChatTest).mockResolvedValue({ response: { reply: "合成診斷回覆", session_id: "test", anonymized: true, rag_used: { status: false, sources: [] }, interaction_mode: "answer", clarifying_questions: [], suggested_replies: [], action_buttons: [] }, diagnostics: {} });
    await openSystemSettings(); render(<PrivacyReviewDialog />);
    fireEvent.click(screen.getByRole("button", { name: /本次診斷測試/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "synthetic@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    expect(await screen.findByText("合成診斷回覆")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "確認即將送出的內容" })).not.toBeInTheDocument();
    expect(runAdminChatTest).toHaveBeenCalledWith("admin-token", expect.objectContaining({ message: "[電子郵件]" }), expect.objectContaining({ enable_client_privacy_review: false }), expect.any(AbortSignal));
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
  });

  it("loads and saves persistent pipeline settings only when the administrator saves", async () => {
    vi.mocked(updateRuntimeConfig).mockResolvedValueOnce(runtimeConfig);
    await openSystemSettings();
    expect(screen.getByRole("checkbox", { name: "精簡對話歷史" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "精簡對話歷史" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "遮罩檢索查詢" }));
    fireEvent.change(screen.getByLabelText("內容處理方式"), { target: { value: "annotate" } });
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "儲存變更" }));
    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalledWith("admin-token", expect.objectContaining({ pipeline: expect.objectContaining({ trim_history: false, mask_retrieval_query: false, content_policy: "annotate", enable_rag: true, history_max_messages: 40 }) })));
  });

  it("runs temporary overrides without saving config or storing test messages", async () => {
    vi.mocked(runAdminChatTest).mockResolvedValue({ response: { reply: "合成診斷回覆", session_id: "test", anonymized: true, rag_used: { status: false, sources: [] }, interaction_mode: "answer", clarifying_questions: [], suggested_replies: [], action_buttons: [] }, diagnostics: { history: { removed_messages: 2 }, pii: { message: { changed_values: 1 } } } });
    await openSystemSettings();
    render(<PrivacyReviewDialog />);
    fireEvent.click(screen.getByRole("button", { name: /本次診斷測試/ }));
    expect(screen.queryByRole("button", { name: "儲存變更" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "synthetic-private-message" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "精簡對話歷史" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "後端 PII 遮罩（第二層）" }));
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    expect(runAdminChatTest).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "確認並送出" }));
    expect(await screen.findByText("合成診斷回覆")).toBeVisible();
    expect(runAdminChatTest).toHaveBeenCalledWith("admin-token", { message: "synthetic-private-message", history: [], use_rag: true, contract_version: 4, case_context: { schema_version: 3, revision: 0, facts: {}, summary: "", summary_origin: "user" } }, expect.objectContaining({ enable_anonymization: false, pipeline: expect.objectContaining({ trim_history: false }) }), expect.any(AbortSignal));
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
    expect(JSON.stringify(localStorage)).not.toContain("synthetic-private-message");
    fireEvent.click(screen.getByRole("button", { name: /系統設定/ }));
    expect(screen.getByRole("checkbox", { name: "精簡對話歷史" })).toBeChecked();
  });

  it("rejects malformed diagnostics input before any test request", async () => {
    await openSystemSettings();
    fireEvent.click(screen.getByRole("button", { name: /本次診斷測試/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成案例" } });
    fireEvent.click(screen.getByText("測試歷史與情境摘要"));
    fireEvent.change(screen.getByRole("textbox", { name: "歷史 JSON" }), { target: { value: '[{"role":"system","content":"invalid"}]' } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    expect(screen.getByRole("alert")).toHaveTextContent("歷史必須是 user／assistant");
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
  });

  it("aborts the diagnostic request when leaving its tab without saving overrides", async () => {
    vi.mocked(runAdminChatTest).mockReturnValue(new Promise(() => {}));
    await openSystemSettings();
    render(<PrivacyReviewDialog />);
    fireEvent.click(screen.getByRole("button", { name: /本次診斷測試/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "測試訊息" }), { target: { value: "合成情境" } });
    fireEvent.click(screen.getByRole("button", { name: "執行本次測試" }));
    fireEvent.click(screen.getByRole("button", { name: "確認並送出" }));
    await waitFor(() => expect(runAdminChatTest).toHaveBeenCalled());
    const signal = vi.mocked(runAdminChatTest).mock.calls[0][3];
    expect(signal?.aborted).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /系統設定/ }));
    expect(signal?.aborted).toBe(true);
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
  });

  it("shows the active maintenance message and resumes only after saving the cleared field", async () => {
    vi.mocked(getRuntimeConfig).mockResolvedValueOnce({
      ...runtimeConfig,
      maintenance_message: "TESTING 1 from Kai",
    });
    vi.mocked(updateRuntimeConfig).mockResolvedValueOnce(runtimeConfig);
    const message = await openSystemSettings();

    expect(message).toHaveValue("TESTING 1 from Kai");
    expect(screen.getByText("目前服務已暫停（維護模式）")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清空維護訊息" }));
    expect(message).toHaveValue("");
    expect(updateRuntimeConfig).not.toHaveBeenCalled();
    expect(screen.getByText("目前服務已暫停（維護模式）")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "儲存變更" }));

    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalledWith("admin-token", expect.objectContaining({
      maintenance_message: "",
      openrouter_model: runtimeConfig.openrouter_model,
      temperature: runtimeConfig.temperature,
      top_p: runtimeConfig.top_p,
      max_tokens: runtimeConfig.max_tokens,
      reasoning_effort: runtimeConfig.reasoning_effort,
      rag_retrieval_top_k: runtimeConfig.rag_retrieval_top_k,
      rag_distance_threshold: runtimeConfig.rag_distance_threshold,
      rag_collections: runtimeConfig.rag_collections,
      enable_anonymization: runtimeConfig.enable_anonymization,
      enable_image_upload: runtimeConfig.enable_image_upload,
      development_mode: runtimeConfig.development_mode,
    })));
    expect(await screen.findByText("目前維護模式已關閉")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已同步" })).toBeDisabled();
  });

  it("allows an administrator to intentionally save a maintenance message", async () => {
    const maintenanceMessage = "系統維護中，請稍後再試。";
    vi.mocked(updateRuntimeConfig).mockResolvedValueOnce({ ...runtimeConfig, maintenance_message: maintenanceMessage });
    const message = await openSystemSettings();
    expect(screen.getByRole("button", { name: "清空維護訊息" })).toBeDisabled();
    fireEvent.change(message, { target: { value: maintenanceMessage } });
    expect(screen.getByText("目前維護模式已關閉")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "儲存變更" }));

    await waitFor(() => expect(updateRuntimeConfig).toHaveBeenCalledWith("admin-token", expect.objectContaining({
      maintenance_message: maintenanceMessage,
    })));
    expect(await screen.findByText("目前服務已暫停（維護模式）")).toBeInTheDocument();
  });

  it("retains the pending cleared message and active status when saving fails", async () => {
    vi.mocked(getRuntimeConfig).mockResolvedValueOnce({ ...runtimeConfig, maintenance_message: "TESTING 1 from Kai" });
    vi.mocked(updateRuntimeConfig).mockRejectedValueOnce(new ApiError(500, "儲存失敗", "設定暫時無法儲存"));
    const message = await openSystemSettings();
    fireEvent.click(screen.getByRole("button", { name: "清空維護訊息" }));
    fireEvent.click(screen.getByRole("button", { name: "儲存變更" }));

    expect(await screen.findByText("設定暫時無法儲存")).toBeInTheDocument();
    expect(message).toHaveValue("");
    expect(screen.getByText("目前服務已暫停（維護模式）")).toBeInTheDocument();
    expect(screen.queryByText("目前維護模式已關閉")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "儲存變更" })).toBeEnabled();
  });

  it("shows a normalized validation detail when saving fails", async () => {
    vi.mocked(updateRuntimeConfig).mockRejectedValueOnce(
      new ApiError(
        422,
        "API 請求失敗 (422)",
        "body.rag_distance_threshold: Must be between 0 and 2",
      ),
    );
    render(<AdminPanel isOpen onClose={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText("輸入 ADMIN_API_KEY"), {
      target: { value: "admin-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: /驗證並進入/ }));

    await waitFor(() => expect(getRuntimeConfig).toHaveBeenCalledWith("admin-token"));
    fireEvent.change(await screen.findByLabelText("OpenRouter Model"), {
      target: { value: "test/next-model" },
    });
    fireEvent.click(screen.getByRole("button", { name: "儲存變更" }));

    expect(await screen.findByText(
      "body.rag_distance_threshold: Must be between 0 and 2",
    )).toBeInTheDocument();
  });
});
