import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPrivacyDraft } from '@rag/shared/clientPrivacy';
import { getRerankModels, type RuntimeConfig } from '../services/api';
import { cancelCorpusJob, retryCorpusJob, getSource, type CorpusRecord, type CorpusSource } from '../services/corpus';
import * as api from '../services/rerankTests';

const kinds: api.Kind[] = ['law', 'judgment', 'remedy'];
const stages: api.Stage[] = ['candidate', 'before', 'model', 'final'];
const button = 'rounded-lg border border-outline/25 px-3 py-2 text-sm disabled:opacity-40';
const panel = 'space-y-3 rounded-xl border border-outline/20 p-4';
const running = (status: string) => ['queued', 'running', 'cancel_requested'].includes(status);
function Field({ title, children }: { title: string; children: ReactNode }) { return <label className="block text-sm"><span className="block font-semibold">{title}</span>{children}</label>; }
function Details({ doc }: { doc: api.Evidence }) {
  return <details className="min-w-0 text-sm"><summary className="cursor-pointer break-words">{api.evidenceTitle(doc)} · {doc.unit_id || '待對應'}</summary>
    <p className="break-all text-xs">{doc.collection}/{doc.doc_id} · 版本 {doc.version || '未提供'} · SHA256 {doc.content_sha256}</p>
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words p-2">{doc.content}</pre></details>;
}

