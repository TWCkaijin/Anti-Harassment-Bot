import type { PipelineSettings } from "../services/pipeline";

const switches = [
  ["mask_message", "遮罩訊息與歷史", "送入模型前處理訊息與歷史中的文字識別資訊。"],
  ["mask_case_context", "遮罩情境摘要", "處理結構化摘要及選項回覆中的文字識別資訊。"],
  ["mask_retrieval_query", "遮罩檢索查詢", "送入檢索服務前處理查詢文字。"],
  ["trim_history", "精簡對話歷史", "依下方預算保留近期歷史；關閉時仍有請求大小上限。"],
  ["extract_facts", "自動擷取事實", "從使用者明確敘述整理條件；不會刪除已確認摘要。"],
  ["auto_clarify", "允許補問", "只在會改變回答方向的資料不足時補問。"],
  ["model_selection_mode", "依問題決定單選或複選", "由問題條件決定選擇方式，仍受選項數量與格式驗證。"],
  ["enable_rag", "啟用資料檢索", "查詢資料庫中的法規、案例及求助資料。"],
  ["enable_skills", "啟用情境腳本", "套用符合情境的已設定指令與資源動作。"],
  ["enable_analysis", "顯示分析摘要", "提供已確認條件、資料來源與回答限制的摘要。"],
] as const;

export default function PipelineSettingsEditor({ value, onChange, disabled = false }: { value: PipelineSettings; onChange: (value: PipelineSettings) => void; disabled?: boolean }) {
  const set = (changes: Partial<PipelineSettings>) => onChange({ ...value, ...changes });
  return <fieldset disabled={disabled} className="space-y-3">
    <legend className="mb-3 text-sm font-bold">處理流程開關</legend>
    <p className="text-xs leading-relaxed text-on-surface/65">以下三個遮罩階段都在後端，受後端 PII 開關控制；瀏覽器仍會自動遮罩文字，確認視窗可由「送出前遮罩確認」開關暫停。診斷結果會列出生效設定與各步驟移除的數量。</p>
    {switches.map(([key, label, description]) => <label key={key} className="flex items-center justify-between gap-4 rounded-lg border border-outline/15 px-3 py-3 text-sm">
      <span><span className="font-medium">{label}</span><span className="mt-1 block text-xs leading-relaxed text-on-surface/60">{description}</span></span>
      <input type="checkbox" aria-label={label} checked={value[key]} onChange={event => set({ [key]: event.target.checked })} className="h-5 w-5 shrink-0 accent-primary" />
    </label>)}
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm">歷史訊息上限<input aria-label="歷史訊息上限" type="number" min={0} max={200} value={value.history_max_messages} disabled={!value.trim_history} onChange={event => set({ history_max_messages: Number(event.target.value) })} className="input mt-1" /></label>
      <label className="text-sm">歷史字元上限<input aria-label="歷史字元上限" type="number" min={0} max={120000} value={value.history_max_chars} disabled={!value.trim_history} onChange={event => set({ history_max_chars: Number(event.target.value) })} className="input mt-1" /></label>
    </div>
    <label className="block text-sm">內容處理方式<select aria-label="內容處理方式" value={value.content_policy} onChange={event => set({ content_policy: event.target.value as PipelineSettings["content_policy"] })} className="input mt-1">
      <option value="repair">整理不一致的內容</option><option value="annotate">保留有效原文並標示限制</option>
    </select></label>
    <p className="text-xs leading-relaxed text-on-surface/60">回應格式驗證、連結檢查、權限與請求大小上限始終適用。</p>
  </fieldset>;
}
