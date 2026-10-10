import { useEffect, useId, useState } from "react";
import { getRerankModels, type RuntimeConfig } from "../services/api";
import { retrievalSettings } from "../services/retrievalSettings";

export default function RerankSettings({ config, onChange, csrfToken }: {
  config: RuntimeConfig; onChange: (value: Partial<RuntimeConfig>) => void; csrfToken: string;
}) {
  const value = retrievalSettings(config);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const listId = useId();
  useEffect(() => {
    let active = true;
    void getRerankModels(csrfToken).then(result => { if (active) { setModels(result.data); setUnavailable(false); } })
      .catch(() => { if (active) setUnavailable(true); });
    return () => { active = false; };
  }, [csrfToken]);
  return <section className="space-y-4 border-t border-outline/10 pt-6" aria-label="模型路由與 Rerank">
    <h4 className="text-sm font-bold">模型路由與 Rerank</h4>
    <label className="block text-sm">Provider 排序<select className="input mt-2" value={value.openrouter_provider_sort} onChange={e => onChange({ openrouter_provider_sort: e.target.value as RuntimeConfig["openrouter_provider_sort"] })}>
      <option value="auto">自動（OpenRouter 預設）</option><option value="latency">優先低延遲</option><option value="throughput">優先高吞吐量</option><option value="price">優先低價格</option>
    </select></label>
    <label className="block text-sm">Rerank 模型<input className="input mt-2" list={listId} value={value.rag_rerank_model} onChange={e => onChange({ rag_rerank_model: e.target.value })} /></label>
    <datalist id={listId}>{models.map(model => <option key={model.id} value={model.id}>{model.name}</option>)}</datalist>
    <p className="text-xs text-on-surface/65">{unavailable ? "模型清單暫時無法載入；可保留目前設定或手填模型 ID。" : "可從清單選取或輸入 OpenRouter rerank 模型 ID。"}</p>
    <div className="grid gap-3 sm:grid-cols-3">
      <label className="text-sm">候選文件數<input className="input mt-2" type="number" min={config.rag_retrieval_top_k} max={50} value={value.rag_rerank_candidate_k} onChange={e => onChange({ rag_rerank_candidate_k: Number(e.target.value) })} /></label>
      <label className="text-sm">重排等待上限（秒）<input className="input mt-2" type="number" min={0.1} max={10} step={0.1} value={value.rag_rerank_timeout_seconds} onChange={e => onChange({ rag_rerank_timeout_seconds: Number(e.target.value) })} /></label>
      <label className="text-sm">每份重排文字上限<input className="input mt-2" type="number" min={100} max={20000} step={100} value={value.rag_rerank_max_chars_per_doc} onChange={e => onChange({ rag_rerank_max_chars_per_doc: Number(e.target.value) })} /></label>
    </div>
    <p className="text-xs leading-relaxed text-on-surface/65">RAG Top K 是最終文件數；候選數不得小於它。重排逾時或失敗就沿用原始排序，不重試。等待上限不包含向量檢索時間。</p>
  </section>;
}
