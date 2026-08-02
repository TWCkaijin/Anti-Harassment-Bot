import { useMemo, useState } from "react";
import {
  ApiError,
  getRuntimeConfig,
  resetRuntimeConfig,
  seedRuntimeConfig,
  updateRuntimeConfig,
  type RuntimeConfig,
  type RuntimeConfigUpdate,
} from "../services/api";
import MaterialIcon from "./MaterialIcon";

interface AdminPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

const TOKEN_STORAGE_KEY = "harassment_bot_admin_token";
const PROMPT_SECTION_FIELDS = [
  ["core_mission", "你的核心使命"],
  ["communication_principles", "溝通原則"],
  ["important_resources", "重要通報資源"],
  ["limitations", "限制說明"],
  ["language", "語言"],
  ["output_format", "強制輸出格式"],
  ["analysis_rules", "分析規則"],
] as const;

const emptyPromptSections = Object.fromEntries(
  PROMPT_SECTION_FIELDS.map(([key]) => [key, ""])
);

const emptyConfig: RuntimeConfig = {
  openrouter_model: "",
  rag_retrieval_top_k: 3,
  enable_anonymization: true,
  temperature: 0.2,
  top_p: 1,
  max_tokens: 1200,
  agent_prompt_sections: emptyPromptSections,
  rag_collections: {
    law: "rag_documents",
    judgment: "rag_judgments",
    remedy: "rag_remedies",
  },
  maintenance_message: "",
  enable_image_upload: true,
  development_mode: false,
  source: "local",
};

function configToUpdate(config: RuntimeConfig): RuntimeConfigUpdate {
  return {
    openrouter_model: config.openrouter_model,
    rag_retrieval_top_k: config.rag_retrieval_top_k,
    enable_anonymization: config.enable_anonymization,
    temperature: config.temperature,
    top_p: config.top_p,
    max_tokens: config.max_tokens,
    agent_prompt_sections: config.agent_prompt_sections,
    rag_collections: config.rag_collections,
    maintenance_message: config.maintenance_message ?? "",
    enable_image_upload: config.enable_image_upload,
    development_mode: config.development_mode,
  };
}

