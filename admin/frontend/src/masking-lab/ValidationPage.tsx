import { useEffect, useRef, useState } from "react";
import { ENTITY_TYPES, TINY_REVISION } from "./config";
import { BrowserMaskingRuntime } from "./runtime";
import { createReport, evaluateSplit, type CaseResult, type EvaluationReport, type Split } from "./benchmark";
import type { LoadMetrics } from "./types";
import { MaskingError } from "./extraction";
import { runBasicChecks, type BasicCheckReport } from "./selfCheck";
import { downloadBlob } from "@rag/shared/browser";

declare global { interface Window { maskingValidationReport?: EvaluationReport } }
const seconds = (ms: number | null | undefined) => ms == null ? "尚未量測" : `${(ms / 1000).toFixed(2)} 秒`;
const percent = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;
const typeNames = { person: "人物", organization: "單位", address: "地址", contact: "聯絡資訊", identifier: "識別號" } as const;

// active follows session access, not navigation: hidden panels keep their worker.
export default function ValidationPage({ active = true }: { active?: boolean }) {
  const runtime = useRef<BrowserMaskingRuntime | null>(null);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const [status, setStatus] = useState("尚未載入模型");
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [coldLoad, setColdLoad] = useState<LoadMetrics | null>(null);
  const [cachedLoad, setCachedLoad] = useState<LoadMetrics | null>(null);
  const [cases, setCases] = useState<CaseResult[]>([]);
  const [basicCheck, setBasicCheck] = useState<BasicCheckReport | null>(null);
  const [wasActive, setWasActive] = useState(active);
  const [failure, setFailure] = useState<string | null>(null);
  if (wasActive !== active) {
    setWasActive(active);
    if (!active) { setBusy(false); setReady(false); setStatus("已停止；需重新載入模型"); }
  }
  const report = createReport(cases, coldLoad, cachedLoad, basicCheck);

  useEffect(() => {
    // Synthetic-only report for local inspection; never contains user conversations.
    window.maskingValidationReport = createReport(cases, coldLoad, cachedLoad, basicCheck);
  }, [cases, coldLoad, cachedLoad, basicCheck]);
  useEffect(() => {
    const lifecycle = generation;
    if (!active) {
      lifecycle.current++; controller.current?.abort(); runtime.current?.dispose(); runtime.current = null;
    }
    return () => { lifecycle.current++; controller.current?.abort(); runtime.current?.dispose(); runtime.current = null; };
  }, [active]);
  useEffect(() => () => { delete window.maskingValidationReport; }, []);

  const load = async () => {
    if (!active || busy) return;
    const current = ++generation.current;
    setBusy(true); setReady(false); setFailure(null); setBasicCheck(null); setStatus("正在載入本機模型");
    const abortController = new AbortController(); controller.current = abortController;
    runtime.current?.dispose();
    const instance = new BrowserMaskingRuntime();
    runtime.current = instance;
    setProgress(0);
    try {
      const metrics = await instance.load((fraction, text) => {
        if (current === generation.current) { setProgress(fraction); setStatus(text); }
      });
      if (current !== generation.current) return;
      if (coldLoad) setCachedLoad(metrics); else setColdLoad(metrics);
      setStatus("正在執行姓名、電話、信箱與無個資的基本辨識檢查");
      const check = await runBasicChecks(instance, abortController.signal);
      if (current !== generation.current) return;
      setBasicCheck(check);
      if (!check.passed) {
        setFailure(check.cases.find(item => item.error)?.error ?? "basic_extraction_failed");
        setStatus("模型已載入，但基本辨識未通過；請查看下方實際輸出");
        return;
      }
      setReady(true); setStatus(`基本辨識命中 ${check.matched} / ${check.expected} 個片段；可執行完整驗證`);
    } catch (error) {
      if (current !== generation.current) return;
      setFailure(error instanceof MaskingError ? error.code : "initialization_failed");
      setStatus("模型未就緒；沒有發送任何聊天請求");
    } finally { if (current === generation.current) { controller.current = null; setBusy(false); } }
  };
  const run = async (split: Split) => {
    if (!active || !runtime.current || !ready) return;
    const current = generation.current;
    setBusy(true); setFailure(null);
    setCases(previous => previous.filter(item => item.split !== split));
    const abortController = new AbortController(); controller.current = abortController;
    let completed = 0;
    try {
      await evaluateSplit(runtime.current, split, abortController.signal, result => {
        if (current !== generation.current) return;
        completed += 1;
        setCases(previous => [...previous, result]);
        setStatus(`${split === "development" ? "開發集" : "保留驗證集"} ${completed} / ${split === "development" ? 80 : 40} · ${result.id}`);
      });
      if (current !== generation.current) return;
      setStatus(abortController.signal.aborted ? "已停止驗證" : `本輪完成 ${completed} 則；請查看指標與失敗案例`);
    } catch { if (current === generation.current) setFailure("evaluation_failed"); }
    finally { if (current === generation.current) { controller.current = null; setBusy(false); } }
  };
  const cancel = () => {
    generation.current++; controller.current?.abort(); runtime.current?.dispose(); runtime.current = null; setBusy(false); setReady(false); setStatus("已停止；需重新載入模型");
  };
  const download = () => {
    downloadBlob(JSON.stringify(report, null, 2), "browser-masking-validation-rules-tiny.json", "application/json");
  };
  return <section aria-label="本機遮罩驗證" className="mx-auto max-w-5xl space-y-6 text-on-surface">
    <header className="space-y-3">
      <h3 className="text-xl font-bold">瀏覽器自動遮罩驗證</h3>
      <p className="max-w-3xl leading-relaxed">只處理 120 則內建合成案例，不連接聊天 API。先驗證辨識品質與速度，通過後才整合正式對話。原始文字與辨識結果留在此裝置。</p>
    </header>
    <section className="space-y-4 rounded-2xl border border-outline/20 p-5" aria-label="執行控制">
      <div className="space-y-2">
        <p className="text-sm font-semibold">規則 + Tiny NER（CPU）</p>
        <p className="text-sm text-on-surface/75">規則先找地址、聯絡資訊與識別號，Tiny NER 再補人名及單位。</p>
        <p className="text-xs text-on-surface/65">模型版本 <code className="break-all">{TINY_REVISION}</code></p>
      </div>
      <div className="flex flex-wrap gap-3">
        <button className="rounded-xl bg-primary px-4 py-3 text-white disabled:opacity-40" disabled={!active || busy} onClick={() => void load()}>{coldLoad ? "重新載入 Tiny NER" : "載入 Tiny NER"}</button>


        <button className="rounded-xl border border-primary/40 px-4 py-3 disabled:opacity-40" disabled={!active || busy || !ready} onClick={() => void run("development")}>執行開發集 80 則</button>
        <button className="rounded-xl border border-primary/40 px-4 py-3 disabled:opacity-40" disabled={!active || busy || !ready || report.scores.development.cases !== 80} onClick={() => void run("holdout")}>執行保留驗證集 40 則</button>
        <button className="rounded-xl border border-outline/30 px-4 py-3 disabled:opacity-40" disabled={!busy} onClick={cancel}>停止</button>
        <button className="rounded-xl border border-outline/30 px-4 py-3" onClick={download}>下載驗證報告</button>
      </div>

      <p role="status" className="break-words text-sm">{status}</p>
      {busy && !ready && <progress aria-label="模型載入進度" max={1} value={progress} className="w-full" />}
      {failure && <p role="alert" className="text-error">驗證未完成：{failure}。{failure === "tiny_assets_missing" ? "此環境尚未準備 Tiny NER 模型檔。" : failure === "basic_extraction_failed" ? "基本案例中沒有命中任何預期片段。" : "請查看基本辨識輸出，或重新載入後再試。"}</p>}
    </section>
    {basicCheck && <section aria-label="基本辨識檢查" className="space-y-3 rounded-2xl border border-outline/20 p-5">
      <h2 className="text-lg font-bold">基本辨識檢查</h2>
      <p className="font-medium">{basicCheck.passed ? "已確認模型能辨識個資" : "尚未確認基本辨識能力"}：命中 {basicCheck.matched} / {basicCheck.expected}，額外片段 {basicCheck.unexpected}。</p>
      <p className="text-xs text-on-surface/65">這 3 則獨立合成案例只確認辨識流程可運作，不計入下方品質分數，也不代表通過完整驗收。</p>
      {basicCheck.cases.map(item => <div key={item.id} className="space-y-1 border-t border-outline/15 pt-3 text-sm">
        <p className="break-words">原文：{item.source}</p>
        <p>預期：{item.expected.map(entity => entity.text).join("、") || "無個資"}</p>
        <p className="break-words">辨識結果：{item.detected.map(entity => `${entity.text}（${entity.type}）`).join("、") || "空集合"}</p>
        <p className="break-words">遮罩結果：{item.masked ?? `檢查失敗：${item.error}`}</p>

      </div>)}
    </section>}
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
      <p className="mt-4 font-medium" data-testid="gate-status">{report.gatePassed ? "固定合成案例門檻通過；仍待獨立人工標注保留集" : cases.length === 120 ? "固定合成案例未通過：暫不替換正式流程" : "尚未完成全部驗證"}</p>
      <p className="mt-2 text-xs leading-relaxed text-on-surface/65">目標：兩組資料集各自整體及每類型精確率／召回率 ≥ 95%，有效輸出 100%，暖機後 P95 ≤ 5 秒。完整片段比對，部分遮罩同時計為漏遮與誤遮；合成資料成績不代表真實情境保證。</p>
    </section>
    <section className="overflow-x-auto rounded-2xl border border-outline/20 p-5" aria-label="各類型辨識結果">
      <h2 className="mb-2 text-lg font-bold">各類型辨識結果</h2>
      <p className="mb-4 text-xs leading-relaxed text-on-surface/65">姓名與單位分開列示。片段正確但類型錯誤，會在原類型計為漏判、預測類型計為誤判。40 則保留集仍是合成案例；獨立人工標注集尚待建立。</p>
      <table className="w-full min-w-[38rem] text-left text-sm"><thead><tr>{["資料集", "類型", "預期", "辨識", "精確率", "召回率", "F1", "漏判", "誤判"].map(label => <th key={label} className="p-2">{label}</th>)}</tr></thead>
        <tbody>{([['development', '開發集'], ['holdout', '保留驗證集'], ['all', '全部']] as const).flatMap(([split, label]) => ENTITY_TYPES.map(type => {
          const score = report.perType[split][type];
          return <tr key={`${split}-${type}`}><td className="p-2">{label}</td><td className="p-2 font-medium">{typeNames[type]}</td><td className="p-2">{score.expected}</td><td className="p-2">{score.detected}</td><td className="p-2">{score.precision === null ? '—' : percent(score.precision)}</td><td className="p-2">{score.recall === null ? '—' : percent(score.recall)}</td><td className="p-2">{score.f1 === null ? '—' : percent(score.f1)}</td><td className="p-2">{score.falseNegative}</td><td className="p-2">{score.falsePositive}</td></tr>;
        }))}</tbody></table>
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
  </section>;
}
function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl bg-surface-container-low p-4"><p className="text-xs text-on-surface/65">{label}</p><p className="mt-2 text-lg font-semibold">{value}</p></div>;
}
