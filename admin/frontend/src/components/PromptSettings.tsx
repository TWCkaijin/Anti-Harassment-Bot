import type { RuntimeConfig } from "../services/api";
import { PROMPT_SECTION_FIELDS } from "../services/runtimeConfig";
import { Field } from "./SettingsFields";

export default function PromptSettings({
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
          各環境分別儲存。主模型提示詞欄位留空時會使用內建內容。
        </p>
      </div>
      <section aria-labelledby="gate-prompt-heading" className="space-y-4 rounded-xl border border-outline/20 bg-surface-container-low p-4">
        <div>
          <h4 id="gate-prompt-heading" className="font-bold">聊天內容審查（Gate）</h4>
          <p className="mt-1 text-xs leading-relaxed text-on-surface/65">
            Gate 啟用時會依這兩段提示詞審查。修改後按「儲存變更」，再到「本次診斷測試」確認判定。
          </p>
        </div>
        <Field label="Jev 判定門檻">
          <input
            type="number"
            min={0.5}
            max={1}
            step={0.01}
            value={Number.isNaN(config.gate_jev_threshold) ? "" : config.gate_jev_threshold ?? 0.7}
            onChange={(event) => onChange({ ...config, gate_jev_threshold: event.target.valueAsNumber })}
            aria-describedby="gate-jev-threshold-help"
            className="input max-w-40"
          />
        </Field>
        <p id="gate-jev-threshold-help" className="text-xs leading-relaxed text-on-surface/65">
          Jev 的輸入與回覆審查共用此門檻。最高選項機率低於門檻時，系統會請使用者重試。
        </p>
        <Field label="Gate 輸入審查提示詞（pre）">
          <textarea
            aria-describedby="gate-input-prompt-help"
            value={config.gate_prompt ?? ""}
            onChange={(event) => onChange({ ...config, gate_prompt: event.target.value })}
            rows={6}
            maxLength={20_000}
            className="input resize-y leading-6"
          />
        </Field>
        <p id="gate-input-prompt-help" className="text-xs text-on-surface/65">設定如何判斷使用者輸入，請填寫內容。</p>
        <Field label="Gate 回覆審查提示詞（post）">
          <textarea
            aria-describedby="gate-reply-prompt-help"
            value={config.gate_post_prompt ?? ""}
            onChange={(event) => onChange({ ...config, gate_post_prompt: event.target.value })}
            rows={8}
            maxLength={20_000}
            className="input resize-y leading-6"
          />
        </Field>
        <p id="gate-reply-prompt-help" className="text-xs text-on-surface/65">設定如何審查主模型產生的回覆，請填寫內容。</p>
      </section>

      <h4 className="border-t border-outline/10 pt-5 font-bold">主模型提示詞</h4>
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