export default function AdminPanel({ isOpen, onClose }: AdminPanelProps) {
  const [adminToken, setAdminToken] = useState(
    () => localStorage.getItem(TOKEN_STORAGE_KEY) ?? ""
  );
  const [verifiedToken, setVerifiedToken] = useState("");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [config, setConfig] = useState<RuntimeConfig>(emptyConfig);
  const [lastLoadedConfig, setLastLoadedConfig] = useState<RuntimeConfig | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const applyLoadedConfig = (nextConfig: RuntimeConfig) => {
    const normalizedConfig = {
      ...nextConfig,
      agent_prompt_sections: {
        ...emptyPromptSections,
        ...nextConfig.agent_prompt_sections,
      },
    };
    setConfig(normalizedConfig);
    setLastLoadedConfig(normalizedConfig);
  };

  const isDirty = useMemo(() => {
    if (!lastLoadedConfig) return false;
    return JSON.stringify(configToUpdate(config)) !== JSON.stringify(configToUpdate(lastLoadedConfig));
  }, [config, lastLoadedConfig]);

  const handleRequestError = (err: unknown, fallbackMessage: string) => {
    if (err instanceof ApiError && err.status === 401) {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
      setVerifiedToken("");
      setIsAuthenticated(false);
      setLastLoadedConfig(null);
      setError("Token 無效或已失效，請重新驗證");
      return;
    }
    setError(err instanceof Error ? err.message : fallbackMessage);
  };

  const handleTokenChange = (value: string) => {
    setAdminToken(value);
    if (isAuthenticated && value.trim() !== verifiedToken) {
      setVerifiedToken("");
      setIsAuthenticated(false);
      setLastLoadedConfig(null);
      setStatus("");
      setError("Token 已變更，請重新驗證");
    }
  };

  const verifyToken = async () => {
    if (!adminToken.trim()) {
      setError("請先輸入 Admin Token");
      return;
    }
    setIsLoading(true);
    setError("");
    setStatus("");
    try {
      const token = adminToken.trim();
      const nextConfig = await getRuntimeConfig(token);
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
      setVerifiedToken(token);
      setIsAuthenticated(true);
      applyLoadedConfig(nextConfig);
      setStatus("Token 驗證成功，已載入目前 runtime config");
    } catch (err) {
      setIsAuthenticated(false);
      setVerifiedToken("");
      handleRequestError(err, "Token 驗證失敗");
    } finally {
      setIsLoading(false);
    }
  };

  const saveConfig = async () => {
    if (!isAuthenticated || !verifiedToken) {
      setError("請先完成 Admin Token 驗證");
      return;
    }
    setIsSaving(true);
    setError("");
    setStatus("");
    try {
      const nextConfig = await updateRuntimeConfig(verifiedToken, configToUpdate(config));
      applyLoadedConfig(nextConfig);
      setStatus("已儲存至此環境的 Firestore runtime config");
    } catch (err) {
      handleRequestError(err, "儲存失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const seedConfig = async () => {
    if (!isAuthenticated || !verifiedToken) {
      setError("請先完成 Admin Token 驗證");
      return;
    }
    setIsSaving(true);
    setError("");
    setStatus("");
    try {
      const nextConfig = await seedRuntimeConfig(verifiedToken);
      applyLoadedConfig(nextConfig);
      setStatus("已補齊此環境 Firestore runtime config 的完整設定");
    } catch (err) {
      handleRequestError(err, "初始化失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const resetConfig = async () => {
    if (!isAuthenticated || !verifiedToken) {
      setError("請先完成 Admin Token 驗證");
      return;
    }
    setIsSaving(true);
    setError("");
    setStatus("");
    try {
      const nextConfig = await resetRuntimeConfig(verifiedToken);
      applyLoadedConfig(nextConfig);
      setStatus("已重置為程式內建的預設設定");
    } catch (err) {
      handleRequestError(err, "重置失敗");
    } finally {
      setIsSaving(false);
    }
  };

  const updateCollection = (key: keyof RuntimeConfig["rag_collections"], value: string) => {
    setConfig((current) => ({
      ...current,
      rag_collections: {
        ...current.rag_collections,
        [key]: value,
      },
    }));
  };

  const handleClose = () => {
    setVerifiedToken("");
    setIsAuthenticated(false);
    setLastLoadedConfig(null);
    setStatus("");
    setError("");
    onClose();
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 bg-black/30 z-[60]" onClick={handleClose} />
      <div className="fixed inset-y-0 right-0 z-[70] w-[520px] max-w-full bg-white border-l border-outline/20 flex flex-col animate-fade-in-up">
        <div className="flex items-center justify-between p-4 border-b border-outline-variant/20">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-on-surface flex items-center gap-2">
              <MaterialIcon icon="admin_panel_settings" size={21} />
              Runtime Admin
            </h2>
            <p className="text-xs text-on-surface/50 mt-1 truncate">
              {isAuthenticated
                ? `${config.source} · ${config.updated_at ? `更新於 ${config.updated_at}` : "已驗證"}`
                : "需要伺服器驗證"}
            </p>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-lg hover:bg-surface-container-high transition-colors cursor-pointer"
            aria-label="關閉"
          >
            <MaterialIcon icon="close" size={20} className="text-on-surface-variant" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {!isAuthenticated ? (
            <section className="space-y-4 rounded-xl border border-outline/20 bg-surface-container-low p-4">
              <div>
                <h3 className="text-base font-semibold text-on-surface flex items-center gap-2">
                  <MaterialIcon icon="lock" size={19} />
                  Admin 驗證
                </h3>
                <p className="mt-2 text-sm leading-6 text-on-surface/60">
                  請輸入 Admin Token，伺服器驗證成功後才能查看或修改 runtime 設定。
                </p>
              </div>
              <label className="block">
                <span className="text-xs font-medium text-on-surface/60">Admin Token</span>
                <input
                  value={adminToken}
                  onChange={(event) => handleTokenChange(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !isLoading) verifyToken();
                  }}
                  type="password"
                  autoComplete="current-password"
                  className="mt-1 w-full px-3 py-2.5 rounded-xl border border-outline/30 bg-white text-sm outline-none focus:border-primary"
                  placeholder="輸入 ADMIN_API_KEY"
                />
              </label>
              <button
                onClick={verifyToken}
                disabled={isLoading || !adminToken.trim()}
                className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-on-primary text-sm font-semibold disabled:opacity-50 cursor-pointer"
              >
                <MaterialIcon icon="verified_user" size={18} />
                {isLoading ? "驗證中" : "驗證並進入"}
              </button>
              {(status || error) && (
                <div
                  className={`rounded-xl px-3 py-2 text-sm ${
                    error
                      ? "bg-error-container/50 text-error"
                      : "bg-primary-container text-on-surface"
                  }`}
                >
                  {error || status}
                </div>
              )}
            </section>
          ) : (
          <>
          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-on-surface flex items-center gap-2">
              <MaterialIcon icon="tune" size={18} />
              Model 與檢索
            </h3>
            <label className="block">
              <span className="text-xs font-medium text-on-surface/60">OpenRouter Model</span>
              <input
                value={config.openrouter_model}
                onChange={(event) =>
                  setConfig((current) => ({ ...current, openrouter_model: event.target.value }))
                }
                className="mt-1 w-full px-3 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm outline-none focus:border-primary"
              />
            </label>
            <div className="grid grid-cols-3 gap-2">
              <label className="block">
                <span className="text-xs font-medium text-on-surface/60">Temperature</span>
                <input
                  value={config.temperature}
                  onChange={(event) =>
                    setConfig((current) => ({
                      ...current,
                      temperature: Number(event.target.value),
                    }))
                  }
                  min={0}
                  max={2}
                  step={0.05}
                  type="number"
                  className="mt-1 w-full px-2.5 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm outline-none focus:border-primary"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-on-surface/60">Top P</span>
                <input
                  value={config.top_p}
                  onChange={(event) =>
                    setConfig((current) => ({
                      ...current,
                      top_p: Number(event.target.value),
                    }))
                  }
                  min={0.01}
                  max={1}
                  step={0.05}
                  type="number"
                  className="mt-1 w-full px-2.5 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm outline-none focus:border-primary"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-on-surface/60">Max tokens</span>
                <input
                  value={config.max_tokens}
                  onChange={(event) =>
                    setConfig((current) => ({
                      ...current,
                      max_tokens: Number(event.target.value),
                    }))
                  }
                  min={0}
                  max={8192}
                  step={64}
                  type="number"
                  className="mt-1 w-full px-2.5 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm outline-none focus:border-primary"
                />
                <span className="mt-1 block text-[11px] text-on-surface/50">
                  0 表示不設定回覆 token 上限
                </span>
              </label>
            </div>
            <label className="block">
              <span className="text-xs font-medium text-on-surface/60">
                RAG Retrieval Top K
              </span>
              <input
                value={config.rag_retrieval_top_k}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    rag_retrieval_top_k: Number(event.target.value),
                  }))
                }
                min={1}
                max={20}
                type="number"
                className="mt-1 w-full px-3 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm outline-none focus:border-primary"
              />
            </label>
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-on-surface flex items-center gap-2">
              <MaterialIcon icon="database" size={18} />
              Firestore Collections
            </h3>
            {(["law", "judgment", "remedy"] as const).map((key) => (
              <label key={key} className="block">
                <span className="text-xs font-medium text-on-surface/60">{key}</span>
                <input
                  value={config.rag_collections[key]}
                  onChange={(event) => updateCollection(key, event.target.value)}
                  className="mt-1 w-full px-3 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm outline-none focus:border-primary"
                />
              </label>
            ))}
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-on-surface flex items-center gap-2">
              <MaterialIcon icon="toggle_on" size={18} />
              Runtime Switches
            </h3>
            <label className="flex items-center justify-between gap-4 rounded-xl bg-surface-container-low px-3 py-3">
              <span className="text-sm font-medium text-on-surface">啟用 PII 匿名化</span>
              <input
                type="checkbox"
                checked={config.enable_anonymization}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    enable_anonymization: event.target.checked,
                  }))
                }
                className="h-5 w-5 accent-primary"
              />
            </label>
            <label className="flex items-center justify-between gap-4 rounded-xl bg-surface-container-low px-3 py-3">
              <span className="text-sm font-medium text-on-surface">Development mode</span>
              <input
                type="checkbox"
                checked={config.development_mode}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    development_mode: event.target.checked,
                  }))
                }
                className="h-5 w-5 accent-primary"
              />
            </label>
            <label className="flex items-center justify-between gap-4 rounded-xl bg-surface-container-low px-3 py-3">
              <span className="text-sm font-medium text-on-surface">允許圖片送入模型</span>
              <input
                type="checkbox"
                checked={config.enable_image_upload}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    enable_image_upload: event.target.checked,
                  }))
                }
                className="h-5 w-5 accent-primary"
              />
            </label>
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-on-surface flex items-center gap-2">
              <MaterialIcon icon="edit_note" size={18} />
              Prompt Sections
            </h3>
            <p className="text-xs leading-5 text-on-surface/55">
              此環境的 Prompt Sections 儲存在 Firestore runtime config。
            </p>
            {PROMPT_SECTION_FIELDS.map(([key, label]) => (
              <label key={key} className="block">
                <span className="text-xs font-medium text-on-surface/60">{label}</span>
                <textarea
                  value={config.agent_prompt_sections[key] ?? ""}
                  onChange={(event) =>
                    setConfig((current) => ({
                      ...current,
                      agent_prompt_sections: {
                        ...current.agent_prompt_sections,
                        [key]: event.target.value,
                      },
                    }))
                  }
                  rows={key === "analysis_rules" ? 12 : key === "output_format" ? 7 : 4}
                  className="mt-1 w-full resize-y px-3 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm leading-6 outline-none focus:border-primary"
                  placeholder="留空使用程式內建 section"
                />
              </label>
            ))}
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-on-surface flex items-center gap-2">
              <MaterialIcon icon="campaign" size={18} />
              Maintenance Message
            </h3>
            <textarea
              value={config.maintenance_message ?? ""}
              onChange={(event) =>
                setConfig((current) => ({ ...current, maintenance_message: event.target.value }))
              }
              rows={3}
              className="w-full resize-y px-3 py-2.5 rounded-xl border border-outline/30 bg-surface-container-low text-sm leading-6 outline-none focus:border-primary"
              placeholder="可留空"
            />
          </section>

          {(status || error) && (
            <div
              className={`rounded-xl px-3 py-2 text-sm ${
                error
                  ? "bg-error-container/50 text-error"
                  : "bg-primary-container text-on-surface"
              }`}
            >
              {error || status}
            </div>
          )}
          </>
          )}
        </div>

        {isAuthenticated && (
          <div className="border-t border-outline/20 p-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <button
                onClick={seedConfig}
                disabled={isSaving}
                className="px-3 py-2.5 rounded-xl bg-surface-container text-sm font-semibold text-on-surface hover:bg-surface-container-high disabled:opacity-50 cursor-pointer"
              >
                補齊設定
              </button>
              <button
                onClick={resetConfig}
                disabled={isSaving}
                className="px-3 py-2.5 rounded-xl border border-error/25 bg-error-container/30 text-sm font-semibold text-error hover:bg-error-container/50 disabled:opacity-50 cursor-pointer"
              >
                重置設定
              </button>
            </div>
            <button
              onClick={saveConfig}
              disabled={isSaving || !isDirty}
              className="min-w-32 px-4 py-2.5 rounded-xl bg-primary text-on-primary text-sm font-bold disabled:opacity-50 cursor-pointer"
            >
              {isSaving ? "儲存中" : isDirty ? "儲存變更" : "已同步"}
            </button>
          </div>
        )}
      </div>
    </>
  );
}
