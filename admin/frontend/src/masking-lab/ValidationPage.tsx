import { useEffect, useRef, useState } from "react";
import { MODEL_ID } from "./config";
import { BrowserMaskingRuntime } from "./runtime";
import { createReport, evaluateSplit, type CaseResult, type EvaluationReport, type Split } from "./benchmark";
import type { LoadMetrics } from "./types";
import { MaskingError } from "./extraction";

declare global { interface Window { maskingValidationReport?: EvaluationReport } }
const seconds = (ms: number | null | undefined) => ms == null ? "尚未量測" : `${(ms / 1000).toFixed(2)} 秒`;
const percent = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;

export default function ValidationPage() {
  const runtime = useRef<BrowserMaskingRuntime | null>(null);
  const controller = useRef<AbortController | null>(null);
  const [status, setStatus] = useState("尚未載入模型");
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [coldLoad, setColdLoad] = useState<LoadMetrics | null>(null);
  const [cachedLoad, setCachedLoad] = useState<LoadMetrics | null>(null);
  const [cases, setCases] = useState<CaseResult[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const report = createReport(cases, coldLoad, cachedLoad);

  useEffect(() => {
    // This test hook exists only on the standalone synthetic validation page.
    window.maskingValidationReport = createReport(cases, coldLoad, cachedLoad);
  }, [cases, coldLoad, cachedLoad]);
  useEffect(() => () => { controller.current?.abort(); runtime.current?.dispose(); }, []);

  const load = async () => {
    setBusy(true); setReady(false); setFailure(null); setStatus("正在載入本機模型");
    runtime.current?.dispose();
    runtime.current = new BrowserMaskingRuntime();
    try {
      const metrics = await runtime.current.load((fraction, text) => { setProgress(fraction); setStatus(text); });
      if (coldLoad) setCachedLoad(metrics); else setColdLoad(metrics);
      setStatus("暖機中");
      await runtime.current.detect("我和主管是同事，想知道如何記錄事件。");
      setReady(true); setStatus("模型就緒；可執行合成案例");
    } catch (error) {
      setFailure(error instanceof MaskingError ? error.code : "initialization_failed");
      setStatus("模型未就緒；沒有發送任何聊天請求");
    } finally { setBusy(false); }
  };
  const run = async (split: Split) => {
    if (!runtime.current || !ready) return;
    setBusy(true); setFailure(null);
    setCases(previous => previous.filter(item => item.split !== split));
    const abortController = new AbortController(); controller.current = abortController;
    let completed = 0;
    try {
      await evaluateSplit(runtime.current, split, abortController.signal, result => {
        completed += 1;
        setCases(previous => [...previous, result]);
        setStatus(`${split === "development" ? "開發集" : "保留驗證集"} ${completed} / ${split === "development" ? 80 : 40} · ${result.id}`);
      });
      setStatus(abortController.signal.aborted ? "已停止驗證" : `本輪完成 ${completed} 則；請查看指標與失敗案例`);
    } catch { setFailure("evaluation_failed"); }
    finally { controller.current = null; setBusy(false); }
  };
  const cancel = () => {
    controller.current?.abort(); runtime.current?.dispose(); setReady(false); setStatus("已停止；需重新載入模型");
  };
  const download = () => {
    const link = document.createElement("a");
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    link.href = url; link.download = "browser-masking-validation.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <main className="mx-auto max-w-5xl space-y-6 px-5 py-10 text-on-surface">
    <header className="space-y-3">
      <p className="text-sm font-semibold text-primary">本機模型 · 獨立驗證頁</p>
      <h1 className="text-3xl font-bold">瀏覽器自動遮罩驗證</h1>
      <p className="max-w-3xl leading-relaxed">只處理 120 則內建合成案例，不連接聊天 API。先驗證辨識品質與速度，通過後才整合正式對話。原始文字與辨識結果留在此裝置。</p>
      <p className="text-sm text-on-surface/65">模型：{MODEL_ID}。首次載入會下載模型資源；請保留此頁，重新載入可量測快取效果。</p>
    </header>
    <section className="space-y-4 rounded-2xl border border-outline/20 p-5" aria-label="執行控制">
      <div className="flex flex-wrap gap-3">
        <button className="rounded-xl bg-primary px-4 py-3 text-white disabled:opacity-40" disabled={busy} onClick={() => void load()}>{coldLoad ? "重新載入並量測快取" : "載入本機模型"}</button>
        <button className="rounded-xl border border-primary/40 px-4 py-3 disabled:opacity-40" disabled={busy || !ready} onClick={() => void run("development")}>執行開發集 80 則</button>
        <button className="rounded-xl border border-primary/40 px-4 py-3 disabled:opacity-40" disabled={busy || !ready || report.scores.development.cases !== 80} onClick={() => void run("holdout")}>執行保留驗證集 40 則</button>
        <button className="rounded-xl border border-outline/30 px-4 py-3 disabled:opacity-40" disabled={!busy} onClick={cancel}>停止</button>
        <button className="rounded-xl border border-outline/30 px-4 py-3" onClick={download}>下載驗證報告</button>
      </div>
      <p role="status" className="break-words text-sm">{status}</p>
      {busy && !ready && <progress aria-label="模型載入進度" max={1} value={progress} className="w-full" />}
      {failure && <p role="alert" className="text-error">驗證未完成：{failure}。請確認 WebGPU 與網路環境後重試。</p>}
    </section>
    <section className="grid gap-3 sm:grid-cols-3" aria-label="模型載入量測">
      <Metric label="首次載入" value={seconds(coldLoad?.elapsedMs)} />
      <Metric label="首次 fetch 內容量" value={coldLoad ? `${(coldLoad.fetchedBytes / 1024 / 1024).toFixed(1)} MiB` : "尚未量測"} />
      <Metric label="快取載入" value={seconds(cachedLoad?.elapsedMs)} />
    </section>
    <section className="overflow-x-auto rounded-2xl border border-outline/20 p-5">
      <h2 className="mb-4 text-lg font-bold">品質與延遲</h2>
      <table className="w-full text-left text-sm"><thead><tr>{["資料集", "完成", "有效輸出", "精確率", "召回率", "P95（200–500 字）"].map(label => <th key={label} className="p-2">{label}</th>)}</tr></thead>
        <tbody>{([['development', '開發集'], ['holdout', '保留驗證集'], ['all', '全部']] as const).map(([key, label]) => {
          const score = report.scores[key];
          return <tr key={key}><td className="p-2">{label}</td><td className="p-2">{score.cases}</td><td className="p-2">{score.successfulChecks}</td><td className="p-2">{score.cases && score.precision !== null ? percent(score.precision) : '—'}</td><td className="p-2">{score.cases ? percent(score.recall) : '—'}</td><td className="p-2">{seconds(score.p95Ms)}</td></tr>;
        })}</tbody></table>
      <p className="mt-4 font-medium" data-testid="gate-status">{report.gatePassed ? "驗收門檻通過" : cases.length === 120 ? "未通過驗收：暫不替換正式流程" : "尚未完成全部驗證"}</p>
      <p className="mt-2 text-xs leading-relaxed text-on-surface/65">目標：兩組資料集各自精確率／召回率 ≥ 95%，有效輸出 100%，暖機後 P95 ≤ 5 秒。完整片段比對，部分遮罩同時計為漏遮與誤遮；合成資料成績不代表真實情境保證。</p>
    </section>
    <section className="space-y-3"><h2 className="text-lg font-bold">案例結果</h2>
      {cases.length === 0 && <p className="text-sm text-on-surface/65">完成辨識後，這裡會顯示原文、預期片段與實際遮罩。</p>}
      {cases.map(item => <details key={item.id} className="rounded-xl border border-outline/20 p-4">
        <summary className="cursor-pointer font-medium">{item.id} · {seconds(item.elapsedMs)} · {item.error ? item.error : item.missing.length || item.unexpected.length ? `漏遮 ${item.missing.length}／誤遮 ${item.unexpected.length}` : "片段符合"}</summary>
        <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed">{item.source}</p>
        <p className="mt-3 text-sm">預期：{item.expected.map(entity => entity.text).join("、") || "無"}</p>
        <p className="mt-3 text-sm">實際：{item.detected.map(entity => entity.text).join("、") || "無"}</p>
        <p className="mt-3 whitespace-pre-wrap rounded-lg bg-surface-container-low p-3 text-sm leading-relaxed">{item.masked ?? "檢查失敗；不產生可送出的內容。"}</p>
      </details>)}
    </section>
  </main>;
}
function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl bg-surface-container-low p-4"><p className="text-xs text-on-surface/65">{label}</p><p className="mt-2 text-lg font-semibold">{value}</p></div>;
}
