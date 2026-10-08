import { useMemo, useState, type ReactNode } from "react";

import {
  ApiError,
  deleteScenarioSkill,
  getRuntimeConfig,
  getScenarioSkills,
  resetRuntimeConfig,
  seedRuntimeConfig,
  seedScenarioScripts,
  updateRuntimeConfig,
  updateScenarioSkill,
  type RuntimeConfig,
  type RuntimeConfigUpdate,
  type ScenarioSkill,
  type ScenarioSkillInput,
} from "../services/api";
import RerankSettings from "./RerankSettings";
import { retrievalSettings } from "../services/retrievalSettings";
import MaterialIcon from "./MaterialIcon";
import SkillActionsEditor from "./SkillActionsEditor";
import AdminChatTest from "./AdminChatTest";
import AdminBatchPrediction from "./AdminBatchPrediction";
import AdminBehaviorTests from "./AdminBehaviorTests";
import PipelineSettingsEditor from "./PipelineSettingsEditor";
import { DEFAULT_PIPELINE } from "../services/pipeline";

interface AdminPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onRuntimeConfigChanged?: () => void;
}

type AdminTab = "system" | "prompts" | "skills" | "test" | "batch" | "behavior";

const TOKEN_STORAGE_KEY = "harassment_bot_admin_token";
// TODO(auth): Replace the persistent static token with short-lived admin authentication.
// Token lifecycle changes are intentionally outside this implementation batch.
const PROMPT_SECTION_FIELDS = [
  ["core_mission", "你的核心使命", 5],
  ["communication_principles", "溝通原則", 5],
  ["important_resources", "重要通報資源", 4],
  ["limitations", "限制說明", 4],
  ["language", "語言", 3],
  ["output_format", "強制輸出格式", 7],
  ["analysis_rules", "分析規則", 10],
  ["retrieval_instructions", "檢索指令", 6],
] as const;

const emptyPromptSections = Object.fromEntries(PROMPT_SECTION_FIELDS.map(([key]) => [key, ""]));
const emptyConfig: RuntimeConfig = {
  pipeline: DEFAULT_PIPELINE,
  openrouter_model: "",
  rag_retrieval_top_k: 3,
  rag_distance_threshold: null,
  enable_anonymization: true,
  temperature: 0.2,
  top_p: 1,
  max_tokens: 1200,
  reasoning_effort: "none",
  agent_prompt_sections: emptyPromptSections,
  rag_collections: { law: "rag_documents", judgment: "rag_judgments", remedy: "rag_remedies" },
  maintenance_message: "",
  enable_image_upload: true,
  enable_client_privacy_review: true,
  development_mode: false,
  source: "local",
};

function emptySkill(): ScenarioSkill {
  return {
    id: `skill_${Date.now().toString(36)}`,
    name: "新情境腳本",
    enabled: true,
    priority: 50,
    trigger_keywords: [],
    instruction: "",
    actions: [],
  };
}

function configToUpdate(config: RuntimeConfig): RuntimeConfigUpdate {
  return {
    pipeline: { ...DEFAULT_PIPELINE, ...config.pipeline },
    ...retrievalSettings(config),
    openrouter_model: config.openrouter_model,
    rag_retrieval_top_k: config.rag_retrieval_top_k,
    rag_distance_threshold: config.rag_distance_threshold,
    enable_anonymization: config.enable_anonymization,
    temperature: config.temperature,
    top_p: config.top_p,
    max_tokens: config.max_tokens,
    reasoning_effort: config.reasoning_effort,
    agent_prompt_sections: config.agent_prompt_sections,
    rag_collections: config.rag_collections,
    maintenance_message: config.maintenance_message ?? "",
    enable_image_upload: config.enable_image_upload,
    enable_client_privacy_review: config.enable_client_privacy_review !== false,
    development_mode: config.development_mode,
  };
}

