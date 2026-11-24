import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

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
import { configToUpdate, PROMPT_SECTION_FIELDS } from "../services/runtimeConfig";
import { MaterialIcon } from "@rag/shared/chat-ui";
const AdminRerankTests = lazy(() => import("./AdminRerankTests"));
const AdminChatTest = lazy(() => import("./AdminChatTest"));
import AdminBatchPrediction from "./AdminBatchPrediction";
const maskingLabEnabled = import.meta.env.MODE === "preview" || import.meta.env.DEV;
const MaskingValidation = maskingLabEnabled ? lazy(() => import("../masking-lab/ValidationPage")) : null;
import PromptSettings from "./PromptSettings";
import SkillsSettings from "./SkillsSettings";
import SystemSettings from "./SystemSettings";
import { DEFAULT_PIPELINE } from "@rag/shared/pipeline";

interface AdminPanelProps {
  session: AdminSession;
  onLogout: () => void;
  unlocked?: boolean;
}

type AdminTab = "system" | "prompts" | "skills" | "test" | "batch" | "masking" | "rerank" | "corpus";


const emptyPromptSections = Object.fromEntries(PROMPT_SECTION_FIELDS.map(([key]) => [key, ""]));
const emptyConfig: RuntimeConfig = {
  pipeline: DEFAULT_PIPELINE,
  openrouter_model: "",
  gate_enabled: false,
  gate_model: "",
  gate_prompt: "",
  gate_post_prompt: "",
  gate_timeout_seconds: 10,
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
  enable_image_upload: false,
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
  ...(maskingLabEnabled ? [{ id: "masking" as const, label: "本機遮罩驗證", icon: "fact_check" }] : []),
  { id: "rerank", label: "Rerank 測試", icon: "sort" },
  { id: "corpus", label: "檢索資料庫", icon: "database" },
];

export default function AdminPanel({ session, onLogout, unlocked = true }: AdminPanelProps) {
  const verifiedCsrf = session.csrf_token;
  const [activeTab, setActiveTab] = useState<AdminTab>(() => window.location.hash === "#rerank" ? "rerank" : maskingLabEnabled && window.location.hash === "#masking" ? "masking" : "system");
  const settingsScrollRef = useRef<HTMLDivElement>(null);
  const [rerankVisited, setRerankVisited] = useState(() => window.location.hash === "#rerank");
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
  const [maskingVisited, setMaskingVisited] = useState(() => maskingLabEnabled && window.location.hash === "#masking");

  useEffect(() => {
    if (activeTab === "prompts" && settingsScrollRef.current) {
      settingsScrollRef.current.scrollTop = 0;
    }
  }, [activeTab]);

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
    const threshold = config.gate_jev_threshold;
    if (threshold !== undefined && !(threshold >= 0.5 && threshold <= 1)) {
      setError("Jev 判定門檻必須介於 0.50 與 1.00。");
      return;
    }
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
              (activeTab === "test" || activeTab === "rerank") ? "max-sm:overflow-x-auto" : ""
            }`}
          >
            <div
              className={
                (activeTab === "test" || activeTab === "rerank")
                  ? "flex w-max gap-3 sm:block sm:w-full sm:space-y-3"
                  : "w-full space-y-3"
              }
            >
              {[
                { title: "設定", items: tabs.filter(t => ["system", "prompts", "skills"].includes(t.id)) },
                { title: "驗證及測試", items: tabs.filter(t => ["test", "batch", "masking", "rerank"].includes(t.id)) },
                { title: "資料管理", items: tabs.filter(t => t.id === "corpus") },
              ].map((group, index) => (
                <div
                  key={group.title}
                  role="group"
                  aria-label={group.title}
                  className={
                    index
                      ? (activeTab === "test" || activeTab === "rerank")
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
                      (activeTab === "test" || activeTab === "rerank") ? "flex-nowrap" : "flex-wrap"
                    }`}
                  >
                    {group.items.map((tab) => (
                      <button
                        key={tab.id}
                        onClick={() => {
                          setActiveTab(tab.id);
                          if (tab.id === "rerank") setRerankVisited(true);
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
            ref={settingsScrollRef}
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
                onOpenGatePrompts={() => setActiveTab("prompts")}
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
            {rerankVisited && lastLoadedConfig && (
              <div hidden={activeTab !== "rerank"}>
                <Suspense fallback={<p role="status">正在載入 Rerank 測試…</p>}>
                  <AdminRerankTests csrfToken={verifiedCsrf} config={lastLoadedConfig} active={unlocked && activeTab === "rerank"} />
                </Suspense>
              </div>
            )}
            {MaskingValidation && maskingVisited && (
              <div hidden={activeTab !== "masking"}>
                <Suspense fallback={<p role="status">正在載入遮罩驗證…</p>}>
                  <MaskingValidation key={lastLoadedConfig?.environment_document_id} active={unlocked && !!lastLoadedConfig} />
                </Suspense>
              </div>
            )}
          </div>
        </div>

        {!isLoading &&
          activeTab !== "corpus" &&
          activeTab !== "test" &&
          activeTab !== "batch" &&
          activeTab !== "masking" &&
          activeTab !== "rerank" && (
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
