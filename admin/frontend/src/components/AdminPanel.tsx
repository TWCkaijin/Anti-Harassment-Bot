import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";

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
  type ScenarioSkill,
  type ScenarioSkillInput,
} from "../services/api";
import type { AdminSession } from "../services/session";
import { invalidateReadCache } from "../services/readCache";
import AdminCorpus from "./AdminCorpus";
import RerankSettings from "./RerankSettings";
import { configToUpdate, PROMPT_SECTION_FIELDS } from "../services/runtimeConfig";
import MaterialIcon from "./MaterialIcon";
import SkillActionsEditor from "./SkillActionsEditor";
const AdminChatTest = lazy(() => import("./AdminChatTest"));
import AdminBatchPrediction from "./AdminBatchPrediction";
const MaskingValidation = lazy(() => import("../masking-lab/ValidationPage"));
import PipelineSettingsEditor from "./PipelineSettingsEditor";
import { DEFAULT_PIPELINE } from "../services/pipeline";

interface AdminPanelProps {
  session: AdminSession;
  onLogout: () => void;
  unlocked?: boolean;
}

type AdminTab = "system" | "prompts" | "skills" | "test" | "batch" | "masking" | "corpus";


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
  development_mode: false,
  enable_reasoning_display: false,
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


function skillToInput(skill: ScenarioSkill, revision?: number): ScenarioSkillInput & { catalog_revision?: number } {
  return {
    ...(revision === undefined ? {} : { catalog_revision: revision }),
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
  { id: "masking", label: "本機遮罩驗證", icon: "fact_check" },
  { id: "corpus", label: "檢索資料庫", icon: "database" },
];

export default function AdminPanel({ session, onLogout, unlocked = true }: AdminPanelProps) {
  const verifiedCsrf = session.csrf_token;
  const [activeTab, setActiveTab] = useState<AdminTab>(() => window.location.hash === "#masking" ? "masking" : "system");
  const [corpusVisited, setCorpusVisited] = useState(false);
  const [testVisited, setTestVisited] = useState(false);
  const [config, setConfig] = useState<RuntimeConfig>(emptyConfig);
  const [lastLoadedConfig, setLastLoadedConfig] = useState<RuntimeConfig | null>(null);
  const [skills, setSkills] = useState<ScenarioSkill[]>([]);
  const [skillsRevision, setSkillsRevision] = useState<number | undefined>();
  const [reloadVersion, setReloadVersion] = useState(0);
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [skillsLoaded, setSkillsLoaded] = useState(false);
  const [loadErrors, setLoadErrors] = useState<{ config?: string; skills?: string }>({});
  const [batchHasData, setBatchHasData] = useState(false);
  const [maskingVisited, setMaskingVisited] = useState(() => window.location.hash === "#masking");

  const selectedSkill = skills.find((skill) => skill.id === selectedSkillId) ?? null;
  const runtimeDirty = useMemo(() => {
    if (!lastLoadedConfig) return false;
    return JSON.stringify(configToUpdate(config)) !== JSON.stringify(configToUpdate(lastLoadedConfig));
  }, [config, lastLoadedConfig]);

  const applyConfig = (nextConfig: RuntimeConfig) => {
    const normalized = {
      ...nextConfig,
      pipeline: { ...DEFAULT_PIPELINE, ...nextConfig.pipeline },
      agent_prompt_sections: { ...emptyPromptSections, ...nextConfig.agent_prompt_sections },
    };
    setConfig(normalized);
    setLastLoadedConfig(normalized);
  };

  const handleError = (err: unknown, fallback: string) => {
    if (err instanceof ApiError) {
      if (err.status === 401) {
        setError("登入已到期，請重新驗證後繼續。本機測試結果仍保留。");
        return;
      }
      if (err.status === 409) {
        setError("設定已被其他操作更新。你的草稿仍保留；請先記下修改，再按「重新載入設定」取得最新版本。");
        return;
      }
      setError(err.debugMessage ?? err.detail ?? err.message);
      return;
    }
    setError(err instanceof Error ? err.message : fallback);
  };

  useEffect(() => {
    let active = true;
    const failed = (section: "config" | "skills", err: unknown) => {
      if (!active) return;
      const detail = err instanceof ApiError
        ? err.debugMessage ?? err.detail ?? err.message
        : err instanceof Error ? err.message : "服務暫時無法使用";
      setLoadErrors(current => ({ ...current, [section]: detail }));
    };
    void Promise.allSettled([
      getRuntimeConfig(verifiedCsrf).then(nextConfig => {
        if (!active) return;
        applyConfig(nextConfig);
      }, err => failed("config", err)),
      getScenarioSkills(verifiedCsrf).then(skillResult => {
        if (!active) return;
        setSkills(skillResult.skills);
        setSkillsRevision(skillResult.revision);
        setSelectedSkillId(skillResult.skills[0]?.id ?? null);
        setSkillsLoaded(true);
      }, err => failed("skills", err)),
    ]).then(() => {
      if (active) {
        setIsLoading(false);
        setStatus("儲存後 Client 最晚於快取期限內更新。");
      }
    });
    return () => { active = false; };
  }, [verifiedCsrf, reloadVersion]);

  const reloadSettings = () => {
    if (runtimeDirty && !window.confirm("重新載入會捨棄尚未儲存的設定草稿，確定繼續？")) return;
    setIsLoading(true);
    setError("");
    setLoadErrors({});
    invalidateReadCache("/config");
    invalidateReadCache("/scenario-scripts");
    setReloadVersion(value => value + 1);
  };

  const saveRuntime = async () => {
    if (!verifiedCsrf || !lastLoadedConfig || isLoading) return;
    setIsSaving(true);
    setError("");
    try {
      applyConfig(await updateRuntimeConfig(verifiedCsrf, configToUpdate(config)));
      setStatus("已儲存至目前環境的 Firestore runtime_config");
    } catch (err) {
      handleError(err, "儲存 Runtime 設定失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const seedRuntime = async () => {
    if (!verifiedCsrf || !lastLoadedConfig || isLoading) return;
    setIsSaving(true);
    try {
      applyConfig(await seedRuntimeConfig(verifiedCsrf, lastLoadedConfig?.revision));
      setStatus("已補齊目前環境的 Runtime 設定");
    } catch (err) {
      handleError(err, "補齊設定失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const resetRuntime = async () => {
    if (!verifiedCsrf || !lastLoadedConfig || isLoading) return;
    setIsSaving(true);
    try {
      applyConfig(await resetRuntimeConfig(verifiedCsrf, lastLoadedConfig?.revision));
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
    if (!verifiedCsrf || !selectedSkill || !skillsLoaded || isLoading) return;
    setIsSaving(true);
    try {
      await updateScenarioSkill(verifiedCsrf, selectedSkill.id, skillToInput(selectedSkill, skillsRevision));
      const latest = await getScenarioSkills(verifiedCsrf);
      setSkills(latest.skills);
      setSkillsRevision(latest.revision);
      setStatus("已儲存目前環境的 Skills");
    } catch (err) {
      handleError(err, "儲存 Skill 失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const removeSkill = async () => {
    if (!verifiedCsrf || !selectedSkill || !skillsLoaded || isLoading) return;
    if (!window.confirm(`刪除「${selectedSkill.name}」？內建 Skill 會改為停用。`)) return;
    setIsSaving(true);
    try {
      await deleteScenarioSkill(verifiedCsrf, selectedSkill.id, skillsRevision);
      const result = await getScenarioSkills(verifiedCsrf);
      setSkillsRevision(result.revision);
      const retainedSkill = result.skills.find((skill) => skill.id === selectedSkill.id);
      setSkills((current) => retainedSkill
        ? current.map((skill) => skill.id === selectedSkill.id ? retainedSkill : skill)
        : current.filter((skill) => skill.id !== selectedSkill.id));
      setSelectedSkillId(retainedSkill?.id ?? null);
      setStatus(retainedSkill ? "已停用內建 Skill，可重新啟用" : "已刪除 Skill");
    } catch (err) {
      handleError(err, "刪除 Skill 失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const seedSkill = async () => {
    if (!verifiedCsrf || !skillsLoaded || isLoading) return;
    setIsSaving(true);
    try {
      await seedScenarioScripts(verifiedCsrf, skillsRevision);
      const result = await getScenarioSkills(verifiedCsrf);
      setSkills(result.skills);
      setSkillsRevision(result.revision);
      setSelectedSkillId("call_support");
      setStatus("已建立目前環境範例 Skills");
    } catch (err) {
      handleError(err, "建立範例 Skill 失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const close = () => {
    if ((batchHasData || runtimeDirty) && !window.confirm("登出會停止測試並清除此頁的資料。請先下載結果並儲存設定；確定登出？")) return;
    onLogout();
  };

  return (
    <>
      <section className="flex h-dvh w-full flex-col bg-white">
        <header className="flex items-center justify-between border-b border-outline/15 px-5 py-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-lg font-bold text-on-surface">
              <MaterialIcon icon="admin_panel_settings" size={21} />
              管理後台
            </h2>
            <p className="mt-1 text-xs text-on-surface/50">
              {session.environment} · {config.environment_document_id ?? "設定載入中"} ·{" "}
              {config.updated_at ?? "已登入"}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button className="text-sm text-primary underline" onClick={reloadSettings}>
              重新載入設定
            </button>
            <button
              onClick={close}
              className="rounded-lg p-2 hover:bg-surface-container"
              aria-label="登出"
            >
              登出
            </button>
          </div>
        </header>

        {isLoading && (
          <p className="px-6 py-2 text-sm" role="status">
            載入環境設定…
          </p>
        )}
        {Object.entries(loadErrors).map(([section, detail]) => (
          <p key={section} role="alert" className="px-6 py-2 text-sm text-error">
            {section === "config" ? "Runtime／Prompt" : "Skills"} 載入失敗：{detail}
            。請按「重新載入設定」重試；已載入的資料與草稿仍保留。
          </p>
        ))}

        <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          <nav
            className={`flex w-full shrink-0 flex-col border-b border-outline/15 bg-surface-container-low p-3 sm:w-44 sm:border-r sm:border-b-0 ${
              activeTab === "test" ? "max-sm:overflow-x-auto" : ""
            }`}
          >
            <div
              className={
                activeTab === "test"
                  ? "flex w-max gap-3 sm:block sm:w-full sm:space-y-3"
                  : "w-full space-y-3"
              }
            >
              {[
                { title: "設定", items: tabs.slice(0, 3) },
                { title: "驗證及測試", items: tabs.slice(3, 6) },
                { title: "資料管理", items: tabs.slice(6) },
              ].map((group, index) => (
                <div
                  key={group.title}
                  role="group"
                  aria-label={group.title}
                  className={
                    index
                      ? activeTab === "test"
                        ? "border-l border-outline/20 pl-3 sm:border-l-0 sm:border-t sm:pl-0 sm:pt-3"
                        : "border-t border-outline/20 pt-3"
                      : ""
                  }
                >
                  <p className="mb-1 px-3 text-xs font-semibold text-on-surface/50">
                    {group.title}
                  </p>
                  <div
                    className={`flex gap-1 sm:block sm:space-y-1 ${
                      activeTab === "test" ? "flex-nowrap" : "flex-wrap"
                    }`}
                  >
                    {group.items.map((tab) => (
                      <button
                        key={tab.id}
                        onClick={() => {
                          setActiveTab(tab.id);
                          if (tab.id === "test") setTestVisited(true);
                          if (tab.id === "masking") setMaskingVisited(true);
                          if (tab.id === "corpus") setCorpusVisited(true);
                        }}
                        aria-current={activeTab === tab.id ? "page" : undefined}
                        className={`flex shrink-0 items-center gap-2 rounded-lg px-2 py-2 text-left text-xs font-semibold sm:w-full sm:px-3 sm:py-2.5 sm:text-sm ${
                          activeTab === tab.id
                            ? "bg-white text-primary shadow-sm"
                            : "text-on-surface/65 hover:bg-white/70"
                        }`}
                      >
                        <MaterialIcon icon={tab.icon} size={18} />
                        {tab.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-auto hidden whitespace-pre-line border-t border-outline/10 pt-3 text-xs leading-5 text-on-surface/50 sm:block">
              {activeTab === "skills"
                ? "目前環境 Skills"
                : activeTab === "masking"
                  ? "本機合成案例驗證\n手動載入模型"
                  : activeTab === "batch"
                    ? "檔案在本機讀取\n多輪測試並下載副本"
                    : activeTab === "test"
                      ? `僅本次請求\n基準：${config.environment_document_id ?? "local fallback"}`
                      : `目前環境\nruntime_config/${config.environment_document_id ?? "local fallback"}`}
            </div>
          </nav>

          <div
            className={`min-w-0 min-h-0 flex-1 ${
              activeTab === "test" ? "overflow-hidden" : "overflow-y-auto p-6"
            }`}
          >
            {((["system", "prompts", "test"].includes(activeTab) && !lastLoadedConfig) ||
              (activeTab === "skills" && !skillsLoaded)) && (
              <p className="text-sm text-on-surface/60">
                {isLoading ? "正在讀取目前環境資料…" : "尚未取得目前環境設定，請重新載入。"}
              </p>
            )}
            {corpusVisited && (
              <div hidden={activeTab !== "corpus"}>
                <AdminCorpus csrfToken={verifiedCsrf} active={activeTab === "corpus"} />
              </div>
            )}
            {activeTab === "system" && lastLoadedConfig && (
              <SystemSettings
                csrfToken={verifiedCsrf}
                config={config}
                savedMaintenanceMessage={lastLoadedConfig?.maintenance_message}
                onChange={setConfig}
              />
            )}
            {activeTab === "prompts" && lastLoadedConfig && (
              <PromptSettings config={config} onChange={setConfig} />
            )}
            {activeTab === "skills" && skillsLoaded && (
              <SkillsSettings
                skills={skills}
                selectedSkill={selectedSkill}
                onSelect={setSelectedSkillId}
                onCreate={createSkill}
                onChange={updateSkill}
              />
            )}
            {testVisited && lastLoadedConfig && (
              <div hidden={activeTab !== "test"} className="h-full">
                <Suspense
                  fallback={
                    <p role="status" className="p-6 text-sm">
                      正在載入診斷對話…
                    </p>
                  }
                >
                  <AdminChatTest
                    csrfToken={verifiedCsrf}
                    config={lastLoadedConfig}
                    visible={activeTab === "test" && unlocked}
                  />
                </Suspense>
              </div>
            )}
            <div hidden={activeTab !== "batch"}>
              <AdminBatchPrediction csrfToken={verifiedCsrf} onHasDataChange={setBatchHasData} />
            </div>
            {maskingVisited && (
              <div hidden={activeTab !== "masking"}>
                <Suspense fallback={<p role="status">正在載入遮罩驗證…</p>}>
                  <MaskingValidation active={unlocked} />
                </Suspense>
              </div>
            )}
          </div>
        </div>

        {!isLoading &&
          activeTab !== "corpus" &&
          activeTab !== "test" &&
          activeTab !== "batch" &&
          activeTab !== "masking" && (
            <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-outline/15 px-5 py-3">
              <p className={`text-xs ${error ? "text-error" : "text-on-surface/55"}`}>
                {error ||
                  status ||
                  (activeTab === "skills"
                    ? "Skills 只作用於目前環境"
                    : "Runtime 設定只會寫入目前環境")}
              </p>
              {activeTab === "skills" ? (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={seedSkill}
                    disabled={isSaving || !skillsLoaded}
                    className="rounded-lg border border-secondary/25 px-3 py-2 text-sm font-semibold text-secondary disabled:opacity-50"
                  >
                    建立範例
                  </button>
                  <button
                    onClick={removeSkill}
                    disabled={isSaving || !skillsLoaded || !selectedSkill}
                    className="rounded-lg border border-error/25 px-3 py-2 text-sm font-semibold text-error disabled:opacity-50"
                  >
                    刪除
                  </button>
                  <button
                    onClick={saveSkill}
                    disabled={isSaving || !skillsLoaded || !selectedSkill}
                    className="rounded-lg bg-primary px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
                  >
                    {isSaving ? "儲存中" : "儲存 Skill"}
                  </button>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={seedRuntime}
                    disabled={isSaving || !lastLoadedConfig}
                    className="rounded-lg px-3 py-2 text-sm font-semibold text-on-surface hover:bg-surface-container disabled:opacity-50"
                  >
                    補齊設定
                  </button>
                  <button
                    onClick={resetRuntime}
                    disabled={isSaving || !lastLoadedConfig}
                    className="rounded-lg border border-error/25 px-3 py-2 text-sm font-semibold text-error disabled:opacity-50"
                  >
                    重置設定
                  </button>
                  <button
                    onClick={saveRuntime}
                    disabled={isSaving || !lastLoadedConfig || !runtimeDirty}
                    className="rounded-lg bg-primary px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
                  >
                    {isSaving ? "儲存中" : runtimeDirty ? "儲存變更" : "已同步"}
                  </button>
                </div>
              )}
            </footer>
          )}
      </section>
    </>
  );
}

function SystemSettings({
  config,
  savedMaintenanceMessage,
  onChange,
  csrfToken,
}: {
  csrfToken: string;
  config: RuntimeConfig;
  savedMaintenanceMessage?: string;
  onChange: (config: RuntimeConfig) => void;
}) {
  const set = (updates: Partial<RuntimeConfig>) => onChange({ ...config, ...updates });
  return (
    <div className="mx-auto max-w-2xl space-y-7">
      <div>
        <h3 className="text-xl font-bold">系統設定</h3>
        <p className="mt-1 text-sm text-on-surface/60">模型、檢索與流程設定只作用於目前環境。</p>
      </div>
      <section aria-label="模型設定" className="space-y-4">
        <h4 className="text-sm font-bold">模型設定</h4>
        <Field label="OpenRouter Model">
          <input
            value={config.openrouter_model}
            onChange={(event) => set({ openrouter_model: event.target.value })}
            className="input"
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Temperature">
            <input
              type="number"
              min="0"
              max="2"
              step="0.05"
              value={config.temperature}
              onChange={(event) => set({ temperature: Number(event.target.value) })}
              className="input"
            />
          </Field>
          <Field label="Top P">
            <input
              type="number"
              min="0.01"
              max="1"
              step="0.05"
              value={config.top_p}
              onChange={(event) => set({ top_p: Number(event.target.value) })}
              className="input"
            />
          </Field>
          <Field label="Max tokens">
            <input
              type="number"
              min="0"
              max="8192"
              value={config.max_tokens}
              onChange={(event) => set({ max_tokens: Number(event.target.value) })}
              className="input"
            />
            <span className="mt-1 block text-[11px] text-on-surface/50">0 表示不設定上限</span>
          </Field>
          <Field label="思考等級">
            <select
              value={config.reasoning_effort}
              onChange={(event) =>
                set({ reasoning_effort: event.target.value as RuntimeConfig["reasoning_effort"] })
              }
              className="input"
            >
              <option value="none">關閉</option>
              <option value="minimal">最少</option>
              <option value="low">低</option>
              <option value="medium">中</option>
              <option value="high">高</option>
              <option value="xhigh">很高</option>
              <option value="max">最大</option>
            </select>
            <span className="mt-1 block text-[11px] text-on-surface/50">
              需使用支援 reasoning 的模型
            </span>
          </Field>
        </div>
        <RerankSettings config={config} onChange={set} csrfToken={csrfToken} />
      </section>

      <section aria-label="處理流程" className="border-t border-outline/10 pt-6">
        <PipelineSettingsEditor
          value={{ ...DEFAULT_PIPELINE, ...config.pipeline }}
          onChange={(pipeline) => set({ pipeline })}
          maskingEnabled={config.enable_anonymization}
        >
          <Toggle
            label="後端 PII 遮罩（第二層）"
            checked={config.enable_anonymization}
            onChange={(checked) => set({ enable_anonymization: checked })}
          />
          <p className="text-xs leading-relaxed text-on-surface/65">
            瀏覽器會自動遮罩文字後送出。後端遮罩另由上方開關控制。
          </p>
          <Toggle
            label="啟用 Reranking"
            checked={config.rag_rerank_enabled ?? false}
            onChange={(checked) => set({ rag_rerank_enabled: checked })}
          />
          <p className="text-xs text-on-surface/60">資料檢索開啟時，才會對檢索結果重排。</p>
          <Toggle
            label="允許圖片上傳"
            checked={config.enable_image_upload}
            onChange={(checked) => set({ enable_image_upload: checked })}
          />
          <p className="text-xs leading-relaxed text-on-surface/65">
            圖片以原圖傳送，不做自動去識別；每張最多 5 MB。
          </p>
        </PipelineSettingsEditor>
      </section>

      <section aria-label="開發設定" className="space-y-3 border-t border-outline/10 pt-6">
        <h4 className="text-sm font-bold">開發設定</h4>
        <Toggle
          label="Development mode"
          checked={config.development_mode}
          onChange={(checked) => set({ development_mode: checked })}
        />
        <Toggle
          label="顯示模型 Thinking 過程"
          checked={config.enable_reasoning_display ?? false}
          onChange={(checked) => set({ enable_reasoning_display: checked })}
        />
        <p className="text-xs leading-relaxed text-on-surface/65">
          預設關閉。開啟後，聊天介面可顯示模型傳回的推理文字，可能包含內部提示；需同時啟用分析摘要並使用支援 reasoning 的模型。此開關不影響模型的思考等級。
        </p>
      </section>

      <section aria-label="檢索設定" className="space-y-4 border-t border-outline/10 pt-6">
        <h4 className="text-sm font-bold">檢索設定</h4>
        <Field label="RAG Retrieval Top K">
          <input
            type="number"
            min="1"
            max="20"
            value={config.rag_retrieval_top_k}
            onChange={(event) => set({ rag_retrieval_top_k: Number(event.target.value) })}
            className="input"
          />
        </Field>
        <Field label="RAG Distance Threshold">
          <input
            type="number"
            min="0"
            max="2"
            step="0.01"
            value={config.rag_distance_threshold ?? ""}
            onChange={(event) =>
              set({
                rag_distance_threshold:
                  event.target.value === "" ? null : Number(event.target.value),
              })
            }
            className="input"
            placeholder="停用"
          />
          <span className="mt-1 block text-[11px] text-on-surface/50">
            0–2；留空表示停用，數值越小代表越相似
          </span>
        </Field>
        {(["law", "judgment", "remedy"] as const).map((key) => (
          <Field key={key} label={key}>
            <input
              value={config.rag_collections[key]}
              onChange={(event) =>
                set({ rag_collections: { ...config.rag_collections, [key]: event.target.value } })
              }
              className="input"
            />
          </Field>
        ))}
      </section>

      <section className="space-y-3 border-t border-outline/10 pt-6">
        <h4 className="text-sm font-bold">維護模式</h4>
        <p
          className={`rounded-lg px-3 py-3 text-sm font-semibold ${
            savedMaintenanceMessage?.trim()
              ? "bg-error/10 text-error"
              : "bg-surface-container-low text-on-surface/65"
          }`}
        >
          {savedMaintenanceMessage?.trim() ? "目前服務已暫停（維護模式）" : "目前維護模式已關閉"}
        </p>
        <Field label="維護訊息">
          <textarea
            value={config.maintenance_message ?? ""}
            onChange={(event) => set({ maintenance_message: event.target.value })}
            rows={3}
            className="input resize-y leading-6"
            placeholder="留空表示關閉維護模式"
          />
        </Field>
        <p className="text-xs leading-5 text-on-surface/60">
          填寫訊息並儲存後會暫停聊天，使用者將看到這段訊息。清空後按「儲存變更」即可恢復服務。
        </p>
        <button
          type="button"
          onClick={() => set({ maintenance_message: "" })}
          disabled={!config.maintenance_message}
          className="rounded-lg border border-outline/25 px-3 py-2 text-sm font-semibold text-on-surface hover:bg-surface-container disabled:opacity-50"
        >
          清空維護訊息
        </button>
      </section>
    </div>
  );
}

function PromptSettings({
  config,
  onChange,
}: {
  config: RuntimeConfig;
  onChange: (config: RuntimeConfig) => void;
}) {
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h3 className="text-xl font-bold">Prompt 設定</h3>
        <p className="mt-1 text-sm text-on-surface/60">
          留空時使用程式內建 section；各環境分別儲存。
        </p>
      </div>
      {PROMPT_SECTION_FIELDS.map(([key, label, rows]) => (
        <Field key={key} label={label}>
          <textarea
            value={config.agent_prompt_sections[key] ?? ""}
            rows={rows}
            onChange={(event) =>
              onChange({
                ...config,
                agent_prompt_sections: {
                  ...config.agent_prompt_sections,
                  [key]: event.target.value,
                },
              })
            }
            className="input resize-y leading-6"
            placeholder="留空使用程式內建內容"
          />
        </Field>
      ))}
    </div>
  );
}

function SkillsSettings({
  skills,
  selectedSkill,
  onSelect,
  onCreate,
  onChange,
}: {
  skills: ScenarioSkill[];
  selectedSkill: ScenarioSkill | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onChange: (changes: Partial<ScenarioSkill>) => void;
}) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 md:flex-row">
      <aside className="w-full shrink-0 border-b border-outline/10 pb-4 md:w-48 md:border-r md:border-b-0 md:pr-4 md:pb-0">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-bold">Skills</h3>
          <button
            onClick={onCreate}
            className="rounded-md p-1 text-primary hover:bg-primary-container"
            aria-label="新增 Skill"
          >
            <MaterialIcon icon="add" size={18} />
          </button>
        </div>
        <p className="mb-3 text-xs leading-5 text-on-surface/55">只作用於目前環境。</p>
        <div className="flex gap-1 overflow-x-auto md:block md:space-y-1">
          {skills.map((skill) => (
            <button
              key={skill.id}
              onClick={() => onSelect(skill.id)}
              className={`w-40 shrink-0 rounded-lg px-3 py-2 text-left text-sm md:w-full ${
                selectedSkill?.id === skill.id
                  ? "bg-primary-container text-primary"
                  : "hover:bg-surface-container"
              }`}
            >
              <span className="block truncate font-semibold">{skill.name}</span>
              <span className="text-[11px] opacity-65">
                {skill.enabled ? "啟用" : "停用"} · {skill.priority}
              </span>
            </button>
          ))}
        </div>
      </aside>
      {selectedSkill ? (
        <div className="min-w-0 flex-1 space-y-5">
          <div>
            <h3 className="text-xl font-bold">{selectedSkill.name}</h3>
            <p className="mt-1 text-sm text-on-surface/60">
              觸發後會在同一次模型呼叫注入情境指令與可用按鈕。
            </p>
          </div>
          <Field label="Skill ID">
            <input value={selectedSkill.id} disabled className="input cursor-not-allowed opacity-55" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
            <Field label="名稱">
              <input
                value={selectedSkill.name}
                onChange={(event) => onChange({ name: event.target.value })}
                className="input"
              />
            </Field>
            <Field label="優先順序">
              <input
                type="number"
                value={selectedSkill.priority}
                onChange={(event) => onChange({ priority: Number(event.target.value) })}
                className="input"
              />
            </Field>
          </div>
          <Toggle
            label="啟用此 Skill"
            checked={selectedSkill.enabled}
            onChange={(enabled) => onChange({ enabled })}
          />
          <Field label="觸發詞（以逗號分隔）">
            <input
              value={selectedSkill.trigger_keywords.join(", ")}
              onChange={(event) =>
                onChange({
                  trigger_keywords: event.target.value
                    .split(",")
                    .map((value) => value.trim())
                    .filter(Boolean),
                })
              }
              className="input"
              placeholder="請輸入此情境的關鍵詞"
            />
          </Field>
          <Field label="情境指令">
            <textarea
              value={selectedSkill.instruction}
              onChange={(event) => onChange({ instruction: event.target.value })}
              rows={8}
              className="input resize-y leading-6"
              placeholder="說明何時使用下方按鈕、選擇哪些按鈕，以及如何回應使用者"
            />
          </Field>
          <SkillActionsEditor
            key={selectedSkill.id}
            actions={selectedSkill.actions}
            onChange={(actions) => onChange({ actions })}
          />
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-on-surface/55">
          選擇或新增一個 Skill
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-on-surface/60">{label}</span>
      {children}
    </label>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-4 rounded-lg border border-outline/10 bg-surface-container-low px-3 py-3 text-sm font-semibold">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-5 w-5 accent-primary"
      />
    </label>
  );
}
