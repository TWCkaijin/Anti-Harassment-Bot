import { useEffect, useRef, useState } from "react";
import { invalidateReadCache } from "../services/readCache";
import { ApiError } from "../services/api";
import {
  confirmImport,
  deleteSource,
  downloadSource,
  getCorpusJob,
  getImport,
  getRecord,
  getSource,
  listRecords,
  listSources,
  mapImport,
  previewImport,
  resumeParse,
  type ColumnMapping,
  type CorpusJob,
  type CorpusKind,
  type CorpusOperation,
  type CorpusRecord,
  type CorpusSource,
  type ImportDraft,
  type ImportPreview,
} from "../services/corpus";
import CorpusJobs from "./CorpusJobs";
import { CorpusDrafts, CorpusMapping } from "./CorpusDrafts";

const KIND_LABELS: Record<CorpusKind, string> = {
  law: "法規",
  judgment: "判決",
  remedy: "救濟與資源",
};

const STATE_LABELS: Record<string, string> = {
  active: "可檢索",
  queued: "排隊中",
  running: "處理中",
  succeeded: "完成",
  completed: "完成",
  failed: "失敗",
  cancelled: "已取消",
  needs_review: "需要檢查",
  deleting: "刪除中",
  purge_pending: "已移出檢索，檔案清理中",
  deleted: "已刪除",
  legacy: "既有資料",
};

const VIEW_TABS = [
  ["sources", "完整來源"],
  ["records", "所有資料片段"],
  ["import", "加入資料"],
] as const;

type CorpusView = (typeof VIEW_TABS)[number][0];

function errorText(value: unknown): string {
  if (value instanceof ApiError) return value.detail ?? value.message;
  if (value instanceof Error) return value.message;
  return "資料操作失敗";
}

