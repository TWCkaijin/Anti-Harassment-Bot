import type { RuntimeConfig } from "../services/api";
import { MaterialIcon } from "@rag/shared/chat-ui";
import { DEFAULT_PIPELINE } from "@rag/shared/pipeline";
import RerankSettings from "./RerankSettings";
import PipelineSettingsEditor from "./PipelineSettingsEditor";
import { Field, Toggle } from "./SettingsFields";

export default function SystemSettings({
  config,
  savedMaintenanceMessage,
  onOpenGatePrompts,
  onChange,
  csrfToken,
}: {
  csrfToken: string;
  config: RuntimeConfig;
  savedMaintenanceMessage?: string;
  onOpenGatePrompts: () => void;
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
            <span className="mt-1 block text-[0.6875rem] text-on-surface/50">0 表示不設定上限</span>
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
            <span className="mt-1 block text-[0.6875rem] text-on-surface/50">
              需使用支援 reasoning 的模型
            </span>
          </Field>
        </div>
        <RerankSettings config={config} onChange={set} csrfToken={csrfToken} />
      </section>

      <section aria-label="Gate 審查設定" className="space-y-4 border-t border-outline/10 pt-6">
        <h4 className="text-sm font-bold">Gate 審查設定</h4>
        <Toggle
          label="啟用聊天內容審查"
          checked={config.gate_enabled ?? false}
          onChange={(gate_enabled) => set({ gate_enabled })}
        />
        <p className="text-xs leading-relaxed text-on-surface/65">
          啟用後，Gate 會先審查使用者輸入，再審查主模型回覆。審查失敗或逾時時會隱藏回覆並提供重試。
        </p>
        <Field label="Gate OpenRouter Model">
          <input
            value={config.gate_model ?? ""}
            onChange={(event) => set({ gate_model: event.target.value })}
            className="input"
          />
        </Field>
        <button
          type="button"
          onClick={() => set({ gate_model: "typesafe/jev-1.14-20261014" })}
          className="rounded-md border border-outline/25 px-3 py-2 text-sm text-primary hover:bg-primary/5"
        >
          選用 Jev 1.14
        </button>
        <Field label="Gate 逾時（秒）">
          <input
            type="number"
            min="1"
            max="60"
            step="1"
            value={config.gate_timeout_seconds ?? 10}
            onChange={(event) => set({ gate_timeout_seconds: Number(event.target.value) })}
            className="input"
          />
        </Field>
        <button
          type="button"
          onClick={onOpenGatePrompts}
          className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-primary/20 px-3 text-sm font-medium text-primary hover:bg-primary/5 focus-visible:outline-2"
        >
          <MaterialIcon icon="edit_note" size={18} />
          編輯內容審查提示詞
        </button>
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
            瀏覽器會先遮罩文字並要求使用者確認送出內容。後端遮罩另由上方開關控制。
          </p>
          <Toggle
            label="啟用 Reranking"
            checked={config.rag_rerank_enabled ?? false}
            onChange={(checked) => set({ rag_rerank_enabled: checked })}
          />
          <p className="text-xs text-on-surface/60">資料檢索開啟時，才會對檢索結果重排。</p>
          <Toggle
            label="預留圖片設定（目前停用）"
            checked={config.enable_image_upload}
            onChange={(checked) => set({ enable_image_upload: checked })}
          />
          <p className="text-xs leading-relaxed text-on-surface/65">
            目前 Client 與 Admin 診斷端點均拒絕圖片。此欄位只保留既有設定值，不能啟用上傳。
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
          <span className="mt-1 block text-[0.6875rem] text-on-surface/50">
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
