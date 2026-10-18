import type { RuntimeConfig } from "../services/api";
import { DEFAULT_PIPELINE } from "../services/pipeline";
import { PROMPT_SECTION_FIELDS } from "../services/runtimeConfig";
import PipelineSettingsEditor from "./PipelineSettingsEditor";
import RerankSettings from "./RerankSettings";

const REASONING_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"] as const;

export default function DiagnosticSettings({
  config,
  onChange,
  csrfToken,
  disabled,
  showEmotions,
  onShowEmotions,
}: {
  config: RuntimeConfig;
  onChange: (config: RuntimeConfig) => void;
  csrfToken: string;
  disabled: boolean;
  showEmotions: boolean;
  onShowEmotions: (show: boolean) => void;
}) {
  const set = (value: Partial<RuntimeConfig>) => onChange({ ...config, ...value });

  return (
    <fieldset disabled={disabled} className="space-y-5 disabled:opacity-65">
      <legend className="sr-only">本次暫時設定</legend>
      <section className="space-y-3" aria-label="本次模型設定">
        <h4 className="text-xs font-bold tracking-wide text-on-surface/55">模型與生成</h4>
        <label className="block text-sm">
          本次模型
          <input
            className="input"
            value={config.openrouter_model}
            required
            onChange={(event) => set({ openrouter_model: event.target.value })}
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            Temperature
            <input
              type="number"
              min={0}
              max={2}
              step={0.05}
              className="input"
              value={config.temperature}
              onChange={(event) => set({ temperature: Number(event.target.value) })}
            />
          </label>
          <label className="text-sm">
            Top P
            <input
              type="number"
              min={0.01}
              max={1}
              step={0.01}
              className="input"
              value={config.top_p}
              onChange={(event) => set({ top_p: Number(event.target.value) })}
            />
          </label>
          <label className="text-sm">
            Max tokens
            <input
              type="number"
              min={0}
              max={8192}
              className="input"
              value={config.max_tokens}
              onChange={(event) => set({ max_tokens: Number(event.target.value) })}
            />
          </label>
          <label className="text-sm">
            思考等級
            <select
              className="input"
              value={config.reasoning_effort}
              onChange={(event) =>
                set({ reasoning_effort: event.target.value as RuntimeConfig["reasoning_effort"] })
              }
            >
              {REASONING_EFFORTS.map((value) => (
                <option key={value} value={value}>
                  {value === "none" ? "關閉" : value}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="text-xs text-on-surface/50">Max tokens 為 0 時不設定上限。</p>
      </section>

      <section className="space-y-3 border-t border-outline/15 pt-4" aria-label="本次檢索設定">
        <h4 className="text-xs font-bold tracking-wide text-on-surface/55">檢索</h4>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            本次檢索 Top K
            <input
              type="number"
              min={1}
              max={20}
              className="input"
              value={config.rag_retrieval_top_k}
              onChange={(event) => set({ rag_retrieval_top_k: Number(event.target.value) })}
            />
          </label>
          <label className="text-sm">
            本次相似度門檻
            <input
              type="number"
              min={0}
              max={2}
              step={0.01}
              placeholder="停用"
              className="input"
              value={config.rag_distance_threshold ?? ""}
              onChange={(event) =>
                set({
                  rag_distance_threshold:
                    event.target.value === "" ? null : Number(event.target.value),
                })
              }
            />
          </label>
        </div>
        <Check
          label="啟用 Reranking"
          checked={config.rag_rerank_enabled ?? false}
          onChange={(rag_rerank_enabled) => set({ rag_rerank_enabled })}
        />
        <details className="rounded-lg border border-outline/20 p-3">
          <summary className="cursor-pointer text-sm">模型路由與 Rerank 進階設定</summary>
          <RerankSettings config={config} csrfToken={csrfToken} onChange={set} />
        </details>
      </section>

      <section className="space-y-3 border-t border-outline/15 pt-4" aria-label="本次對話功能">
        <h4 className="text-xs font-bold tracking-wide text-on-surface/55">對話功能</h4>
        <Check label="顯示心情分析" checked={showEmotions} onChange={onShowEmotions} />
        <Check
          label="允許圖片附件"
          checked={config.enable_image_upload}
          onChange={(enable_image_upload) => set({ enable_image_upload })}
        />
        <Check
          label="後端 PII 遮罩（第二層）"
          checked={config.enable_anonymization}
          onChange={(enable_anonymization) => set({ enable_anonymization })}
        />
        <p className="text-xs leading-relaxed text-on-surface/55">
          瀏覽器會先遮罩文字。圖片與一般對話相同，以原圖送出。
        </p>
      </section>

      <details className="rounded-lg border border-outline/20 p-3">
        <summary className="cursor-pointer text-sm font-medium">處理流程</summary>
        <div className="pt-4">
          <PipelineSettingsEditor
            value={{ ...DEFAULT_PIPELINE, ...config.pipeline }}
            onChange={(pipeline) => set({ pipeline })}
            maskingEnabled={config.enable_anonymization}
            disabled={disabled}
          />
        </div>
      </details>

      <details className="rounded-lg border border-outline/20 p-3">
        <summary className="cursor-pointer text-sm font-medium">本次 Prompt</summary>
        <div className="space-y-4 pt-4">
          {PROMPT_SECTION_FIELDS.map(([key, label]) => (
            <label key={key} className="block text-sm">
              {label}
              <textarea
                className="input resize-y"
                rows={4}
                value={config.agent_prompt_sections[key] ?? ""}
                onChange={(event) =>
                  set({
                    agent_prompt_sections: {
                      ...config.agent_prompt_sections,
                      [key]: event.target.value,
                    },
                  })
                }
              />
            </label>
          ))}
        </div>
      </details>
    </fieldset>
  );
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 text-sm">
      <span>{label}</span>
      <input
        aria-label={label}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 shrink-0 accent-primary"
      />
    </label>
  );
}
