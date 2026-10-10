import { useEffect, useRef, useState } from "react";
import { getRuntimeConfig } from "../services/api";
import { createPrivacyDraft } from "../services/clientPrivacy";
import { BEHAVIOR_STATUS, REVIEW_LABELS, builtinBehaviorCases, importBehaviorCases, prepareBehaviorRun, validateBehaviorCases, type BehaviorCase, type BehaviorRecord, type BehaviorReview, type BehaviorRun } from "../services/behaviorTests";
import { MAX_BEHAVIOR_JSON_BYTES, behaviorStatistics, currentBehaviorReview, exportBehaviorJson, importBehaviorRun, refreshBehaviorEvidence } from "../services/behaviorRecords";
import { runBehaviorTests } from "../services/behaviorRunner";
import { exportBehaviorWorkbook } from "../services/behaviorWorkbook";
import { MAX_WORKBOOK_BYTES, downloadLocalFile, parseScenarioWorkbook } from "../services/scenarioWorkbook";

interface Props { csrfToken: string; onHasDataChange: (hasData: boolean) => void }
const button = "min-h-11 rounded-lg border border-outline/25 px-3 py-2 text-sm font-semibold disabled:opacity-40";
const primary = `${button} bg-primary text-white`;

export default function AdminBehaviorTests({ csrfToken, onHasDataChange }: Props) {
  const [cases, setCases] = useState(builtinBehaviorCases);
  const [repetitions, setRepetitions] = useState<1 | 3>(1);
  const [run, setRun] = useState<BehaviorRun | null>(null);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState("");
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [blockReason, setBlockReason] = useState<string | null>(null);
  const blocked = blockReason === "contract" || blockReason === csrfToken;
  const setBlocked = (value: boolean) => setBlockReason(value ? "contract" : null);
  const [edited, setEdited] = useState(false);
  const [snapshotConfirmed, setSnapshotConfirmed] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const pauseRequested = useRef(false);
  const alive = useRef(true);
  const hasData = !!run || edited;
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.current?.abort(); }; }, []);
  useEffect(() => { onHasDataChange(hasData); }, [hasData, onHasDataChange]);
  useEffect(() => {
    if (!hasData) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard); return () => window.removeEventListener("beforeunload", guard);
  }, [hasData]);
  const locked = busy || running;
  const reportError = (failure: unknown) => { if (alive.current) setError(failure instanceof Error ? failure.message : "無法完成操作。"); };
  const replaceAllowed = () => !hasData || window.confirm("載入其他套件會清除目前頁面的資料。請先下載紀錄；確定繼續？");
  const editCase = (index: number, updates: Partial<BehaviorCase>) => {
    setCases(previous => previous.map((test, i) => i === index ? { ...test, ...updates, confirmed: updates.confirmed ?? false } : test)); setEdited(true); setError("");
  };
  const loadFile = async (file: File | undefined, kind: "xlsx" | "json") => {
    if (!file || locked) return;
    setBusy(true); setError("");
    try {
      if (file.size > (kind === "xlsx" ? MAX_WORKBOOK_BYTES : MAX_BEHAVIOR_JSON_BYTES)) throw new Error(`檔案不可超過 ${kind === "xlsx" ? 10 : 20} MB。`);
      const imported = kind === "json" ? await importBehaviorRun(await file.text()) : importBehaviorCases(parseScenarioWorkbook(new Uint8Array(await file.arrayBuffer()), file.name));
      if (!alive.current || !replaceAllowed()) return;
      setRun(kind === "json" ? imported as BehaviorRun : null);
      setCases(kind === "json" ? (imported as BehaviorRun).cases : imported as BehaviorCase[]);
      setRepetitions(kind === "json" ? (imported as BehaviorRun).repetitions : 1);
      setEdited(true); setSelected(0); setBlocked(false); setSnapshotConfirmed(false);
      setActivity(kind === "json" ? "已離線載入紀錄；尚未發出任何測試請求。續跑前請重新確認設定。" : "已匯入草稿；請補齊逐輪腳本並逐項確認。原表的結果不會冒充本次覆核。");
    } catch (failure) { reportError(failure); }
    finally { if (alive.current) setBusy(false); }
  };
  const prepare = async () => {
    if (locked) return;
    setBusy(true); setError("");
    try {
      validateBehaviorCases(cases);
      const config = await getRuntimeConfig(csrfToken);
      const prepared = await refreshBehaviorEvidence(prepareBehaviorRun(cases, repetitions, config));
      if (alive.current) { setRun(prepared); setSelected(0); setSnapshotConfirmed(false); setActivity("設定已固定，請檢查腳本預覽後開始。尚未呼叫模型。"); }
    } catch (failure) { reportError(failure); }
    finally { if (alive.current) setBusy(false); }
  };
  const start = async (retryFailed = false) => {
    if (!run || controller.current || locked || blocked || !snapshotConfirmed) return;
    const abort = new AbortController(); controller.current = abort; pauseRequested.current = false;
    setRunning(true); setPausing(false); setError("");
    try {
      const current = await getRuntimeConfig(csrfToken);
      if (abort.signal.aborted || !alive.current) return;
      if (current.chat_test_contract_sha256 !== run.contractSha256) { setBlocked(true); throw new Error("對話測試契約已變更，請下載紀錄並重新準備套件。"); }
      const result = await runBehaviorTests({ run, token: csrfToken, signal: abort.signal, shouldPause: () => pauseRequested.current, retryFailed,
        onUpdate: next => { if (alive.current) setRun(next); }, onActivity: text => { if (alive.current) setActivity(text); } });
      if (alive.current) {
        setRun(result.run);
        setActivity(result.run.records.every(record => record.status === "completed") ? "已完成所有對話請求，請逐項人工覆核。" : "已停止送出；可檢查紀錄、續跑未完成項目或手動重試錯誤。");
        if (result.stoppedReason) { setError(result.stoppedReason); if (result.stoppedReason.includes("契約已變更")) setBlocked(true); else if (result.stoppedReason.includes("驗證失效")) setBlockReason(csrfToken); }
      }
    } catch (failure) { reportError(failure); }
    finally { controller.current = null; if (alive.current) { setRunning(false); setPausing(false); } }
  };
  const saveReview = async (review: Omit<BehaviorReview, "evidenceSha256" | "reviewedAt">) => {
    if (!run || locked) return;
    setBusy(true); setError("");
    try {
      const refreshed = await refreshBehaviorEvidence(run);
      const updated = { ...review, evidenceSha256: refreshed.records[selected].evidenceSha256, reviewedAt: new Date().toISOString() };
      if (alive.current) setRun({ ...refreshed, records: refreshed.records.map((record, index) => index === selected ? { ...record, review: updated } : record) });
    } catch (failure) { reportError(failure); }
    finally { if (alive.current) setBusy(false); }
  };
  const download = (kind: "json" | "xlsx") => {
    if (!run) return;
    try {
      const name = `behavior-tests-${run.runId}`;
      downloadLocalFile(kind === "json" ? exportBehaviorJson(run) : exportBehaviorWorkbook(run), `${name}.${kind}`, kind === "json" ? "application/json" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    } catch (failure) { reportError(failure); }
  };
  const stats = run && behaviorStatistics(run);
  const record = run?.records[selected];
  const test = run?.cases.find(test => test.id === record?.caseId);
  return <section aria-label="對話行為測試" className="mx-auto max-w-5xl space-y-5">
    <div><h3 className="text-xl font-bold">對話行為測試</h3><p className="mt-2 text-sm leading-relaxed text-on-surface/65">測試服務範圍、幻覺、情境更新、提示注入及即時安全。規則提供證據，Pass／Fail 由管理員覆核。</p></div>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm font-medium">匯入測試表 Excel<input aria-label="匯入測試表 Excel" type="file" accept=".xlsx" disabled={locked} onChange={event => { void loadFile(event.target.files?.[0], "xlsx"); event.target.value = ""; }} className="mt-2 block w-full min-w-0 text-xs" /></label>
      <label className="text-sm font-medium">匯入執行紀錄 JSON<input aria-label="匯入執行紀錄 JSON" type="file" accept=".json" disabled={locked} onChange={event => { void loadFile(event.target.files?.[0], "json"); event.target.value = ""; }} className="mt-2 block w-full min-w-0 text-xs" /></label>
    </div>
    <p className="text-xs text-on-surface/60">紀錄只留在本頁記憶體。請下載 JSON 保存與續跑；Excel 用於檢視報告。匯入不會自動呼叫模型。</p>
    {error && <p role="alert" className="whitespace-pre-wrap break-words rounded-lg bg-error/10 p-3 text-sm text-error">{error}</p>}
    {activity && <p role="status" className="text-sm text-on-surface/70">{activity}</p>}
    {!run ? <>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">每案重複次數<select aria-label="每案重複次數" className="input mt-1" value={repetitions} disabled={locked} onChange={event => setRepetitions(Number(event.target.value) as 1 | 3)}><option value={1}>1 次（基本回歸）</option><option value={3}>3 次（穩定性檢查）</option></select></label>
        <button className={button} disabled={locked} onClick={() => { if (replaceAllowed()) { setCases(builtinBehaviorCases()); setEdited(false); setError(""); } }}>載入內建五項</button>
      </div>
      <p className="text-sm">共 {cases.length} 個案例 · {cases.reduce((sum, test) => sum + test.turns.length, 0) * repetitions} 個預定對話請求。每個請求可能包含多次模型呼叫。</p>
      <div className="space-y-3">{cases.map((test, index) => <details key={index} open={cases.length <= 5} className="rounded-xl border border-outline/20 p-4">
        <summary className="cursor-pointer text-sm font-semibold">{test.id} · {test.category} · {test.confirmed ? "已確認" : "草稿待確認"}</summary>
        <fieldset disabled={locked} className="mt-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2"><label className="text-xs">案例 ID<input aria-label={`案例 ID ${index + 1}`} className="input mt-1" maxLength={80} value={test.id} onChange={event => editCase(index, { id: event.target.value })} /></label><label className="text-xs">測試類型<input aria-label={`測試類型 ${index + 1}`} className="input mt-1" maxLength={100} value={test.category} onChange={event => editCase(index, { category: event.target.value })} /></label></div>
          <p className="break-words text-xs text-on-surface/60">來源：{test.source.file}／{test.source.sheet}／第 {test.source.row} 列<br />原始描述：{test.originalPrompt}</p>
          {test.turns.map((message, turnIndex) => <div key={turnIndex} className="space-y-1"><label className="block text-sm">第 {turnIndex + 1} 輪使用者訊息<textarea aria-label={`${test.id} 第 ${turnIndex + 1} 輪`} rows={3} maxLength={2000} value={message} onChange={event => editCase(index, { turns: test.turns.map((text, i) => i === turnIndex ? event.target.value : text) })} className="input mt-1 resize-y" /></label>{test.turns.length > 1 && <button className="min-h-9 text-xs text-error" onClick={() => editCase(index, { turns: test.turns.filter((_, i) => i !== turnIndex) })}>刪除第 {turnIndex + 1} 輪</button>}</div>)}
          <button disabled={test.turns.length >= 10} className={button} onClick={() => editCase(index, { turns: [...test.turns, ""] })}>新增一輪</button>
          <label className="block text-sm">Expected behavior<textarea aria-label={`${test.id} 預期行為`} rows={2} maxLength={4000} value={test.expected} onChange={event => editCase(index, { expected: event.target.value })} className="input mt-1" /></label>
          <label className="block text-sm">Critical failure<textarea aria-label={`${test.id} 重大失敗條件`} rows={2} maxLength={4000} value={test.criticalFailure} onChange={event => editCase(index, { criticalFailure: event.target.value })} className="input mt-1" /></label>
          <label className="flex min-h-11 items-center gap-3 text-sm font-medium"><input type="checkbox" aria-label={`確認 ${test.id} 腳本`} checked={test.confirmed} onChange={event => editCase(index, { confirmed: event.target.checked })} className="h-5 w-5 accent-primary" />已確認這份完整腳本與評分條件</label>
        </fieldset>
      </details>)}</div>
      <button className={primary} disabled={locked || !cases.every(test => test.confirmed)} onClick={() => void prepare()}>{busy ? "準備中…" : "準備對話測試"}</button>
    </> : <>
      <div className="space-y-2 rounded-xl border border-outline/20 bg-surface-container-low p-4 text-sm">
        <p>環境：{run.environment} · 模型：{run.overrides.openrouter_model}</p>
        <p>固定 {run.cases.length} 案 × {run.repetitions} 次 · 資料庫版本：未知</p>
        <details><summary className="cursor-pointer">腳本與設定預覽</summary><p className="my-2 text-xs">以下是固定的使用者腳本；多輪另承接真實歷史及摘要，每輪依設定重新遮罩與確認。預期行為、重大失敗條件不送給模型。</p>
          {run.cases.map(test => <div key={test.id} className="my-3"><p className="font-medium">{test.id}</p>{test.turns.map((message, i) => <pre key={i} className="mt-1 whitespace-pre-wrap break-words text-xs">{i + 1}. {createPrivacyDraft({ message, history: [], use_rag: true, contract_version: 4 }).request.message}</pre>)}</div>)}
          <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({ contractSha256: run.contractSha256, settings: run.overrides }, null, 2)}</pre>
        </details>
        <label className="flex min-h-11 items-center gap-3"><input type="checkbox" checked={snapshotConfirmed} disabled={locked} onChange={event => setSnapshotConfirmed(event.target.checked)} className="h-5 w-5 shrink-0 accent-primary" />已確認本次設定與腳本，允許開始或續跑測試</label>
      </div>
      <div className="flex flex-wrap gap-2">
        {!running && <button className={primary} disabled={busy || blocked || !snapshotConfirmed || !run.records.some(record => ["pending", "paused"].includes(record.status))} onClick={() => void start()}>{run.records.some(record => record.turns.length) ? "繼續未完成測試" : "確認送出並開始對話測試"}</button>}
        {!running && run.records.some(record => ["error", "interrupted"].includes(record.status)) && <button className={button} disabled={busy || blocked || !snapshotConfirmed} onClick={() => void start(true)}>手動重試操作錯誤</button>}
        {running && <><button className={button} disabled={pausing} onClick={() => { pauseRequested.current = true; setPausing(true); }}>{pausing ? "等待本輪完成後暫停…" : "完成本輪後暫停"}</button><button className={button} onClick={() => controller.current?.abort()}>中斷本次請求</button></>}
        <button className={button} disabled={busy} onClick={() => download("json")}>下載完整 JSON</button><button className={button} disabled={busy} onClick={() => download("xlsx")}>下載測試報告 Excel</button>
        <button className={button} disabled={locked} onClick={() => { if (replaceAllowed()) { setRun(null); setCases(previous => previous.map(test => ({ ...test, confirmed: false }))); setSnapshotConfirmed(false); setBlocked(false); setError(""); setActivity(""); } }}>另開一套測試</button>
      </div>
      {stats && <section aria-label="對話測試統計" className="space-y-3">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">{[
          ["已覆核可判定通過率", stats.passRate === null ? "—" : `${(stats.passRate * 100).toFixed(1)}%`, `${stats.pass} / ${stats.pass + stats.fail}（通過／通過＋失敗）`],
          ["重大失敗", stats.critical, `${stats.critical} / ${stats.reviewed} 已覆核`], ["待覆核", stats.unreviewed, `${stats.unreviewed} / ${stats.completed} 已完成`],
          ["無法判定", stats.indeterminate, `${stats.indeterminate} / ${stats.reviewed} 已覆核`], ["操作錯誤", stats.operationErrors, `${stats.operationErrors} / ${stats.total} 案例測次`], ["已完成", stats.completed, `${stats.completed} / ${stats.total} 案例測次`],
        ].map(([label, value, detail]) => <div key={label} className="rounded-xl border border-outline/15 p-3"><p className="text-xs text-on-surface/60">{label}</p><p className="my-1 text-xl font-bold">{value}</p><p className="text-xs text-on-surface/60">{detail}</p></div>)}</div>
        <p className="text-xs leading-relaxed text-on-surface/65">已發出 {stats.requests} 個請求（含重試）；原定 {stats.plannedRequests} 個。已知模型呼叫 {stats.modelCalls} 次，另有 {stats.unknownModelCalls} 個請求的呼叫數未知。未執行、待覆核與無法判定不計入可判定通過率。</p>
      </section>}
      <label className="block text-sm font-medium">檢視案例測次<select aria-label="檢視案例測次" className="input mt-2" value={selected} onChange={event => setSelected(Number(event.target.value))}>{run.records.map((record, index) => <option key={`${record.caseId}:${record.repetition}`} value={index}>{record.caseId} · 測次 {record.repetition} · {BEHAVIOR_STATUS[record.status]} · {currentBehaviorReview(record) ? REVIEW_LABELS[currentBehaviorReview(record)!.verdict] : "待覆核"}</option>)}</select></label>
      {record && test && <article className="space-y-4 rounded-xl border border-outline/20 p-4">
        <h4 className="font-bold">{test.id} · {test.category} · 測次 {record.repetition}</h4>
        <p className="text-sm">預期行為：{test.expected}</p><p className="text-sm">重大失敗條件：{test.criticalFailure}</p>
        <p className="text-sm font-medium">執行狀態：{BEHAVIOR_STATUS[record.status]}</p>
        {record.turns.map((turn, index) => <details key={`${turn.number}:${turn.attempt}`} open={index === record.turns.length - 1} className="rounded-lg border border-outline/15 p-3">
          <summary className="cursor-pointer text-sm font-semibold">第 {turn.number} 輪 · 嘗試 {turn.attempt} · {BEHAVIOR_STATUS[turn.status]}{turn.elapsedMs !== null ? ` · ${(turn.elapsedMs / 1000).toFixed(1)} 秒` : ""}</summary>
          <div className="mt-3 space-y-3 text-sm"><p className="whitespace-pre-wrap break-words">實際送出：{turn.request.message}</p>
            {turn.result && <div><p className="mb-1 font-semibold">完整回覆</p><p className="whitespace-pre-wrap break-words leading-relaxed">{turn.result.response.reply}</p></div>}
            {turn.error && <p className="text-error">{turn.error}</p>}
            {turn.issues.map((issue, i) => <div key={i} className={`rounded-lg p-3 text-xs ${issue.severity === "warning" ? "bg-error/10" : "bg-surface-container-low"}`}><p>{issue.text}</p><pre className="mt-1 whitespace-pre-wrap break-words">{issue.evidence}</pre></div>)}
            <details><summary className="cursor-pointer text-xs">歷史、摘要與引用證據</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({ history: turn.request.history, contextBefore: turn.request.case_context, summaryUpdate: turn.result?.response.summary_update, contextAfter: turn.contextAfter, sources: turn.result?.response.rag_used.sources }, null, 2)}</pre></details>
            {turn.result && <details><summary className="cursor-pointer text-xs">完整診斷</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(turn.result.diagnostics, null, 2)}</pre></details>}
          </div>
        </details>)}
        <ReviewForm key={`${record.caseId}:${record.repetition}:${record.evidenceSha256}:${record.review?.reviewedAt ?? ""}`} record={record} disabled={locked} onSave={saveReview} />
      </article>}
    </>}
  </section>;
}

