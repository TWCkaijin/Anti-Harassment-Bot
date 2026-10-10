import { evaluateLawRow, formatBatchRate, LAW_MATCH_RULE, scoreLawBatch } from "../services/lawBatchScoring";
import { useEffect, useRef, useState } from "react";
import { getRuntimeConfig } from "../services/api";
import { approveLawBatch, BATCH_STATUS_LABELS, prepareLawBatch, runLawBatch, type BatchRow, type BatchSnapshot } from "../services/applicableLawBatch";
import { columnName, downloadLocalFile, exportScenarioPredictions, MAX_WORKBOOK_BYTES, parseScenarioWorkbook, scenarioColumns, selectScenarioRows, type ScenarioWorkbook } from "../services/scenarioWorkbook";

interface Props { csrfToken: string; onHasDataChange: (hasData: boolean) => void }
const PAGE_SIZE = 20;

export default function AdminBatchPrediction({ csrfToken, onHasDataChange }: Props) {
  const [workbook, setWorkbook] = useState<ScenarioWorkbook | null>(null);
  const [sheetIndex, setSheetIndex] = useState(0);
  const [column, setColumn] = useState(1);
  const [referenceColumn, setReferenceColumn] = useState<number | null>(null);
  const [headerRow, setHeaderRow] = useState(1);
  const [hiddenTerms, setHiddenTerms] = useState("");
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [snapshot, setSnapshot] = useState<BatchSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [error, setError] = useState("");
  const [blockReason, setBlockReason] = useState<string | null>(null);
  const blocked = blockReason === "contract" || blockReason === csrfToken;
  const setBlocked = (value: boolean) => setBlockReason(value ? "contract" : null);
  const [page, setPage] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const pauseRequested = useRef(false);
  const fileVersion = useRef(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.current?.abort(); }; }, []);
  useEffect(() => { onHasDataChange(rows.length > 0); }, [rows.length, onHasDataChange]);
  useEffect(() => {
    if (!rows.length) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [rows.length]);

  const sheet = workbook?.sheets[sheetIndex];
  const columns = sheet ? scenarioColumns(sheet, headerRow) : [];
  let selectedCount = 0, selectionError = "";
  if (sheet) { try { selectedCount = selectScenarioRows(sheet, column, headerRow).length; } catch (failure) { selectionError = failure instanceof Error ? failure.message : "欄位無法讀取。"; } }
  const locked = busy || running || rows.length > 0;
  const completed = rows.filter(row => ["predicted", "insufficient_facts", "no_target_law"].includes(row.status)).length;
  const failed = rows.filter(row => row.status === "error").length;
  const pending = rows.filter(row => row.status === "pending").length;
  const current = rows.find(row => row.status === "running");
  const statistics = scoreLawBatch(rows);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));

  const selectFile = async (file?: File) => {
    if (!file || locked) return;
    const version = ++fileVersion.current;
    setBusy(true); setError(""); setWorkbook(null);
    try {
      if (file.size > MAX_WORKBOOK_BYTES) throw new Error("Excel 檔案需小於 10 MB。");
      const parsed = parseScenarioWorkbook(new Uint8Array(await file.arrayBuffer()), file.name);
      if (!alive.current || version !== fileVersion.current) return;
      setWorkbook(parsed); setSheetIndex(0); setHeaderRow(1);
      const firstColumns = scenarioColumns(parsed.sheets[0], 1);
      setReferenceColumn(firstColumns.find(item => /適用法規|參考答案|正確答案/.test(item.label))?.index ?? null);
      setColumn(firstColumns.find(item => /情境|場景/.test(item.label))?.index ?? firstColumns[0]?.index ?? 0);
    } catch (failure) { if (alive.current) setError(failure instanceof Error ? failure.message : "無法讀取檔案。"); }
    finally { if (alive.current) setBusy(false); }
  };
  const prepare = async () => {
    if (!sheet || locked) return;
    setBusy(true); setError("");
    try {
      const config = await getRuntimeConfig(csrfToken);
      if (referenceColumn === column) throw new Error("情境欄位與參考答案欄位不能相同。");
      const selectedRows = selectScenarioRows(sheet, column, headerRow).map(row => ({ ...row,
        reference: referenceColumn === null ? undefined : sheet.rows.get(row.row)?.get(referenceColumn) ?? "",
      }));
      const batch = prepareLawBatch(selectedRows, config, hiddenTerms.split("\n"));
      // Validate exportability before any paid call, including protected sheets and full columns.
      exportScenarioPredictions(workbook!, sheet, headerRow, [], []);
      if (!alive.current) return;
      setRows(batch.rows); setSnapshot(batch.snapshot); setPage(0); setBlocked(false);
    } catch (failure) { if (alive.current) setError(failure instanceof Error ? failure.message : "無法準備預測。"); }
    finally { if (alive.current) setBusy(false); }
  };
  const run = async (retryFailed = false) => {
    if (controller.current || !snapshot || blocked) return;
    const abort = new AbortController(); controller.current = abort; pauseRequested.current = false;
    setRunning(true); setPausing(false); setError("");
    try {
      const approved = approveLawBatch(rows);
      setRows(approved);
      const result = await runLawBatch({ rows: approved, snapshot, token: csrfToken, signal: abort.signal,
        shouldPause: () => pauseRequested.current,
        onUpdate: next => { if (alive.current) setRows(next); }, retryFailed });
      if (alive.current && result.stoppedReason) {
        setError(result.stoppedReason);
        if (result.stoppedReason.includes("契約已變更")) setBlocked(true);
        else if (result.stoppedReason.includes("驗證失效")) setBlockReason(csrfToken);
      }
    } catch (failure) { if (alive.current) setError(failure instanceof Error ? failure.message : "預測失敗。"); }
    finally { controller.current = null; if (alive.current) { setRunning(false); setPausing(false); } }
  };
  const download = () => {
    if (!workbook || !sheet) return;
    try {
      const bytes = exportScenarioPredictions(workbook, sheet, headerRow, rows.map(row => ({ row: row.row,
        evaluation: evaluateLawRow(row).result, matchedLaws: evaluateLawRow(row).matches.join("、"),
        prediction: row.laws.join("、"), status: BATCH_STATUS_LABELS[row.status], reason: row.error ?? row.reason ?? "",
        elapsedSeconds: row.elapsedMs === null ? null : Math.round(row.elapsedMs / 10) / 100 })), [
        ["統計狀態", statistics.pending ? "暫時計算" : "已完成"],
        ["工作表", sheet.name], ["標題列", headerRow], ["情境欄位", columnName(column)],
        ["參考答案欄位", referenceColumn === null ? "未設定參考答案" : columnName(referenceColumn)],
        ["比對規則", LAW_MATCH_RULE], ["模型", snapshot?.model ?? ""], ["契約指紋", snapshot?.contractSha256 ?? ""],
        ["指標", "百分比", "分子", "分母"],
        ...statistics.metrics.map(metric => [metric.label, formatBatchRate(metric), metric.numerator, metric.denominator]),
        ...Object.entries(statistics).filter(([key]) => key !== "metrics").map(([key, value]) => [key, Number(value)]),
        ...Object.entries(snapshot?.overrides ?? {}).flatMap(([key, value]) => {
          const text = JSON.stringify(value);
          return Array.from({ length: Math.ceil(text.length / 30000) || 1 }, (_, index) => [`設定 ${key} (${index + 1})`, text.slice(index * 30000, (index + 1) * 30000)]);
        }),
      ]);
      downloadLocalFile(bytes, workbook.fileName.replace(/\.xlsx$/i, ".predicted.xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "下載失敗。"); }
  };
  const downloadPreview = () => downloadLocalFile(rows.map(row => JSON.stringify({ row: row.row, request: row.draft.request })).join("\n"), "prediction-preview.jsonl", "application/x-ndjson;charset=utf-8");
  const reset = () => {
    if (running || !window.confirm("清除本次預測？尚未下載的結果將消失。")) return;
    setRows([]); setSnapshot(null); setPage(0); setError(""); setBlocked(false);
  };
  const buttonClass = "min-h-11 rounded-lg border border-outline/25 px-4 py-2 text-sm font-semibold disabled:opacity-40";

  return <section aria-label="批次適用法規預測" className="mx-auto max-w-5xl space-y-6">
    <div><h3 className="text-xl font-bold">批次適用法規預測</h3>
      <p className="mt-2 text-sm leading-relaxed text-on-surface/65">選擇 Excel 與情境欄位，逐筆預測並下載結果。檔案在瀏覽器讀取，只有選定欄位的遮罩後情境會送出。</p></div>
    <fieldset disabled={locked} className="space-y-4 disabled:opacity-60">
      <label className="block text-sm font-semibold">Excel 檔案<input aria-label="Excel 檔案" type="file" accept=".xlsx" onChange={event => void selectFile(event.target.files?.[0])} className="mt-2 block w-full rounded-lg border border-dashed border-outline/35 p-4 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-surface-container file:px-3 file:py-2" /></label>
      <p className="text-xs text-on-surface/60">支援 .xlsx，最多 10 MB、5,000 筆情境；每筆最多 2,000 字。</p>
      {workbook && <div className="grid gap-4 sm:grid-cols-3">
        <label className="text-sm font-medium">工作表<select aria-label="工作表" className="input mt-2" value={sheetIndex} onChange={event => { const index = Number(event.target.value); setSheetIndex(index); setHeaderRow(1); const next = scenarioColumns(workbook.sheets[index], 1); setColumn(next.find(item => /情境|場景/.test(item.label))?.index ?? next[0]?.index ?? 0); setReferenceColumn(next.find(item => /適用法規|參考答案|正確答案/.test(item.label))?.index ?? null); }}>
          {workbook.sheets.map((item, index) => <option key={item.path} value={index}>{item.name}</option>)}
        </select></label>
        <label className="text-sm font-medium">標題列<input aria-label="標題列" type="number" min={1} max={sheet?.maxRow} value={headerRow} onChange={event => setHeaderRow(Number(event.target.value))} className="input mt-2" /></label>
        <label className="text-sm font-medium">情境欄位<select aria-label="情境欄位" value={column} onChange={event => setColumn(Number(event.target.value))} className="input mt-2">{columns.map(item => <option key={item.index} value={item.index}>{item.label}</option>)}</select></label>
      </div>}
      {sheet && <label className="block text-sm font-medium">參考答案欄位<select aria-label="參考答案欄位" value={referenceColumn ?? ""} onChange={event => setReferenceColumn(event.target.value === "" ? null : Number(event.target.value))} className="input mt-2"><option value="">不設定（只預測）</option>{columns.filter(item => item.index !== column).map(item => <option key={item.index} value={item.index}>{item.label}</option>)}</select></label>}
      <p className="text-xs leading-relaxed text-on-surface/65">{LAW_MATCH_RULE} 參考答案只在瀏覽器本機比對，不傳給模型。</p>
      {sheet && <p className="text-sm text-on-surface/70">{selectedCount} 筆非空情境 · 將在右側 {columnName(sheet.maxColumn + 1)}–{columnName(sheet.maxColumn + 5)} 欄新增預測、狀態、耗時、比對結果與命中法規，另附統計工作表。</p>}
      <details><summary className="cursor-pointer text-sm font-medium">自訂隱藏詞</summary><label className="mt-2 block text-xs text-on-surface/65">姓名、單位或其他需隱藏的文字，每行一項<textarea aria-label="自訂隱藏詞" value={hiddenTerms} onChange={event => setHiddenTerms(event.target.value)} rows={3} className="input mt-2" /></label></details>
    </fieldset>
    {!rows.length && <button type="button" disabled={busy || !selectedCount || !!selectionError} onClick={() => void prepare()} className={`${buttonClass} bg-primary text-white`}>{busy ? "讀取中…" : "準備預測"}</button>}
    {(error || selectionError) && <p role="alert" className="text-sm text-error">{error || selectionError}</p>}
    {rows.length > 0 && snapshot && <>
      <div className="space-y-3 rounded-xl border border-outline/20 bg-surface-container-low p-4">
        <p className="break-all text-sm font-semibold">模型：{snapshot.model} · 經 OpenRouter 執行</p>
        <p className="text-sm leading-relaxed text-on-surface/65">請檢查下方完整送出情境，可翻頁或下載全部預覽。開始後會產生模型費用；自動遮罩可能漏掉姓名、單位及事件細節。需要修正時，清除本次預測後調整隱藏詞或原檔。</p>
        <p className="text-xs text-on-surface/60">逐筆獨立預測，不附上原始法規標籤或歷史對話。請保持此頁開啟；切換後台頁籤會保留進度，關閉或重新整理會清除。</p>
        <div className="flex flex-wrap gap-2">
          <button className={buttonClass} onClick={downloadPreview}>下載送出預覽</button>
          <button className={buttonClass} onClick={download}>下載結果 Excel</button>
          <button disabled={running} className={buttonClass} onClick={reset}>清除本次預測</button>
        </div>
      </div>
      <div aria-live="polite" className="space-y-2">
        <p className="text-sm font-medium">完成 {completed} / {rows.length} · 失敗 {failed} · 待預測 {pending}{current ? ` · 正在處理第 ${current.row} 列` : ""}</p>
        <progress aria-label="預測進度" className="h-2 w-full accent-primary" max={rows.length} value={completed + failed} />
      </div>
      <div className="flex flex-wrap gap-2">
        {running ? <button className={buttonClass} disabled={pausing} onClick={() => { pauseRequested.current = true; setPausing(true); }}>{pausing ? "等待本筆完成後暫停…" : "完成本筆後暫停"}</button>
          : <>
            <button className={`${buttonClass} bg-primary text-white`} disabled={!pending || blocked} onClick={() => void run()}>{!pending ? (failed ? "沒有待預測列" : "已完成本次預測") : completed + failed > 0 ? "繼續未完成預測" : "確認送出並開始預測"}</button>
            {failed > 0 && <button className={buttonClass} disabled={blocked} onClick={() => void run(true)}>僅重試失敗 {failed} 筆（會再次呼叫模型）</button>}
          </>}
      </div>
      <section aria-label="批次預測統計" className="space-y-3">
        <h4 className="font-semibold">{statistics.pending ? "暫時計算" : "預測統計"} · 未完成 {statistics.pending} 筆</h4>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">{statistics.metrics.map(metric => <div key={metric.label} className="rounded-lg border border-outline/20 p-3"><p className="text-sm">{metric.label}</p><p className="mt-2 text-xl font-semibold">{referenceColumn === null && metric.label.includes("正確率") ? "未設定參考答案" : formatBatchRate(metric)}</p><p className="text-xs text-on-surface/60">{metric.numerator} / {metric.denominator}</p></div>)}</div>
        <p className="text-xs leading-relaxed text-on-surface/65">資訊不足 {statistics.insufficient} · 不適用三法 {statistics.noTarget} · 參考答案空白 {statistics.referenceMissing} · 無法識別 {statistics.referenceInvalid}。總正確率包含有有效參考答案的未回答與失敗列；回答正確率僅計入已給出法規且有有效參考答案的列。</p>
      </section>
      <div className="overflow-x-auto rounded-lg border border-outline/20">
        <table className="w-full min-w-[640px] table-fixed text-left text-sm"><caption className="sr-only">送出情境與預測結果</caption>
          <thead className="bg-surface-container"><tr><th className="w-14 p-3">列</th><th className="p-3">完整送出情境</th><th className="w-36 p-3">參考答案</th><th className="w-40 p-3">預測與比對結果</th></tr></thead>
          <tbody>{rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(row => <tr key={row.row} className="border-t border-outline/15 align-top">
            <td className="p-3">{row.row}</td><td className="whitespace-pre-wrap break-words p-3 leading-relaxed">{row.draft.request.message}</td>
            <td className="break-words whitespace-pre-wrap p-3">{row.reference?.raw || "—"}</td>
            <td className="break-words p-3"><p className="font-medium">{row.laws.join("、") || "—"}</p><p className={`mt-1 text-xs ${row.status === "error" ? "text-error" : "text-on-surface/60"}`}>{row.error ?? BATCH_STATUS_LABELS[row.status]}</p>{row.reason && <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">無法預測原因：{row.reason}</p>}{row.elapsedMs !== null && <p className="mt-1 text-xs text-on-surface/50">{(row.elapsedMs / 1000).toFixed(1)} 秒</p>}<p className="mt-2 text-xs">{evaluateLawRow(row).result}</p>{evaluateLawRow(row).matches.length > 0 && <p className="mt-1 text-xs">命中：{evaluateLawRow(row).matches.join("、")}</p>}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="flex items-center justify-between gap-3"><button className={buttonClass} disabled={page === 0} onClick={() => setPage(value => value - 1)}>上一頁</button><span className="text-sm">第 {page + 1} / {pages} 頁</span><button className={buttonClass} disabled={page + 1 >= pages} onClick={() => setPage(value => value + 1)}>下一頁</button></div>
    </>}
  </section>;
}
