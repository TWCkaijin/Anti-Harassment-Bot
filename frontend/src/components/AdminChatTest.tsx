import { useEffect, useRef, useState } from "react";
import { ApiError, runAdminChatTest, type AdminChatTestResult, type ChatRequest, type RuntimeConfig, type RuntimeConfigUpdate } from "../services/api";
import { isRecord, MAX_SUMMARY_LENGTH } from "../services/caseFacts";
import { reproducibleOverrides } from "../services/adminDiagnostics";
import { requestPrivacyReview } from "../services/privacyReview";
import { DEFAULT_PIPELINE } from "../services/pipeline";
import PipelineSettingsEditor from "./PipelineSettingsEditor";

interface TestRun { id: number; request: ChatRequest; result: AdminChatTestResult; comparison: boolean }
const DIAGNOSTIC_SECTIONS = [
  ["effective_config", "生效設定"], ["pii", "PII 處理"], ["input_stages", "輸入處理前後"],
  ["history", "歷史裁切"], ["retrieval", "檢索結果"], ["rewrites", "內容改寫"],
] as const;

export default function AdminChatTest({ adminToken, config }: { adminToken: string; config: RuntimeConfig }) {
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState("[]");
  const [summary, setSummary] = useState("");
  const [pipeline, setPipeline] = useState(() => ({ ...DEFAULT_PIPELINE, ...config.pipeline }));
  const [masking, setMasking] = useState(config.enable_anonymization);
  const [topK, setTopK] = useState(config.rag_retrieval_top_k);
  const [threshold, setThreshold] = useState(config.rag_distance_threshold);
  const [runs, setRuns] = useState<TestRun[]>([]);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const sequence = useRef(0);
  const requestRef = useRef<AbortController | null>(null);
  useEffect(() => () => requestRef.current?.abort(), []);
  const latest = runs.at(-1);
  const comparisonConfig = reproducibleOverrides(latest?.result.diagnostics.effective_config);

  const execute = async (request: ChatRequest, overrides: RuntimeConfigUpdate, comparison = false) => {
    if (requestRef.current) return;
    const controller = new AbortController();
    const frozenOverrides = structuredClone(overrides);
    requestRef.current = controller;
    setError(""); setRunning(true);
    try {
      const reviewedRequest = comparison ? request : await requestPrivacyReview(request, controller.signal, { enabled: config.enable_client_privacy_review !== false });
      if (!reviewedRequest || controller.signal.aborted || requestRef.current !== controller) return;
      const result = await runAdminChatTest(adminToken, reviewedRequest, frozenOverrides, controller.signal);
      if (!controller.signal.aborted && requestRef.current === controller) {
        const id = ++sequence.current;
        setRuns(previous => [...previous, { id, request: reviewedRequest, result, comparison }].slice(-2));
      }
    } catch (failure) {
      if (!controller.signal.aborted && requestRef.current === controller) setError(failure instanceof ApiError ? failure.detail ?? failure.message : failure instanceof Error ? failure.message : "測試失敗");
    } finally { if (requestRef.current === controller) { requestRef.current = null; setRunning(false); } }
  };
  const run = () => {
    if (running || !message.trim()) return;
    try {
      const parsedHistory: unknown = JSON.parse(history);
      if (!Array.isArray(parsedHistory) || parsedHistory.length > 200 || !parsedHistory.every(item => isRecord(item) && ["user", "assistant"].includes(String(item.role)) && typeof item.content === "string")) throw new Error("歷史必須是 user／assistant 訊息陣列，最多 200 筆。");
      if (summary.length > MAX_SUMMARY_LENGTH) throw new Error("情境摘要最多 4000 字。");
      const request: ChatRequest = { message: message.trim(), history: parsedHistory, use_rag: pipeline.enable_rag, contract_version: 4, case_context: { schema_version: 3, revision: 0, facts: {}, summary, summary_origin: "user" } };
      void execute(request, { pipeline, enable_anonymization: masking, enable_client_privacy_review: config.enable_client_privacy_review !== false, rag_retrieval_top_k: topK, rag_distance_threshold: threshold });
    } catch (failure) { setError(failure instanceof Error ? failure.message : "測試輸入格式不正確"); }
  };
  const compare = () => {
    if (latest && comparisonConfig && !running) void execute(latest.request, { ...comparisonConfig, enable_anonymization: !comparisonConfig.enable_anonymization }, true);
  };
  const stop = () => { requestRef.current?.abort(); requestRef.current = null; setRunning(false); };

  return <section aria-label="本次診斷測試" className="mx-auto max-w-5xl space-y-5">
    <div><h3 className="text-xl font-bold">本次診斷測試</h3><p className="mt-2 text-sm leading-relaxed text-on-surface/65">使用目前環境的模型；覆寫值只套用這次請求。最近兩次結果只保留在這個頁面的記憶體中。</p></div>
    <label className="block text-sm font-medium">測試訊息<textarea aria-label="測試訊息" maxLength={2000} value={message} onChange={event => setMessage(event.target.value)} rows={4} className="input mt-2" placeholder="輸入合成測試情境" /></label>
    <details className="rounded-lg border border-outline/15 p-3"><summary className="cursor-pointer text-sm">測試歷史與情境摘要</summary>
      <label className="mt-3 block text-sm">歷史 JSON<textarea aria-label="歷史 JSON" value={history} onChange={event => setHistory(event.target.value)} rows={4} className="input mt-1 font-mono text-xs" /></label>
      <label className="mt-3 block text-sm">文字情境摘要<textarea aria-label="文字情境摘要" maxLength={MAX_SUMMARY_LENGTH} value={summary} onChange={event => setSummary(event.target.value)} rows={4} className="input mt-1 text-sm" placeholder="輸入已確認的情境摘要" /></label>
    </details>
    <div className="space-y-2"><label className="flex items-center justify-between gap-3 text-sm">後端 PII 遮罩（第二層）<input aria-label="後端 PII 遮罩（第二層）" type="checkbox" checked={masking} onChange={event => setMasking(event.target.checked)} /></label>
      <p className="text-xs leading-relaxed text-on-surface/65">瀏覽器會自動遮罩文字；送前確認視窗依目前系統設定開關決定。這個開關只控制後端的第二層遮罩，PII 比較會重用同一份送出資料。</p></div>
    <div className="grid gap-3 sm:grid-cols-2"><label className="text-sm">本次檢索 Top K<input aria-label="本次檢索 Top K" type="number" min={1} max={20} value={topK} onChange={event => setTopK(Number(event.target.value))} className="input mt-1" /></label>
      <label className="text-sm">本次相似度門檻<input aria-label="本次相似度門檻" type="number" min={0} max={2} step={0.01} value={threshold ?? ""} onChange={event => setThreshold(event.target.value === "" ? null : Number(event.target.value))} placeholder="停用" className="input mt-1" /></label></div>
    <PipelineSettingsEditor value={pipeline} onChange={setPipeline} disabled={running} />
    <div className="flex flex-wrap gap-2"><button type="button" onClick={run} disabled={running || !message.trim()} className="min-h-11 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{running ? "測試中…" : "執行本次測試"}</button>
      {latest && <button type="button" onClick={compare} disabled={running || !comparisonConfig} className="min-h-11 rounded-lg border border-primary/30 px-4 py-2 text-sm disabled:opacity-40">只切換後端 PII 再測一次</button>}
      {running && <button type="button" onClick={stop} className="rounded-lg border border-outline/20 px-4 py-2 text-sm">停止測試</button>}</div>
    {latest && !comparisonConfig && <p className="text-xs text-on-surface/60">這次診斷未提供完整設定，無法建立只切換後端 PII 的比較。</p>}
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    {runs.length > 0 && <div aria-label="最近兩次測試比較" className="grid min-w-0 gap-4 lg:grid-cols-2">{runs.map(item => <article key={item.id} aria-label={`測試 ${item.id}`} className="min-w-0 space-y-3 rounded-xl border border-outline/15 p-4">
      <h4 className="font-semibold">測試 {item.id}{item.comparison ? " · 僅切換後端 PII" : ""}</h4>
      <p className="text-xs text-on-surface/60">後端 PII：{item.result.diagnostics.effective_config && isRecord(item.result.diagnostics.effective_config) ? item.result.diagnostics.effective_config.enable_anonymization ? "開啟" : "關閉" : "未提供"}</p>
      <h5 className="text-sm font-semibold">測試回覆</h5><p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{item.result.response.reply}</p>
      {DIAGNOSTIC_SECTIONS.map(([key, label]) => <details key={key} open={key === "input_stages"} className="rounded-lg border border-outline/15 p-3"><summary className="cursor-pointer text-sm font-semibold">{label}</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(item.result.diagnostics[key] ?? "未提供", null, 2)}</pre></details>)}
    </article>)}</div>}
  </section>;
}