function ReviewForm({ record, disabled, onSave }: { record: BehaviorRecord; disabled: boolean; onSave: (review: Omit<BehaviorReview, "evidenceSha256" | "reviewedAt">) => Promise<void> }) {
  const saved = currentBehaviorReview(record);
  const [verdict, setVerdict] = useState<BehaviorReview["verdict"] | "">(saved?.verdict ?? "");
  const [critical, setCritical] = useState(saved?.critical ?? false);
  const [reason, setReason] = useState(saved?.reason ?? "");
  const [reviewer, setReviewer] = useState(saved?.reviewer ?? "");
  if (record.status !== "completed") return <p className="text-sm text-on-surface/60">完成此案例的所有輪次後，才能做整體人工判分。</p>;
  return <fieldset disabled={disabled} className="space-y-3 border-t border-outline/15 pt-4">
    <legend className="text-sm font-semibold">人工覆核</legend>
    {record.review && !saved && <p className="text-sm text-error">腳本、設定或證據已變更，原覆核失效，請重新判分。</p>}
    {saved && <p className="text-sm text-primary">已保存：{REVIEW_LABELS[saved.verdict]} · {saved.reviewer} · {new Date(saved.reviewedAt).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</p>}
    <label className="block text-sm">人工結論<select aria-label="人工結論" value={verdict} onChange={event => { setVerdict(event.target.value as typeof verdict); if (event.target.value !== "fail") setCritical(false); }} className="input mt-1"><option value="">請選擇</option><option value="pass">通過</option><option value="fail">失敗</option><option value="indeterminate">無法判定</option></select></label>
    <label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={critical} disabled={verdict !== "fail"} onChange={event => setCritical(event.target.checked)} className="h-5 w-5 accent-primary" />重大失敗（限失敗結論）</label>
    <label className="block text-sm">覆核理由<textarea aria-label="覆核理由" rows={3} maxLength={4000} value={reason} onChange={event => setReason(event.target.value)} className="input mt-1" placeholder="指出對應輪次與回覆證據" /></label>
    <label className="block text-sm">覆核者<input aria-label="覆核者" maxLength={100} value={reviewer} onChange={event => setReviewer(event.target.value)} className="input mt-1" /></label>
    <button className={primary} disabled={!verdict || !reason.trim() || !reviewer.trim()} onClick={() => { if (verdict) void onSave({ verdict, critical, reason: reason.trim(), reviewer: reviewer.trim() }); }}>保存人工覆核</button>
  </fieldset>;
}
