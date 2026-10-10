import { useEffect, useState } from "react";
import { ApiError } from "../services/api";
import { listImports, mappingLabels, type ColumnMapping, type ImportDraft } from "../services/corpus";

const statusLabels: Record<string, string> = { parse_queued: "排隊解析", parsing: "解析中", parse_failed: "解析失敗", mapping_required: "等待欄位對應", preview: "等待預覽確認", importing: "匯入中", confirmed: "已確認匯入", active: "已匯入", succeeded: "已完成", failed: "失敗", expired: "已過期" };
export function CorpusDrafts({ refreshKey, onOpen }: { refreshKey: number; onOpen: (id: string) => void }) {
  const [items, setItems] = useState<ImportDraft[]>([]);
  const [pages, setPages] = useState<Array<string | null>>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const cursor = pages.at(-1) ?? null;
  useEffect(() => {
    const abort = new AbortController();
    void listImports(cursor, abort.signal).then(page => { if (!abort.signal.aborted) { setItems(page.items); setNextCursor(page.next_cursor); setError(""); } }).catch(failure => { if (!abort.signal.aborted) setError(failure instanceof ApiError ? failure.detail ?? failure.message : "無法載入匯入草稿"); });
    return () => abort.abort();
  }, [cursor, refresh, refreshKey]);
  return <section aria-label="可恢復的匯入草稿" className="space-y-3 rounded-lg border border-outline/30 p-4">
    <div className="flex flex-wrap justify-between gap-2"><h4 className="font-bold">可恢復的匯入草稿</h4><button className="text-sm text-primary underline" onClick={() => setRefresh(value => value + 1)}>重新整理草稿</button></div>
    <p className="text-xs text-on-surface/65">解析完成後，重新登入仍可開啟尚未到期的欄位對應與預覽。</p>
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    {items.map(draft => <button key={draft.import_id} onClick={() => onOpen(draft.import_id)} className="block w-full rounded border border-outline/20 p-3 text-left text-sm"><span className="block break-words font-medium">{draft.title || draft.filename}</span><span className="text-xs text-on-surface/65">{statusLabels[draft.status] ?? draft.status} · {draft.filename}</span></button>)}
    {!items.length && <p className="text-sm text-on-surface/60">沒有可恢復的匯入草稿。</p>}
    <div className="flex gap-3 text-sm"><button disabled={pages.length < 2} className="text-primary disabled:opacity-40" onClick={() => setPages(value => value.slice(0, -1))}>上一頁草稿</button><button disabled={!nextCursor} className="text-primary disabled:opacity-40" onClick={() => setPages(value => [...value, nextCursor])}>下一頁草稿</button></div>
  </section>;
}

export function CorpusMapping({ draft, busy, onSubmit }: { draft: ImportDraft; busy: boolean; onSubmit: (mapping: ColumnMapping) => void }) {
  const [mapping, setMapping] = useState<ColumnMapping>(draft.column_mapping ?? draft.suggested_mapping ?? {});
  const fields = draft.available_fields ?? [];
  return <section aria-label="結構化資料欄位對應" className="space-y-4 rounded-lg border border-primary/30 p-4">
    <div><h4 className="font-bold">{draft.title || draft.filename} · 欄位對應</h4><p className="mt-2 text-sm text-on-surface/65">選擇用來建立片段內容的欄位，再指定需要保留的來源資訊。請先核對下方樣本。</p></div>
    <div className="grid gap-3 sm:grid-cols-2">{(Object.entries(mappingLabels) as Array<[keyof ColumnMapping, string]>).map(([target, label]) => <label key={target} className="text-sm">{label}<select aria-label={label} className="input mt-1 w-full" value={mapping[target] ?? ""} disabled={busy} onChange={event => setMapping(previous => { const next = { ...previous }; if (event.target.value) next[target] = event.target.value; else delete next[target]; return next; })}><option value="">{target === "content" ? "請選擇內容欄位" : "不對應"}</option>{fields.map(field => <option key={field} value={field}>{field}</option>)}</select></label>)}</div>
    <details open><summary className="cursor-pointer text-sm font-medium">資料樣本（最多 3 列，長內容截短）</summary><div className="mt-2 space-y-2">{draft.sample_rows?.map((row, index) => <pre key={index} className="whitespace-pre-wrap break-words rounded bg-surface-container-low p-3 text-xs">{JSON.stringify(row, null, 2)}</pre>)}</div></details>
    <button disabled={busy || !mapping.content} onClick={() => onSubmit(mapping)} className="rounded-lg border border-primary px-4 py-2 text-sm font-semibold text-primary disabled:opacity-40">確認欄位並產生預覽</button><p className="text-xs text-on-surface/65">這一步只解析與建立預覽；檢查全部片段並再次確認後才會呼叫 embedding。</p>
  </section>;
}