export default function AdminCorpus({
  csrfToken,
  active = true,
}: {
  csrfToken: string;
  active?: boolean;
}) {
  const [view, setView] = useState<CorpusView>("sources");
  const [kind, setKind] = useState<CorpusKind>("law");
  const [pages, setPages] = useState<Array<string | null>>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [sources, setSources] = useState<CorpusSource[]>([]);
  const [records, setRecords] = useState<CorpusRecord[]>([]);
  const [selected, setSelected] = useState<CorpusSource | null>(null);
  const [deleteName, setDeleteName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [metadata, setMetadata] = useState("{}");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [draft, setDraft] = useState<ImportDraft | null>(null);
  const [jobRefresh, setJobRefresh] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [operation, setOperation] = useState<CorpusOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  const detailRequest = useRef<AbortController | null>(null);
  const previewRequest = useRef<AbortController | null>(null);
  const alive = useRef(true);

  const cursor = pages.at(-1) ?? null;
  const operationPending =
    !!operation?.job_id &&
    !["succeeded", "completed", "failed", "cancelled", "needs_review"].includes(operation.status);
  const parsePending = draft?.status === "parse_queued" || draft?.status === "parsing";

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      detailRequest.current?.abort();
      previewRequest.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!active || view === "import") return;
    const abort = new AbortController();
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        if (view === "sources") {
          const page = await listSources(cursor, abort.signal);
          if (!abort.signal.aborted) {
            setSources(page.items);
            setNextCursor(page.next_cursor);
          }
        } else {
          const page = await listRecords(kind, cursor, abort.signal);
          if (!abort.signal.aborted) {
            setRecords(page.items);
            setNextCursor(page.next_cursor);
          }
        }
      } catch (failure) {
        if (!abort.signal.aborted) setError(errorText(failure));
      } finally {
        if (!abort.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => abort.abort();
  }, [view, kind, cursor, refresh, active, csrfToken]);

  const acceptDraft = (value: ImportDraft) => {
    const parseStopped = ["failed", "cancelled", "needs_review"].includes(value.job?.status ?? "");
    const nextDraft =
      parseStopped && ["parse_queued", "parsing"].includes(value.status)
        ? { ...value, status: value.job?.status === "failed" ? "parse_failed" : value.job!.status }
        : value;
    setDraft(nextDraft);
    setConfirmed(false);
    setPreview(
      value.status === "preview" && value.plan_hash && value.chunks
        ? (value as ImportPreview)
        : null,
    );
    if (["parse_queued", "parsing"].includes(value.status) && value.parse_job_id) {
      setOperation({ job_id: value.parse_job_id, status: value.job?.status ?? "queued" });
    } else if (value.job_id) {
      setOperation({ job_id: value.job_id, status: "queued" });
    }
    setJobRefresh((count) => count + 1);
  };

  const openDraft = async (id: string) => {
    previewRequest.current?.abort();
    const controller = new AbortController();
    previewRequest.current = controller;
    setBusy(true);
    setError("");
    setView("import");
    try {
      const value = await getImport(id, controller.signal);
      if (value.parse_job_id && ["parse_queued", "parsing", "parse_failed"].includes(value.status)) {
        value.job = await getCorpusJob(value.parse_job_id, controller.signal);
      }
      if (!controller.signal.aborted) acceptDraft(value);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const completeJob = (job: CorpusJob) => {
    invalidateReadCache("/corpus/");
    setOperation((previous) =>
      previous?.job_id === job.job_id ? { ...previous, status: job.status } : previous,
    );
    setRefresh((value) => value + 1);
    setJobRefresh((value) => value + 1);

    if (["succeeded", "completed"].includes(job.status)) {
      if (job.kind === "corpus_parse" && job.result?.import_id) {
        if (draft?.import_id === job.result.import_id || operation?.job_id === job.job_id) {
          void openDraft(job.result.import_id);
        }
      } else if (job.kind === "corpus_import") {
        setPreview(null);
        setSelected(null);
      } else if (job.kind === "corpus_delete") {
        setSelected(null);
      }
    } else if (job.kind === "corpus_parse") {
      setDraft((previous) =>
        previous?.parse_job_id === job.job_id
          ? { ...previous, status: job.status === "failed" ? "parse_failed" : job.status }
          : previous,
      );
      if (
        job.status === "failed" &&
        (draft?.parse_job_id === job.job_id || operation?.job_id === job.job_id) &&
        draft?.import_id
      ) {
        void openDraft(draft.import_id);
      }
    }
  };

  const applyMapping = async (mapping: ColumnMapping) => {
    if (!draft || busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await mapImport(draft, mapping, csrfToken);
      if (alive.current) acceptDraft(value);
    } catch (failure) {
      if (alive.current) setError(errorText(failure));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const restartParsing = async () => {
    if (!draft || busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await resumeParse(draft.import_id, csrfToken);
      if (alive.current) acceptDraft(value);
    } catch (failure) {
      if (alive.current) setError(errorText(failure));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const navigate = (next: CorpusView) => {
    setView(next);
    setPages([null]);
    setSelected(null);
    setError("");
  };

  const openSource = async (id: string, nextChunkCursor?: string | null) => {
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    setBusy(true);
    setError("");
    setDeleteName("");
    try {
      const source = await getSource(id, controller.signal, nextChunkCursor);
      if (!controller.signal.aborted) {
        setSelected((previous) =>
          nextChunkCursor && previous?.source_id === id
            ? { ...source, chunks: [...(previous.chunks ?? []), ...(source.chunks ?? [])] }
            : source,
        );
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const download = async () => {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const blob = await downloadSource(selected.source_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = selected.filename;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      if (alive.current) setError(errorText(failure));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const prepare = async () => {
    if (!file || busy) return;
    setBusy(true);
    setError("");
    setPreview(null);
    setConfirmed(false);
    const controller = new AbortController();
    previewRequest.current = controller;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("來源檔案不可超過 5 MiB。");
      const value: unknown = JSON.parse(metadata);
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("來源資訊必須是 JSON 物件。");
      }
      const result = await previewImport(
        file,
        kind,
        title,
        value as Record<string, unknown>,
        csrfToken,
        controller.signal,
      );
      if (!controller.signal.aborted) acceptDraft(result);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const confirm = async () => {
    if (!preview || !confirmed || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await confirmImport(preview, csrfToken);
      if (alive.current) {
        setOperation(result);
        setJobRefresh((value) => value + 1);
        if (result.duplicate || !result.job_id) {
          setPreview(null);
          setRefresh((value) => value + 1);
        }
      }
    } catch (failure) {
      if (alive.current) setError(errorText(failure));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const remove = async () => {
    if (!selected || deleteName !== selected.title || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await deleteSource(selected, csrfToken);
      if (alive.current) {
        setOperation(result);
        setJobRefresh((value) => value + 1);
        setSelected({ ...selected, status: "deleting" });
        setDeleteName("");
      }
    } catch (failure) {
      if (alive.current) setError(errorText(failure));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <section aria-label="檢索資料庫" className="mx-auto max-w-6xl space-y-5">
      <div>
        <h3 className="text-xl font-bold">檢索資料庫</h3>
        <p className="mt-2 text-sm text-on-surface/65">
          查看完整來源、所有資料片段，並在確認預覽後加入檢索資料。
        </p>
      </div>

      <nav aria-label="資料庫檢視" className="flex flex-wrap gap-2">
        {VIEW_TABS.map(([id, label]) => (
          <button
            key={id}
            onClick={() => navigate(id)}
            aria-current={view === id ? "page" : undefined}
            className={`rounded-lg border px-4 py-2 text-sm ${
              view === id ? "border-primary bg-primary-container text-primary" : "border-outline/30"
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {error && (
        <p role="alert" className="rounded-lg bg-error-container p-3 text-sm text-error">
          {error}
        </p>
      )}

      {operation && (
        <div role="status" className="rounded-lg border border-outline/30 p-3 text-sm">
          工作狀態：{STATE_LABELS[operation.status] ?? operation.status}
          {operation.duplicate ? "；相同來源已存在，未重複 embedding。" : ""}
          {operation.job_id && (
            <span className="ml-2 break-all font-mono text-xs">{operation.job_id}</span>
          )}
          <p className="mt-1 text-xs text-on-surface/60">
            背景工作由伺服器執行；離開頁面不會中斷已確認的匯入或刪除。
          </p>
        </div>
      )}

      {view !== "import" && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            {view === "records" && (
              <KindSelect
                value={kind}
                onChange={(value) => {
                  setKind(value);
                  setPages([null]);
                }}
              />
            )}
            <button
              className="rounded-lg border border-outline/30 px-3 py-2 text-sm"
              onClick={() => {
                invalidateReadCache("/corpus/");
                setSelected(null);
                setRefresh((value) => value + 1);
              }}
              disabled={loading}
            >
              重新整理
            </button>
            <span className="text-xs text-on-surface/60">第 {pages.length} 頁，每頁最多 50 筆</span>
            <span className="text-xs text-on-surface/50">列表使用本機快取；開啟內容時更新</span>
          </div>

          {loading ? (
            <p role="status">讀取資料中…</p>
          ) : view === "sources" ? (
            <div className="overflow-x-auto rounded-lg border border-outline/30">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-container">
                  <tr>
                    <th className="p-3">來源</th>
                    <th className="p-3">類型</th>
                    <th className="p-3">片段</th>
                    <th className="p-3">狀態</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((source) => (
                    <tr key={source.source_id} className="border-t border-outline/20">
                      <td className="p-3">
                        <button
                          disabled={busy}
                          className="text-left font-medium text-primary underline"
                          onClick={() => {
                            void openSource(source.source_id);
                          }}
                        >
                          {source.title || source.filename}
                        </button>
                        <p className="mt-1 text-xs text-on-surface/50">{source.filename}</p>
                      </td>
                      <td className="p-3">{KIND_LABELS[source.kind]}</td>
                      <td className="p-3">{source.chunk_count}</td>
                      <td className="p-3">{STATE_LABELS[source.status] ?? source.status}</td>
                    </tr>
                  ))}
                  {!sources.length && (
                    <tr>
                      <td colSpan={4} className="p-5 text-center text-on-surface/60">
                        尚無已核對的完整來源。既有片段可從「所有資料片段」查看。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="space-y-3">
              {records.map((record) => (
                <RecordDetail key={record.id} record={record} refreshOnOpen />
              ))}
              {!records.length && <p>此類型尚無資料。</p>}
            </div>
          )}

          <div className="flex gap-2">
            <button
              className="rounded border border-outline/30 px-3 py-2 text-sm disabled:opacity-40"
              disabled={loading || pages.length < 2}
              onClick={() => setPages((value) => value.slice(0, -1))}
            >
              上一頁
            </button>
            <button
              className="rounded border border-outline/30 px-3 py-2 text-sm disabled:opacity-40"
              disabled={loading || !nextCursor}
              onClick={() => setPages((value) => [...value, nextCursor])}
            >
              下一頁
            </button>
          </div>

          {selected && (
            <article className="space-y-4 rounded-lg border border-primary/30 p-5" aria-label="來源內容">
              <div className="flex flex-wrap justify-between gap-3">
                <div>
                  <h4 className="font-bold">{selected.title}</h4>
                  <p className="mt-1 text-xs text-on-surface/60">
                    {selected.filename} · {selected.chunk_count} 片段 ·{" "}
                    {STATE_LABELS[selected.status] ?? selected.status}
                  </p>
                </div>
                <button
                  className="text-sm text-primary underline disabled:opacity-40"
                  disabled={!selected.original_available || busy}
                  onClick={() => {
                    void download();
                  }}
                >
                  下載完整原始檔
                </button>
              </div>
              {!selected.original_available && (
                <p className="text-sm text-on-surface/60">
                  此來源尚無可驗證的原始檔，僅可查看資料庫中的完整片段。
                </p>
              )}
              {selected.chunks?.map((record) => (
                <RecordDetail key={record.id} record={record} />
              ))}
              {selected.next_cursor && (
                <button
                  className="text-sm text-primary underline"
                  disabled={busy}
                  onClick={() => {
                    void openSource(selected.source_id, selected.next_cursor);
                  }}
                >
                  載入更多來源片段（已載入 {selected.chunks?.length ?? 0}／{selected.chunk_count}）
                </button>
              )}
              {selected.status === "active" && (
                <div className="space-y-2 border-t border-outline/30 pt-4">
                  <label className="block text-sm">
                    刪除完整來源：輸入「{selected.title}」確認
                    <input
                      aria-label="刪除來源確認名稱"
                      className="input mt-2 w-full"
                      value={deleteName}
                      onChange={(event) => setDeleteName(event.target.value)}
                    />
                  </label>
                  <p className="text-xs text-on-surface/65">
                    會刪除所有已核對片段及伺服器原始檔。大型既有來源可能分批處理，完成前仍可能出現在檢索結果。
                  </p>
                  <button
                    disabled={busy || operationPending || deleteName !== selected.title}
                    onClick={() => {
                      void remove();
                    }}
                    className="rounded-lg bg-error px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
                  >
                    確認刪除完整來源
                  </button>
                </div>
              )}
            </article>
          )}
        </>
      )}

      {view === "import" && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-medium">
              來源檔案
              <input
                type="file"
                accept=".txt,.md,.markdown,.csv,.json,.jsonl,.xlsx,.pdf,.docx"
                disabled={busy || operationPending || parsePending}
                className="mt-2 block w-full text-sm"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setPreview(null);
                  setDraft(null);
                  setConfirmed(false);
                }}
              />
            </label>
            <KindSelect
              value={kind}
              onChange={(value) => {
                setKind(value);
                setPreview(null);
              }}
              disabled={busy || operationPending || parsePending}
            />
          </div>

          <label className="block text-sm font-medium">
            來源名稱
            <input
              className="input mt-2 w-full"
              value={title}
              maxLength={300}
              disabled={busy || operationPending || parsePending}
              onChange={(event) => {
                setTitle(event.target.value);
                setPreview(null);
              }}
              placeholder="留空則使用檔名"
            />
          </label>

          <details>
            <summary className="cursor-pointer text-sm">來源資訊（JSON，選填）</summary>
            <textarea
              aria-label="來源資訊 JSON"
              className="input mt-2 w-full font-mono text-xs"
              rows={5}
              value={metadata}
              disabled={busy || operationPending || parsePending}
              onChange={(event) => {
                setMetadata(event.target.value);
                setPreview(null);
              }}
            />
          </details>

          <p className="text-xs leading-relaxed text-on-surface/65">
            JSON／JSONL／CSV／XLSX 會先列出欄位供您對應內容與來源資訊；XLSX／CSV 首列為欄名。檔案上傳後由背景工作解析。此步驟不會呼叫 embedding，也不會加入檢索。檔案最多 5 MiB／100 個片段，PDF 最多 100 頁且須含可擷取文字。
          </p>

          <button
            disabled={!file || busy || operationPending || parsePending}
            className="rounded-lg border border-primary px-4 py-2 text-sm font-semibold text-primary disabled:opacity-40"
            onClick={() => {
              void prepare();
            }}
          >
            {busy ? "處理中…" : "產生匯入預覽"}
          </button>

          {parsePending && (
            <p role="status" className="text-sm">
              檔案正在背景解析；可離開頁面，稍後從「可恢復的匯入草稿」繼續。
            </p>
          )}

          {draft?.error?.detail && (
            <p role="alert" className="text-sm text-error">
              {draft.error.detail}
            </p>
          )}

          {draft && ["parse_failed", "cancelled"].includes(draft.status) && (
            <button
              disabled={busy}
              onClick={() => {
                void restartParsing();
              }}
              className="rounded-lg border border-primary px-4 py-2 text-sm text-primary disabled:opacity-40"
            >
              使用已上傳檔案重新解析
            </button>
          )}

          {draft &&
            (draft.status === "mapping_required" ||
              (draft.status === "parse_failed" && !!draft.available_fields?.length)) && (
              <CorpusMapping
                key={`${draft.import_id}:${draft.parse_revision}`}
                draft={draft}
                busy={busy}
                onSubmit={(mapping) => {
                  void applyMapping(mapping);
                }}
              />
            )}

          {preview && (
            <section aria-label="匯入預覽" className="space-y-4 rounded-lg border border-outline/30 p-4">
              <div>
                <h4 className="font-bold">
                  {preview.title} · {preview.chunk_count} 個片段
                </h4>
                <p className="mt-1 text-xs text-on-surface/60">預覽到期：{preview.expires_at}</p>
              </div>
              {preview.warnings.map((warning, index) => (
                <p key={index} className="text-sm text-error">
                  {warning}
                </p>
              ))}
              {preview.duplicate_source_id && (
                <p className="text-sm text-on-surface/70">相同來源已存在，確認時不會重複加入。</p>
              )}
              {preview.chunks.map((chunk, index) => (
                <details key={index} className="rounded-lg bg-surface-container-low p-3">
                  <summary className="cursor-pointer text-sm">
                    片段 {index + 1} · {chunk.content.length} 字元
                  </summary>
                  <pre className="mt-3 whitespace-pre-wrap break-words text-sm">{chunk.content}</pre>
                  <pre className="mt-2 whitespace-pre-wrap break-words text-xs text-on-surface/60">
                    {JSON.stringify(chunk.metadata, null, 2)}
                  </pre>
                </details>
              ))}
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                  disabled={busy || operationPending || parsePending}
                  className="mt-1"
                />
                我已檢查全部片段與來源資訊，確認執行 embedding 並加入目前環境的檢索資料庫。
              </label>
              <button
                disabled={!confirmed || busy || operationPending}
                onClick={() => {
                  void confirm();
                }}
                className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
              >
                確認並開始 embedding
              </button>
            </section>
          )}

          <CorpusDrafts
            refreshKey={jobRefresh}
            onOpen={(id) => {
              void openDraft(id);
            }}
          />
        </div>
      )}

      <CorpusJobs
        active={active}
        csrfToken={csrfToken}
        activeJobId={operation?.job_id}
        refreshKey={jobRefresh}
        onCompleted={completeJob}
        onOpenImport={(id) => {
          void openDraft(id);
        }}
      />
    </section>
  );
}

function KindSelect({
  value,
  onChange,
  disabled = false,
}: {
  value: CorpusKind;
  onChange: (value: CorpusKind) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block text-sm font-medium">
      資料類型
      <select
        aria-label="資料類型"
        className="input ml-2"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as CorpusKind)}
      >
        {Object.entries(KIND_LABELS).map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}

function RecordDetail({
  record,
  refreshOnOpen = false,
}: {
  record: CorpusRecord;
  refreshOnOpen?: boolean;
}) {
  const [latest, setLatest] = useState(record);
  const displayed = refreshOnOpen ? latest : record;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  const refresh = async (open: boolean) => {
    if (!refreshOnOpen) return;
    request.current?.abort();
    if (!open) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    try {
      const value = await getRecord(record.kind, record.id, controller.signal);
      if (!controller.signal.aborted) setLatest(value);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };

  return (
    <details
      className="rounded-lg border border-outline/20 p-3"
      onToggle={(event) => {
        if (event.target === event.currentTarget) {
          void refresh(event.currentTarget.open);
        }
      }}
    >
      <summary className="cursor-pointer break-all text-sm font-medium">
        {record.id} · {record.embedding?.dimensions ?? 0} 維
        {!record.source_id && " · 未核對完整來源"}
      </summary>
      {loading ? (
        <p role="status" className="mt-3 text-sm">
          正在確認最新片段…
        </p>
      ) : error ? (
        <p role="alert" className="mt-3 text-sm text-error">
          {error}
        </p>
      ) : (
        <>
          <pre className="mt-3 whitespace-pre-wrap break-words text-sm">{displayed.content}</pre>
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-on-surface/60">完整來源資訊</summary>
            <pre className="mt-2 whitespace-pre-wrap break-words text-xs">
              {JSON.stringify(displayed.metadata, null, 2)}
            </pre>
          </details>
        </>
      )}
    </details>
  );
}