export default function AdminRerankTests({ csrfToken, config, active }: { csrfToken: string; config: RuntimeConfig; active: boolean }) {
  const [datasets, setDatasets] = useState<api.Dataset[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [dataset, setDataset] = useState<api.Dataset | null>(null);
  const [name, setName] = useState('');
  const [settings, setSettings] = useState(() => api.defaultSettings(config));
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [external, setExternal] = useState(false);
  const [runs, setRuns] = useState<api.Run[]>([]);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [run, setRun] = useState<api.Run | null>(null);
  const [costAck, setCostAck] = useState(false);
  const operation = useRef<{ body: string; key: string } | null>(null);
  async function perform(action: () => Promise<void>) {
    if (!active || busy) return;
    setError(''); setBusy(true);
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  useEffect(() => {
    if (!active) return;
    let alive = true;
    void Promise.all([api.listDatasets(), api.listRuns(), getRerankModels(csrfToken).catch(() => ({ data: [] }))]).then(([ds, rs, ms]) => {
      if (!alive) return;
      setDatasets(ds.items); setCursor(ds.next_cursor); setRuns(rs.items); setRunCursor(rs.next_cursor); setModels(ms.data);
    }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [active, csrfToken]);
  useEffect(() => {
    if (!active || !run || !running(run.status)) return;
    let alive = true;
    const timer = window.setInterval(() => { void api.getRun(run.id).then(value => { if (alive) setRun(value); }).catch(e => { if (alive) setError(String(e)); }); }, 2000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [active, run]);
  const select = async (id: string) => { const value = await api.getDataset(id); setDataset(value); setName(value.name); };
  async function launch(limit: boolean, reuse: api.Run | null) {
    if (!dataset || !external) throw new Error('請選擇已發布資料集，並確認外部呼叫。');
    const publishedRevision = reuse?.dataset_revision ?? dataset.published_revision;
    if (!publishedRevision) throw new Error('請先發布人工確認的資料集版本。');
    const target = reuse ? await api.getDataset(reuse.dataset_id) : dataset;
    const version = await api.getRevision(target.id, publishedRevision);
    const ids = reuse ? reuse.results.filter(row => row.candidate_ref).map(row => row.case_id) : limit ? version.cases.slice(0, 5).map(c => c.id) : null;
    if (reuse && !ids?.length) throw new Error('此執行沒有可重用的候選。');
    const selected = ids ? version.cases.filter(c => ids.includes(c.id)) : version.cases;
    const invalid = api.validateSettings(settings, selected.map(c => c.data_types));
    if (invalid) throw new Error(invalid);
    const body = JSON.stringify({ dataset: target.id, revision: publishedRevision, settings, ids, reuse: reuse?.id });
    if (operation.current?.body !== body) operation.current = { body, key: crypto.randomUUID() };
    const result = await api.startRun({ ...target, published_revision: publishedRevision }, settings, csrfToken, operation.current.key, ids, reuse?.id ?? null);
    operation.current = null; setRun(result); setRuns(old => [result, ...old.filter(r => r.id !== result.id)]);
  }
  async function download(format: 'json' | 'csv') {
    if (!run) return;
    const blob = await api.downloadRun(run.id, format); const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `rerank-${run.id}.${format}`; anchor.click(); URL.revokeObjectURL(url);
  }
  return <div className="mx-auto max-w-6xl space-y-6" aria-label="Rerank 測試">
    <header><h3 className="text-xl font-bold">Rerank 測試</h3><p className="text-sm">量測相對人工標註來源的召回率。K 是片段數：先取前 K 筆，再以法條／來源去重。</p></header>
    {error && <p role="alert" className="break-words text-error">{error}</p>}
    <fieldset disabled={!active || busy} className={panel}><legend className="font-bold">資料集與標註</legend>
      <div className="flex flex-wrap gap-2"><select aria-label="資料集" className="input flex-1" value={dataset?.id ?? ''} onChange={e => void perform(() => select(e.target.value))}><option value="" disabled>選擇資料集</option>{datasets.map(d => <option key={d.id} value={d.id}>{d.name}{d.archived ? '（已封存）' : ''}</option>)}</select>
        {cursor && <button className={button} onClick={() => void perform(async () => { const page = await api.listDatasets(cursor); setDatasets(old => [...old, ...page.items]); setCursor(page.next_cursor); })}>更多資料集</button>}
      </div>
      <Field title="資料集名稱"><input className="input" value={name} onChange={e => setName(e.target.value)} maxLength={120} /></Field>
      <div className="flex flex-wrap gap-2"><button className={button} disabled={!name.trim()} onClick={() => void perform(async () => { const d = await api.createDataset(name, csrfToken); setDataset(d); setDatasets(old => [d, ...old]); })}>建立資料集</button>
        {dataset && <><button className={button} onClick={() => void perform(async () => { const d = await api.updateDataset(dataset, name, dataset.archived, csrfToken); setDataset(d); setDatasets(old => old.map(v => v.id === d.id ? d : v)); })}>儲存名稱</button>
          <button className={button} onClick={() => void perform(async () => { const d = await api.updateDataset(dataset, dataset.name, !dataset.archived, csrfToken); setDataset(d); setDatasets(old => old.map(v => v.id === d.id ? d : v)); })}>{dataset.archived ? '解除封存' : '封存資料集'}</button>
          <button className={button} onClick={() => void perform(() => select(dataset.id))}>重新載入草稿</button>
          <button className={button} disabled={dataset.archived} onClick={() => void perform(async () => { await api.publishDataset(dataset, csrfToken); await select(dataset.id); })}>發布確認版本</button>
          <p className="text-sm">草稿 {dataset.revision} · 已發布 {dataset.published_revision ?? '無'} · {dataset.cases.length}/200 題</p></>}
      </div>
    </fieldset>
    {dataset && <CaseEditor key={dataset.id} dataset={dataset} onSaved={setDataset} token={csrfToken} settings={settings} active={active && !dataset.archived} external={external} />}
    <fieldset disabled={!active || busy} className={panel}><legend className="font-bold">執行設定（僅本次）</legend>
      <Field title="模型 ID"><input className="input" list="rerank-evaluation-models" value={settings.model} onChange={e => setSettings({ ...settings, model: e.target.value })} /><datalist id="rerank-evaluation-models">{models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</datalist></Field>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{([
        ['candidate_k', '候選 N', 1, 50, 1], ['top_k', '保留 K', 1, 20, 1], ['timeout_seconds', '等待秒數', 0.1, 10, 0.1], ['max_chars_per_doc', '每文件節錄字數', 100, 20000, 1],
      ] as const).map(([key, title, min, max, step]) => <Field title={title} key={key}><input type="number" className="input" min={min} max={max} step={step} value={settings[key]} onChange={e => setSettings({ ...settings, [key]: Number(e.target.value) })} /></Field>)}</div>
      <Field title="距離門檻（空白停用）"><input className="input" type="number" min={0} max={2} step={0.01} value={settings.distance_threshold ?? ''} onChange={e => setSettings({ ...settings, distance_threshold: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
      <Field title="Provider 排序"><select className="input" value={settings.provider_sort} onChange={e => setSettings({ ...settings, provider_sort: e.target.value as api.Settings['provider_sort'] })}>{['auto', 'latency', 'throughput', 'price'].map(v => <option key={v}>{v}</option>)}</select></Field>
      <label className="block"><input type="checkbox" checked={settings.preserve_data_types} onChange={e => setSettings({ ...settings, preserve_data_types: e.target.checked })} /> 保留資料類型保障（K 至少等於類型數）</label>
      <p className="text-sm">取得候選：已確認查詢送至目前環境的 embedding 服務。執行重排：查詢及文件節錄送至 OpenRouter rerank 端點及選用模型供應商。可能產生費用；不自動重試付費呼叫。</p>
      <label className="block"><input type="checkbox" checked={external} onChange={e => setExternal(e.target.checked)} /> 我已確認本次外部呼叫用途及送出內容</label>
      <div className="flex flex-wrap gap-2"><button className={button} disabled={!external || !dataset?.published_revision} onClick={() => void perform(() => launch(true, null))}>先執行 5 題</button><button className={button} disabled={!external || !dataset?.published_revision} onClick={() => void perform(() => launch(false, null))}>重新檢索執行完整資料集</button></div>
    </fieldset>
    <section className={panel}><h4 className="font-bold">結果與歷史</h4>
      <div className="flex flex-wrap gap-2"><select aria-label="執行歷史" className="input flex-1" value={run?.id ?? ''} onChange={e => void perform(async () => { setRun(await api.getRun(e.target.value)); setCostAck(false); })}><option value="" disabled>選擇執行紀錄</option>{runs.map(r => <option key={r.id} value={r.id}>{r.created_at} · {r.settings.model} · {r.status}{r.archived ? '（封存）' : ''}</option>)}</select>
        <button className={button} disabled={busy || !active} onClick={() => void perform(async () => { const p = await api.listRuns(); setRuns(p.items); setRunCursor(p.next_cursor); })}>更新歷史</button>
        {runCursor && <button className={button} onClick={() => void perform(async () => { const p = await api.listRuns(runCursor); setRuns(old => [...old, ...p.items]); setRunCursor(p.next_cursor); })}>更多紀錄</button>}
      </div>
      {run && <><p className="break-all text-sm">{run.id} · 版本 {run.dataset_revision} · {run.status} · {run.settings.model} · N={run.settings.candidate_k}, K={run.settings.top_k}</p>
        {run.error && <p role="alert">{run.error.detail}</p>}
        <div className="flex flex-wrap gap-2"><button className={button} disabled={busy} onClick={() => void perform(() => download('json'))}>下載 JSON</button><button className={button} disabled={busy} onClick={() => void perform(() => download('csv'))}>下載 CSV</button>
          <button className={button} disabled={busy || !external || !dataset} onClick={() => void perform(() => launch(false, run))}>用相同候選重跑</button>
          <button className={button} disabled={busy || run.archived} onClick={() => void perform(async () => setRun(await api.archiveRun(run.id, csrfToken)))}>封存結果</button>
          {running(run.status) && <button className={button} onClick={() => void perform(async () => { await cancelCorpusJob(run.job_id, csrfToken); setRun(await api.getRun(run.id)); })}>取消後續題目</button>}
        </div>
        {['failed', 'needs_review'].includes(run.status) && <div><label><input type="checkbox" checked={costAck} onChange={e => setCostAck(e.target.checked)} /> 已確認可能重複計費；允許明確重試</label><button className={button} disabled={!costAck || busy} onClick={() => void perform(async () => { await retryCorpusJob(run.job_id, true, csrfToken); setRun(await api.getRun(run.id)); setCostAck(false); })}>重試未完成任務</button></div>}
        <RunResults run={run} />
      </>}
    </section>
  </div>;
}

function CaseEditor({ dataset, onSaved, token, settings, active, external }: { dataset: api.Dataset; onSaved: (d: api.Dataset) => void; token: string; settings: api.Settings; active: boolean; external: boolean }) {
  const [id, setId] = useState<string | null>(null);
  const [raw, setRaw] = useState('');
  const [hidden, setHidden] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const [approved, setApproved] = useState(false);
  const [types, setTypes] = useState<api.Kind[]>(['law']);
  const [annotations, setAnnotations] = useState<api.Annotation[]>([]);
  const [reviewed, setReviewed] = useState(false);
  const [noRelevant, setNoRelevant] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preparation, setPreparation] = useState<api.Preparation | null>(null);
  const [lookupKind, setLookupKind] = useState<api.Kind>('law');
  const [lookupId, setLookupId] = useState('');
  const [manualId, setManualId] = useState('');
  const [records, setRecords] = useState<CorpusRecord[]>([]);
  const [recordCursor, setRecordCursor] = useState<string | null>(null);
  const [source, setSource] = useState<CorpusSource | null>(null);
  const operation = useRef<{ body: string; key: string } | null>(null);
  async function perform(action: () => Promise<void>) {
    if (!active || busy) return;
    setBusy(true); setError('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  function load(value?: api.EvaluationCase) {
    setId(value?.id ?? null); setRaw(value?.query ?? ''); setHidden(''); setPreview(value?.query ?? null); setApproved(!!value);
    setTypes(value?.data_types ?? ['law']); setAnnotations(value?.annotations ?? []); setReviewed(value?.reviewed ?? false); setNoRelevant(value?.no_relevant ?? false); setPreparation(null); setError('');
  }
  useEffect(() => {
    if (!id || !active) return;
    let alive = true;
    const poll = () => { void api.getPreparation(dataset.id, id).then(p => { if (alive) setPreparation(p); }).catch(e => { if (alive) setError(String(e)); }); };
    poll(); const timer = window.setInterval(poll, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [id, dataset.id, active]);
  function merge(docs: api.Evidence[]) {
    setAnnotations(old => [...old, ...docs.filter(d => !old.some(a => a.document.key === d.key)).map(document => ({ document, relevance: 'unjudged' as const }))]); setReviewed(false);
  }
  async function save() {
    if (!approved || preview == null || !preview.trim()) throw new Error('請先完成本機去識別並確認預覽。');
    if (Array.from(preview).length > 4000) throw new Error('查詢超過 4,000 字，請縮短後重新確認；不會自動截斷。');
    if (!types.length) throw new Error('至少選擇一種資料類型。');
    if (!id && dataset.cases.length >= 200) throw new Error('每個資料集最多 200 題。');
    const result = await api.saveCase(dataset, { query: preview, data_types: types, privacy_confirmed: true, annotations, reviewed, no_relevant: noRelevant }, id, token);
    onSaved(result); load(result.cases.find(c => c.id === id) ?? result.cases[result.cases.length - 1]);
  }
  const saved = dataset.cases.find(c => c.id === id);
  const savedQueryMatches = !!saved && approved && preview === saved.query && JSON.stringify(types) === JSON.stringify(saved.data_types);
  const groups = new Map<string, api.Annotation[]>();
  for (const a of annotations) { const key = a.document.unit_id || a.document.key; groups.set(key, [...(groups.get(key) ?? []), a]); }
  return <fieldset disabled={!active || busy} className={panel}><legend className="font-bold">人工標註</legend>
    {error && <p role="alert" className="text-error">{error}</p>}
    <div className="flex flex-wrap gap-2"><select aria-label="標註題目" className="input flex-1" value={id ?? ''} onChange={e => load(dataset.cases.find(c => c.id === e.target.value))}><option value="">新增查詢</option>{dataset.cases.map(c => <option key={c.id} value={c.id}>{c.reviewed ? '已確認' : '待確認'} · {c.query.slice(0, 60)}</option>)}</select><button className={button} onClick={() => load()}>新增查詢</button></div>
    <Field title="查詢原文（只留在本機，最多 4,000 字）"><textarea className="input" rows={3} value={raw} onChange={e => { setRaw(e.target.value); setPreview(null); setApproved(false); setReviewed(false); }} /></Field>
    <Field title="額外遮罩詞（每行一個，只留在本機）"><textarea className="input" rows={2} value={hidden} onChange={e => { setHidden(e.target.value); setPreview(null); setApproved(false); setReviewed(false); }} /></Field>
    <button className={button} onClick={() => { try { if (Array.from(raw).length > 4000) throw new Error('查詢超過 4,000 字。'); setPreview(createPrivacyDraft({ message: raw, history: [], use_rag: true }, hidden.split('\n').filter(Boolean)).request.message); setApproved(false); setError(''); } catch (e) { setError(String(e)); } }}>本機去識別預覽</button>
    {preview !== null && <div className="rounded-lg bg-surface-container-low p-3"><p className="text-sm font-bold">將儲存並送出的查詢</p><pre className="whitespace-pre-wrap break-words">{preview}</pre><label><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} /> 我已確認此去識別文字可儲存及送出</label></div>}
    <div className="flex flex-wrap gap-3">{kinds.map(kind => <label key={kind}><input type="checkbox" checked={types.includes(kind)} onChange={e => { setTypes(e.target.checked ? [...types, kind] : types.filter(k => k !== kind)); setReviewed(false); }} /> {api.kindLabels[kind]}</label>)}</div>
    <div className="flex flex-wrap gap-2"><button className={button} disabled={!approved} onClick={() => void perform(save)}>儲存題目與標註</button>
      {id && <button className={button} onClick={() => void perform(async () => { if (!window.confirm('刪除此草稿題目？已發布版本仍保留。')) return; onSaved(await api.deleteCase(dataset, id, token)); load(); })}>刪除草稿題目</button>}
      <button className={button} disabled={!savedQueryMatches || !external} onClick={() => void perform(async () => {
        if (!id) return;
        const invalid = api.validateSettings(settings, [types]); if (invalid) throw new Error(invalid);
        const body = JSON.stringify({ dataset: dataset.id, revision: dataset.revision, id, settings });
        if (operation.current?.body !== body) operation.current = { body, key: crypto.randomUUID() };
        setPreparation(await api.prepareCandidates(dataset, id, settings, token, operation.current.key)); operation.current = null;
      })}>取得標註候選</button>
    </div>
    <p className="text-xs">先儲存確認後的查詢，再於執行設定勾選外部呼叫確認。候選僅供人工判定，預設全部未判定，不顯示重排分數。</p>
    {preparation && preparation.status !== 'none' && <div><p>候選任務：{preparation.status} {preparation.error?.detail}</p>
      {preparation.result && <button className={button} onClick={() => merge(preparation.result!.documents)}>載入 {preparation.result.documents.length} 個候選供標註</button>}
      {preparation.job_id && running(preparation.status) && <button className={button} onClick={() => void perform(async () => { await cancelCorpusJob(preparation.job_id!, token); setPreparation(await api.getPreparation(dataset.id, id!)); })}>取消候選準備</button>}
      {preparation.job_id && ['failed', 'needs_review'].includes(preparation.status) && <button className={button} onClick={() => void perform(async () => { if (!window.confirm('外部呼叫可能已計費。確認允許重複費用並重試？')) return; await retryCorpusJob(preparation.job_id!, true, token); setPreparation(await api.getPreparation(dataset.id, id!)); })}>確認費用後重試候選準備</button>}
    </div>}
    <details className="space-y-2"><summary className="cursor-pointer font-semibold">從檢索資料庫補選候選外來源</summary>
      <div className="flex flex-wrap gap-2"><select aria-label="查找資料類型" className="input" value={lookupKind} onChange={e => { setLookupKind(e.target.value as api.Kind); setRecords([]); setRecordCursor(null); }}>{kinds.map(k => <option key={k} value={k}>{api.kindLabels[k]}</option>)}</select><button className={button} onClick={() => void perform(async () => { const p = await api.browseDocuments(lookupKind, null); setRecords(p.items); setRecordCursor(p.next_cursor); })}>瀏覽資料庫</button></div>
      <Field title="文件 ID"><input className="input" value={lookupId} onChange={e => setLookupId(e.target.value)} /></Field>
      <Field title="人工來源對應 ID（僅缺少法條／來源資訊時填寫）"><input className="input" value={manualId} onChange={e => setManualId(e.target.value)} /></Field>
      <button className={button} disabled={!lookupId.trim()} onClick={() => void perform(async () => merge([await api.resolveDocument(lookupKind, lookupId.trim(), token, manualId.trim() || null)]))}>依 ID 加入待判定</button>
      {records.map(r => <div key={r.id} className="border-b border-outline/15 py-2"><p className="break-all text-xs">{r.id} · {String(r.metadata.title ?? r.metadata.law_name ?? r.source_id ?? '')}</p><details><summary>片段全文</summary><pre className="whitespace-pre-wrap break-words">{r.content}</pre></details><button className={button} onClick={() => void perform(async () => merge([await api.resolveDocument(lookupKind, r.id, token)]))}>加入標註</button>{r.source_id && <button className={button} onClick={() => void perform(async () => setSource(await getSource(r.source_id!)))}>來源詳情</button>}</div>)}
      {recordCursor && <button className={button} onClick={() => void perform(async () => { const p = await api.browseDocuments(lookupKind, recordCursor); setRecords(p.items); setRecordCursor(p.next_cursor); })}>下一頁文件</button>}
      {source && <section className={panel}><h5>{source.title} · {source.source_id} · {source.status} · 版本 {source.revision}</h5>{source.chunks?.map(c => <details key={c.id}><summary>{c.id}</summary><pre className="whitespace-pre-wrap break-words">{c.content}</pre><button className={button} onClick={() => void perform(async () => merge([await api.resolveDocument(source.kind, c.id, token)]))}>加入來源片段</button></details>)}{source.next_cursor && <button className={button} onClick={() => void perform(async () => setSource(await getSource(source.source_id, undefined, source.next_cursor)))}>下一頁來源片段</button>}</section>}
    </details>
    {[...groups].map(([unit, items]) => <section key={unit} className="space-y-2 rounded-lg border border-outline/15 p-3"><p className="break-all text-sm font-semibold">{unit} · {items.length} 個片段</p>
      <select aria-label={`相關性 ${unit}`} className="input" value={items.every(a => a.relevance === items[0].relevance) ? items[0].relevance : 'unjudged'} onChange={e => { const value = e.target.value as api.Relevance; const keys = new Set(items.map(a => a.document.key)); setAnnotations(old => old.map(a => keys.has(a.document.key) ? { ...a, relevance: value } : a)); setReviewed(false); }}><option value="unjudged">未判定</option><option value="relevant">相關</option><option value="irrelevant">不相關</option></select>
      {items.map(a => <div key={a.document.key}><Details doc={a.document} />{!a.document.unit_id && <button className={button} onClick={() => void perform(async () => { const mapping = window.prompt('輸入此資料集的明確來源對應 ID（同一來源使用相同 ID）'); if (!mapping?.trim()) return; const doc = await api.resolveDocument(a.document.kind, a.document.doc_id, token, mapping.trim()); setAnnotations(old => old.map(v => v.document.key === doc.key ? { ...v, document: doc } : v)); setReviewed(false); })}>設定人工對應</button>}
        <button className={button} onClick={() => void perform(async () => { const doc = await api.resolveDocument(a.document.kind, a.document.doc_id, token, a.document.manual_unit_id); setAnnotations(old => old.map(v => v.document.key === doc.key ? { document: doc, relevance: 'unjudged' } : v)); setReviewed(false); })}>重新讀取並重新判定</button>
        <button className={button} onClick={() => { setAnnotations(old => old.filter(v => v.document.key !== a.document.key)); setReviewed(false); }}>移除草稿標註</button></div>)}
    </section>)}
    <label className="block"><input type="checkbox" checked={noRelevant} onChange={e => { setNoRelevant(e.target.checked); setReviewed(false); }} /> 明確確認此題無相關來源（recall 不適用）</label>
    <label className="block"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} /> 已人工核對所有標註及候選外相關來源，確認標準答案</label>
    <p className="text-xs">修改後請儲存，再發布新版本。未判定、待對應及範圍不符的標註無法發布。</p>
  </fieldset>;
}

function RunResults({ run }: { run: api.Run }) {
  const [filter, setFilter] = useState('all');
  const summary = run.summary;
  const rows = run.results.filter(r => filter === 'all' || (filter === 'missing' && !!r.metrics?.missing_candidates.length) || (filter === 'worse' && (r.metrics?.delta ?? 0) < 0) || (filter === 'failed' && (r.error || r.rerank?.status !== 'ok')));
  function aggregate(title: string, values: Record<api.Stage, api.Aggregate>) {
    return <div className="overflow-x-auto"><table className="w-full text-left text-sm"><caption className="text-left font-semibold">{title}</caption><thead><tr><th>指標</th><th>Macro</th><th>Micro</th><th>有效題數</th></tr></thead><tbody>{stages.map(stage => <tr key={stage}><td>{api.stageLabels[stage]}</td><td>{api.percentage(values[stage]?.macro)}</td><td>{api.percentage(values[stage]?.micro)}</td><td>{values[stage]?.evaluated_cases ?? 0}/{summary.total}</td></tr>)}</tbody></table></div>;
  }
  return <div className="space-y-4"><p className="text-sm">已完成 {summary.finished}/{summary.total}（{api.percentage(summary.completion_rate)}）；有效量測 {summary.measured}/{summary.total}。耗時 P95：{summary.p95_ms ?? '不適用'} ms。</p>
    <p className="text-sm">重排狀態：{Object.entries(summary.statuses).map(([s, n]) => `${s}: ${n}`).join(' · ')}</p>
    {aggregate('整體', summary.stages)}<details><summary>法規／判決／救濟與混合檢索</summary>{kinds.map(k => <div key={k}>{aggregate(api.kindLabels[k], summary.per_kind[k])}</div>)}{aggregate('混合檢索', summary.mixed)}</details>
    <label className="block text-sm">篩選<select className="input" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部題目</option><option value="missing">檢索漏掉</option><option value="worse">重排變差</option><option value="failed">失敗／逾時／略過</option></select></label>
    {rows.map(row => <details key={row.case_id} className={panel}><summary className="cursor-pointer break-words">{row.query} · {row.rerank?.status ?? row.status} · 差值 {api.percentage(row.metrics?.delta)}</summary>
      {row.error && <p role="alert">{row.error.code}：{row.error.detail}</p>}
      {row.metrics && <><div className="grid gap-2 sm:grid-cols-2">{stages.map(s => <p key={s}>{api.stageLabels[s]}：{api.percentage(row.metrics!.stages[s].recall)}（命中 {row.metrics!.stages[s].hits ?? '不適用'}/{row.metrics!.stages[s].relevant}，返回 {row.metrics!.stages[s].returned_chunks ?? '不適用'} 片段）</p>)}</div>
        {(['missing_candidates', 'dropped', 'gained', 'missing_final', 'unmapped_keys'] as const).map((key, i) => <p key={key} className="break-all text-xs">{['漏檢索', '重排後掉出', '新增命中', '最終遺漏', '待對應'][i]}：{row.metrics![key].join('、') || '無'}</p>)}</>}
      <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>候選排名</th><th>模型排名</th><th>最終排名</th><th>原始分數</th><th>文件與規則</th></tr></thead><tbody>{row.candidates?.map((doc, i) => <tr key={doc.key} className="border-t border-outline/15 align-top"><td>{i + 1}</td><td>{row.model_order?.includes(doc.key) ? row.model_order.indexOf(doc.key) + 1 : '不適用'}</td><td>{row.final_order?.includes(doc.key) ? row.final_order.indexOf(doc.key) + 1 : '—'}</td><td>{row.scores?.[doc.key] ?? '—'}</td><td className="min-w-48 max-w-xl"><Details doc={doc} /><p>{row.selection_reasons?.[doc.key]?.join('、')}</p><details><summary>{row.rerank?.status === 'skipped_small_pool' ? '準備節錄（未送出）' : '實際送出節錄'}</summary><pre className="whitespace-pre-wrap break-words">{row.excerpts?.find(e => e.key === doc.key)?.text ?? '未送出'}</pre><p className="break-all">{row.excerpts?.find(e => e.key === doc.key)?.sha256}</p></details></td></tr>)}</tbody></table></div>
    </details>)}
    <p className="text-xs">指標只相對已標註的相關來源；不代表涵蓋全庫所有相關文件。來源版本按讀取時記錄，並非全庫同一時點快照。</p>
  </div>;
}