function skillToInput(skill: ScenarioSkill): ScenarioSkillInput {
  return {
    name: skill.name,
    enabled: skill.enabled,
    priority: skill.priority,
    trigger_keywords: skill.trigger_keywords,
    instruction: skill.instruction,
    actions: skill.actions,
  };
}

const tabs: Array<{ id: AdminTab; label: string; icon: string }> = [
  { id: "system", label: "系統設定", icon: "tune" },
  { id: "prompts", label: "Prompt 設定", icon: "edit_note" },
  { id: "skills", label: "Skills 設定", icon: "account_tree" },
  { id: "test", label: "本次診斷測試", icon: "science" },
  { id: "batch", label: "批次預測", icon: "description" },
  { id: "behavior", label: "對話行為測試", icon: "fact_check" },
];

export default function AdminPanel({ isOpen, onClose, onRuntimeConfigChanged }: AdminPanelProps) {
  const [adminToken, setAdminToken] = useState(() => localStorage.getItem(TOKEN_STORAGE_KEY) ?? "");
  const [verifiedToken, setVerifiedToken] = useState("");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [activeTab, setActiveTab] = useState<AdminTab>("system");
  const [config, setConfig] = useState<RuntimeConfig>(emptyConfig);
  const [lastLoadedConfig, setLastLoadedConfig] = useState<RuntimeConfig | null>(null);
  const [skills, setSkills] = useState<ScenarioSkill[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [batchHasData, setBatchHasData] = useState(false);
  const [behaviorHasData, setBehaviorHasData] = useState(false);

  const selectedSkill = skills.find((skill) => skill.id === selectedSkillId) ?? null;
  const runtimeDirty = useMemo(() => {
    if (!lastLoadedConfig) return false;
    return JSON.stringify(configToUpdate(config)) !== JSON.stringify(configToUpdate(lastLoadedConfig));
  }, [config, lastLoadedConfig]);

  const applyConfig = (nextConfig: RuntimeConfig) => {
    const normalized = {
      ...nextConfig,
      enable_client_privacy_review: nextConfig.enable_client_privacy_review !== false,
      pipeline: { ...DEFAULT_PIPELINE, ...nextConfig.pipeline },
      agent_prompt_sections: { ...emptyPromptSections, ...nextConfig.agent_prompt_sections },
    };
    setConfig(normalized);
    setLastLoadedConfig(normalized);
  };

  const handleError = (err: unknown, fallback: string) => {
    if (err instanceof ApiError) {
      if (err.status === 401) {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        setVerifiedToken("");
        setIsAuthenticated(false);
        setError("Token 無效或已失效，請重新驗證");
        return;
      }
      setError(err.debugMessage ?? err.detail ?? err.message);
      return;
    }
    setError(err instanceof Error ? err.message : fallback);
  };

  const verifyToken = async () => {
    if (!adminToken.trim()) return setError("請先輸入 Admin Token");
    setIsLoading(true);
    setError("");
    try {
      const token = adminToken.trim();
      const [nextConfig, skillResult] = await Promise.all([getRuntimeConfig(token), getScenarioSkills(token)]);
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
      setVerifiedToken(token);
      setIsAuthenticated(true);
      applyConfig(nextConfig);
      setSkills(skillResult.skills);
      setSelectedSkillId(skillResult.skills[0]?.id ?? null);
      setStatus("已載入目前環境的 Runtime 設定與共用 Skills");
    } catch (err) {
      handleError(err, "Token 驗證失敗");
    } finally {
      setIsLoading(false);
    }
  };

  const saveRuntime = async () => {
    if (!verifiedToken) return;
    setIsSaving(true);
    setError("");
    try {
      applyConfig(await updateRuntimeConfig(verifiedToken, configToUpdate(config)));
      onRuntimeConfigChanged?.();
      setStatus("已儲存至目前環境的 Firestore runtime_config");
    } catch (err) {
      handleError(err, "儲存 Runtime 設定失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const seedRuntime = async () => {
    if (!verifiedToken) return;
    setIsSaving(true);
    try {
      applyConfig(await seedRuntimeConfig(verifiedToken));
      onRuntimeConfigChanged?.();
      setStatus("已補齊目前環境的 Runtime 設定");
    } catch (err) {
      handleError(err, "補齊設定失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const resetRuntime = async () => {
    if (!verifiedToken) return;
    setIsSaving(true);
    try {
      applyConfig(await resetRuntimeConfig(verifiedToken));
      onRuntimeConfigChanged?.();
      setStatus("已重置為程式內建預設設定");
    } catch (err) {
      handleError(err, "重置設定失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const updateSkill = (updates: Partial<ScenarioSkill>) => {
    if (!selectedSkill) return;
    setSkills((current) => current.map((skill) => skill.id === selectedSkill.id ? { ...skill, ...updates } : skill));
  };

  const createSkill = () => {
    const skill = emptySkill();
    setSkills((current) => [...current, skill]);
    setSelectedSkillId(skill.id);
    setStatus("請完成 Skill 名稱、觸發詞與情境指令後儲存");
  };

  const saveSkill = async () => {
    if (!verifiedToken || !selectedSkill) return;
    setIsSaving(true);
    try {
      const saved = await updateScenarioSkill(verifiedToken, selectedSkill.id, skillToInput(selectedSkill));
      setSkills((current) => current.map((skill) => skill.id === saved.id ? saved : skill));
      setStatus("已儲存至共用的 scenario_scripts collection");
    } catch (err) {
      handleError(err, "儲存 Skill 失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const removeSkill = async () => {
    if (!verifiedToken || !selectedSkill) return;
    if (!window.confirm(`刪除「${selectedSkill.name}」？內建 Skill 會改為停用。`)) return;
    setIsSaving(true);
    try {
      await deleteScenarioSkill(verifiedToken, selectedSkill.id);
      const result = await getScenarioSkills(verifiedToken);
      const retainedSkill = result.skills.find((skill) => skill.id === selectedSkill.id);
      setSkills((current) => retainedSkill
        ? current.map((skill) => skill.id === selectedSkill.id ? retainedSkill : skill)
        : current.filter((skill) => skill.id !== selectedSkill.id));
      setSelectedSkillId(retainedSkill?.id ?? null);
      setStatus(retainedSkill ? "已停用內建 Skill，可重新啟用" : "已刪除共用 Skill");
    } catch (err) {
      handleError(err, "刪除 Skill 失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const seedSkill = async () => {
    if (!verifiedToken) return;
    setIsSaving(true);
    try {
      await seedScenarioScripts(verifiedToken);
      const result = await getScenarioSkills(verifiedToken);
      setSkills(result.skills);
      setSelectedSkillId("call_support");
      setStatus("已建立共用的範例 Skills");
    } catch (err) {
      handleError(err, "建立範例 Skill 失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const close = () => {
    if ((batchHasData || behaviorHasData) && !window.confirm("關閉後台會停止測試並清除本次資料。請先下載 JSON 或 Excel；確定關閉？")) return;
    setBatchHasData(false);
    setBehaviorHasData(false);
    setIsAuthenticated(false);
    setVerifiedToken("");
    setStatus("");
    setError("");
    onClose();
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 z-[60] bg-black/30" onClick={close} />
      <section className="fixed inset-y-0 right-0 z-[70] flex w-[980px] max-w-full flex-col border-l border-outline/20 bg-white shadow-float animate-fade-in-up">
        <header className="flex items-center justify-between border-b border-outline/15 px-5 py-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-lg font-bold text-on-surface"><MaterialIcon icon="admin_panel_settings" size={21} />Runtime Admin</h2>
            <p className="mt-1 text-xs text-on-surface/50">{isAuthenticated ? `${config.source} · ${config.updated_at ?? "已驗證"}` : "需要伺服器驗證"}</p>
          </div>
          <button onClick={close} className="rounded-lg p-2 hover:bg-surface-container" aria-label="關閉"><MaterialIcon icon="close" size={20} /></button>
        </header>

        {!isAuthenticated ? (
          <div className="m-auto w-full max-w-md p-6">
            <div className="space-y-4 rounded-lg border border-outline/20 bg-surface-container-low p-5">
              <div><h3 className="flex items-center gap-2 font-semibold"><MaterialIcon icon="lock" size={19} />Admin 驗證</h3><p className="mt-2 text-sm text-on-surface/60">驗證後才能讀取及修改設定。</p></div>
              <input value={adminToken} onChange={(event) => setAdminToken(event.target.value)} onKeyDown={(event) => event.key === "Enter" && verifyToken()} type="password" autoComplete="current-password" placeholder="輸入 ADMIN_API_KEY" className="w-full rounded-lg border border-outline/30 px-3 py-2.5 text-sm outline-none focus:border-primary" />
              <button onClick={verifyToken} disabled={isLoading || !adminToken.trim()} className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"><MaterialIcon icon="verified_user" size={18} />{isLoading ? "驗證中" : "驗證並進入"}</button>
              {error && <p className="text-sm text-error">{error}</p>}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
            <nav className="flex w-full shrink-0 border-b border-outline/15 bg-surface-container-low p-3 sm:w-44 sm:flex-col sm:border-r sm:border-b-0">
              <div className="w-full space-y-3">
                {[{ title: "設定", items: tabs.slice(0, 3) }, { title: "驗證及測試", items: tabs.slice(3) }].map((group, index) => <div key={group.title} role="group" aria-label={group.title} className={index ? "border-t border-outline/20 pt-3" : ""}>
                  <p className="mb-1 px-3 text-xs font-semibold text-on-surface/50">{group.title}</p>
                  <div className="flex gap-1 overflow-x-auto sm:block sm:space-y-1">
                    {group.items.map(tab => <button key={tab.id} onClick={() => setActiveTab(tab.id)} aria-current={activeTab === tab.id ? "page" : undefined} className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm font-semibold sm:w-full ${activeTab === tab.id ? "bg-white text-primary shadow-sm" : "text-on-surface/65 hover:bg-white/70"}`}><MaterialIcon icon={tab.icon} size={18} />{tab.label}</button>)}
                  </div>
                </div>)}
              </div>
              <div className="mt-auto hidden whitespace-pre-line border-t border-outline/10 pt-3 text-xs leading-5 text-on-surface/50 sm:block">{activeTab === "skills" ? "共用 collection\nscenario_scripts" : activeTab === "behavior" ? "本機紀錄與人工覆核\n離開前請下載 JSON" : activeTab === "batch" ? "檔案在本機讀取\n逐筆預測並下載副本" : activeTab === "test" ? `僅本次請求\n基準：${config.environment_document_id ?? "local fallback"}` : `目前環境\nruntime_config/${config.environment_document_id ?? "local fallback"}`}</div>
            </nav>
            <div className="min-w-0 flex-1 overflow-y-auto p-6">
              {activeTab === "system" && <SystemSettings adminToken={verifiedToken} config={config} savedMaintenanceMessage={lastLoadedConfig?.maintenance_message} onChange={setConfig} />}
              {activeTab === "prompts" && <PromptSettings config={config} onChange={setConfig} />}
              {activeTab === "skills" && <SkillsSettings skills={skills} selectedSkill={selectedSkill} onSelect={setSelectedSkillId} onCreate={createSkill} onChange={updateSkill} />}
              {activeTab === "test" && <AdminChatTest key={`${lastLoadedConfig?.environment_document_id}-${lastLoadedConfig?.updated_at}`} adminToken={verifiedToken} config={lastLoadedConfig ?? config} />}
              <div hidden={activeTab !== "batch"}><AdminBatchPrediction adminToken={verifiedToken} onHasDataChange={setBatchHasData} /></div>
              <div hidden={activeTab !== "behavior"}><AdminBehaviorTests adminToken={verifiedToken} onHasDataChange={setBehaviorHasData} /></div>
            </div>
          </div>
        )}

          {isAuthenticated && activeTab !== "test" && activeTab !== "batch" && activeTab !== "behavior" && <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-outline/15 px-5 py-3">
          <p className={`text-xs ${error ? "text-error" : "text-on-surface/55"}`}>{error || status || (activeTab === "skills" ? "Skills 為 dev/main 共用設定" : "Runtime 設定只會寫入目前環境")}</p>
          {activeTab === "skills" ? <div className="flex flex-wrap gap-2"><button onClick={seedSkill} disabled={isSaving} className="rounded-lg border border-secondary/25 px-3 py-2 text-sm font-semibold text-secondary disabled:opacity-50">建立範例</button><button onClick={removeSkill} disabled={isSaving || !selectedSkill} className="rounded-lg border border-error/25 px-3 py-2 text-sm font-semibold text-error disabled:opacity-50">刪除</button><button onClick={saveSkill} disabled={isSaving || !selectedSkill} className="rounded-lg bg-primary px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{isSaving ? "儲存中" : "儲存 Skill"}</button></div> : <div className="flex flex-wrap gap-2"><button onClick={seedRuntime} disabled={isSaving} className="rounded-lg px-3 py-2 text-sm font-semibold text-on-surface hover:bg-surface-container disabled:opacity-50">補齊設定</button><button onClick={resetRuntime} disabled={isSaving} className="rounded-lg border border-error/25 px-3 py-2 text-sm font-semibold text-error disabled:opacity-50">重置設定</button><button onClick={saveRuntime} disabled={isSaving || !runtimeDirty} className="rounded-lg bg-primary px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{isSaving ? "儲存中" : runtimeDirty ? "儲存變更" : "已同步"}</button></div>}
        </footer>}
      </section>
    </>
  );
}

function SystemSettings({ config, savedMaintenanceMessage, onChange, adminToken }: { adminToken: string; config: RuntimeConfig; savedMaintenanceMessage?: string; onChange: (config: RuntimeConfig) => void }) {
  const set = (updates: Partial<RuntimeConfig>) => onChange({ ...config, ...updates });
  return <div className="mx-auto max-w-2xl space-y-7">
    <div><h3 className="text-xl font-bold">系統設定</h3><p className="mt-1 text-sm text-on-surface/60">模型、檢索與流程設定只作用於目前環境。</p></div>
    <section aria-label="模型設定" className="space-y-4">
      <h4 className="text-sm font-bold">模型設定</h4>
      <Field label="OpenRouter Model"><input value={config.openrouter_model} onChange={(event) => set({ openrouter_model: event.target.value })} className="input" /></Field><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Field label="Temperature"><input type="number" min="0" max="2" step="0.05" value={config.temperature} onChange={(event) => set({ temperature: Number(event.target.value) })} className="input" /></Field><Field label="Top P"><input type="number" min="0.01" max="1" step="0.05" value={config.top_p} onChange={(event) => set({ top_p: Number(event.target.value) })} className="input" /></Field><Field label="Max tokens"><input type="number" min="0" max="8192" value={config.max_tokens} onChange={(event) => set({ max_tokens: Number(event.target.value) })} className="input" /><span className="mt-1 block text-[11px] text-on-surface/50">0 表示不設定上限</span></Field><Field label="思考等級"><select value={config.reasoning_effort} onChange={(event) => set({ reasoning_effort: event.target.value as RuntimeConfig["reasoning_effort"] })} className="input"><option value="none">關閉</option><option value="minimal">最少</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="xhigh">很高</option><option value="max">最大</option></select><span className="mt-1 block text-[11px] text-on-surface/50">需使用支援 reasoning 的模型</span></Field></div>
      <RerankSettings config={config} onChange={set} adminToken={adminToken} />
    </section>
    <section aria-label="處理流程" className="border-t border-outline/10 pt-6">
      <PipelineSettingsEditor value={{ ...DEFAULT_PIPELINE, ...config.pipeline }} onChange={pipeline => set({ pipeline })} maskingEnabled={config.enable_anonymization}>
        <Toggle label="後端 PII 遮罩（第二層）" checked={config.enable_anonymization} onChange={checked => set({ enable_anonymization: checked })} />
        <Toggle label="送出前遮罩確認" checked={config.enable_client_privacy_review !== false} onChange={checked => set({ enable_client_privacy_review: checked })} />
        <p className="text-xs leading-relaxed text-on-surface/65">瀏覽器會自動遮罩文字；此開關決定送出前是否顯示確認視窗。後端遮罩另由上方開關控制。</p>
        <Toggle label="啟用 Reranking" checked={config.rag_rerank_enabled ?? false} onChange={checked => set({ rag_rerank_enabled: checked })} />
        <p className="text-xs text-on-surface/60">資料檢索開啟時，才會對檢索結果重排。</p>
        <Toggle label="允許圖片上傳" checked={config.enable_image_upload} onChange={checked => set({ enable_image_upload: checked })} />
        <p className="text-xs leading-relaxed text-on-surface/65">圖片以原圖傳送，不做自動去識別；每張最多 5 MB。</p>
        <Toggle label="Development mode" checked={config.development_mode} onChange={checked => set({ development_mode: checked })} />
      </PipelineSettingsEditor>
    </section>
    <section aria-label="檢索設定" className="space-y-4 border-t border-outline/10 pt-6">
      <h4 className="text-sm font-bold">檢索設定</h4>
      <Field label="RAG Retrieval Top K"><input type="number" min="1" max="20" value={config.rag_retrieval_top_k} onChange={(event) => set({ rag_retrieval_top_k: Number(event.target.value) })} className="input" /></Field><Field label="RAG Distance Threshold"><input type="number" min="0" max="2" step="0.01" value={config.rag_distance_threshold ?? ""} onChange={(event) => set({ rag_distance_threshold: event.target.value === "" ? null : Number(event.target.value) })} className="input" placeholder="停用" /><span className="mt-1 block text-[11px] text-on-surface/50">0–2；留空表示停用，數值越小代表越相似</span></Field>{(["law", "judgment", "remedy"] as const).map((key) => <Field key={key} label={key}><input value={config.rag_collections[key]} onChange={(event) => set({ rag_collections: { ...config.rag_collections, [key]: event.target.value } })} className="input" /></Field>)}
    </section>
    <section className="space-y-3 border-t border-outline/10 pt-6">
      <h4 className="text-sm font-bold">維護模式</h4>
      <p className={`rounded-lg px-3 py-3 text-sm font-semibold ${savedMaintenanceMessage?.trim() ? "bg-error/10 text-error" : "bg-surface-container-low text-on-surface/65"}`}>
        {savedMaintenanceMessage?.trim() ? "目前服務已暫停（維護模式）" : "目前維護模式已關閉"}
      </p>
      <Field label="維護訊息">
        <textarea value={config.maintenance_message ?? ""} onChange={(event) => set({ maintenance_message: event.target.value })} rows={3} className="input resize-y leading-6" placeholder="留空表示關閉維護模式" />
      </Field>
      <p className="text-xs leading-5 text-on-surface/60">填寫訊息並儲存後會暫停聊天，使用者將看到這段訊息。清空後按「儲存變更」即可恢復服務。</p>
      <button type="button" onClick={() => set({ maintenance_message: "" })} disabled={!config.maintenance_message} className="rounded-lg border border-outline/25 px-3 py-2 text-sm font-semibold text-on-surface hover:bg-surface-container disabled:opacity-50">清空維護訊息</button>
    </section>
  </div>;
}

function PromptSettings({ config, onChange }: { config: RuntimeConfig; onChange: (config: RuntimeConfig) => void }) {
  return <div className="mx-auto max-w-2xl space-y-6"><div><h3 className="text-xl font-bold">Prompt 設定</h3><p className="mt-1 text-sm text-on-surface/60">留空時使用程式內建 section；各環境分別儲存。</p></div>{PROMPT_SECTION_FIELDS.map(([key, label, rows]) => <Field key={key} label={label}><textarea value={config.agent_prompt_sections[key] ?? ""} rows={rows} onChange={(event) => onChange({ ...config, agent_prompt_sections: { ...config.agent_prompt_sections, [key]: event.target.value } })} className="input resize-y leading-6" placeholder="留空使用程式內建內容" /></Field>)}</div>;
}

function SkillsSettings({ skills, selectedSkill, onSelect, onCreate, onChange }: { skills: ScenarioSkill[]; selectedSkill: ScenarioSkill | null; onSelect: (id: string) => void; onCreate: () => void; onChange: (changes: Partial<ScenarioSkill>) => void }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 md:flex-row">
      <aside className="w-full shrink-0 border-b border-outline/10 pb-4 md:w-48 md:border-r md:border-b-0 md:pr-4 md:pb-0">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-bold">Skills</h3>
          <button onClick={onCreate} className="rounded-md p-1 text-primary hover:bg-primary-container" aria-label="新增 Skill"><MaterialIcon icon="add" size={18} /></button>
        </div>
        <p className="mb-3 text-xs leading-5 text-on-surface/55">共用於 dev 與 main。</p>
        <div className="flex gap-1 overflow-x-auto md:block md:space-y-1">
          {skills.map((skill) => (
            <button key={skill.id} onClick={() => onSelect(skill.id)} className={`w-40 shrink-0 rounded-lg px-3 py-2 text-left text-sm md:w-full ${selectedSkill?.id === skill.id ? "bg-primary-container text-primary" : "hover:bg-surface-container"}`}>
              <span className="block truncate font-semibold">{skill.name}</span>
              <span className="text-[11px] opacity-65">{skill.enabled ? "啟用" : "停用"} · {skill.priority}</span>
            </button>
          ))}
        </div>
      </aside>
      {selectedSkill ? (
        <div className="min-w-0 flex-1 space-y-5">
          <div>
            <h3 className="text-xl font-bold">{selectedSkill.name}</h3>
            <p className="mt-1 text-sm text-on-surface/60">觸發後會在同一次模型呼叫注入情境指令與可用按鈕。</p>
          </div>
          <Field label="Skill ID"><input value={selectedSkill.id} disabled className="input cursor-not-allowed opacity-55" /></Field>
          <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
            <Field label="名稱"><input value={selectedSkill.name} onChange={(event) => onChange({ name: event.target.value })} className="input" /></Field>
            <Field label="優先順序"><input type="number" value={selectedSkill.priority} onChange={(event) => onChange({ priority: Number(event.target.value) })} className="input" /></Field>
          </div>
          <Toggle label="啟用此 Skill" checked={selectedSkill.enabled} onChange={(enabled) => onChange({ enabled })} />
          <Field label="觸發詞（以逗號分隔）"><input value={selectedSkill.trigger_keywords.join(", ")} onChange={(event) => onChange({ trigger_keywords: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} className="input" placeholder="請輸入此情境的關鍵詞" /></Field>
          <Field label="情境指令"><textarea value={selectedSkill.instruction} onChange={(event) => onChange({ instruction: event.target.value })} rows={8} className="input resize-y leading-6" placeholder="說明何時使用下方按鈕、選擇哪些按鈕，以及如何回應使用者" /></Field>
          <SkillActionsEditor key={selectedSkill.id} actions={selectedSkill.actions} onChange={(actions) => onChange({ actions })} />
        </div>
      ) : <div className="flex flex-1 items-center justify-center text-sm text-on-surface/55">選擇或新增一個 Skill</div>}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="block"><span className="text-xs font-semibold text-on-surface/60">{label}</span>{children}</label>; }
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) { return <label className="flex items-center justify-between gap-4 rounded-lg border border-outline/10 bg-surface-container-low px-3 py-3 text-sm font-semibold"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-5 w-5 accent-primary" /></label>; }
