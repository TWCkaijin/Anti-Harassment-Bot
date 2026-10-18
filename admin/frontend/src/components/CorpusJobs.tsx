import { useEffect, useRef, useState } from "react";
import { ApiError } from "../services/api";
import { AdminRequestError } from "../services/session";
import {
  cancelCorpusJob,
  getCorpusJob,
  listCorpusJobs,
  retryCorpusJob,
  type CorpusJob,
} from "../services/corpus";

const TERMINAL_STATUSES = new Set([
  "succeeded",
  "completed",
  "failed",
  "cancelled",
  "needs_review",
]);

const STATUS_LABELS: Record<string, string> = {
  queued: "排隊中",
  running: "處理中",
  succeeded: "完成",
  completed: "完成",
  failed: "失敗",
  cancelled: "已取消",
  needs_review: "需要檢查",
};

const KIND_LABELS: Record<string, string> = {
  corpus_parse: "解析檔案",
  corpus_import: "匯入資料",
  corpus_delete: "刪除來源",
};

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  if (error instanceof Error) return error.message;
  return "無法取得工作狀態";
}

export default function CorpusJobs({
  csrfToken,
  activeJobId,
  refreshKey,
  onCompleted,
  onOpenImport,
  active = true,
}: {
  csrfToken: string;
  activeJobId?: string;
  refreshKey: number;
  active?: boolean;
  onCompleted: (job: CorpusJob) => void;
  onOpenImport: (id: string) => void;
}) {
  const [items, setItems] = useState<CorpusJob[]>([]);
  const [pages, setPages] = useState<Array<string | null>>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [choice, setChoice] = useState<{ id: string; activeId?: string }>();
  const [jobDetail, setSelected] = useState<CorpusJob | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const callbacks = useRef({ onCompleted });
  const notified = useRef(new Set<string>());
  const pendingJobs = useRef(new Set<string>());

  const cursor = pages.at(-1) ?? null;
  const selectedId =
    choice?.activeId === activeJobId && choice
      ? choice.id
      : activeJobId ??
        items.find((job) => !TERMINAL_STATUSES.has(job.status))?.job_id ??
        items[0]?.job_id;
  const selected = jobDetail?.job_id === selectedId ? jobDetail : null;

  useEffect(() => {
    callbacks.current = { onCompleted };
  }, [onCompleted]);

  useEffect(() => {
    if (!active) return;
    const abort = new AbortController();
    void listCorpusJobs(cursor, abort.signal)
      .then((page) => {
        if (abort.signal.aborted) return;
        for (const job of page.items) {
          if (!TERMINAL_STATUSES.has(job.status)) {
            pendingJobs.current.add(job.job_id);
          }
        }
        setItems(page.items);
        setNextCursor(page.next_cursor);
      })
      .catch((failure) => {
        if (!abort.signal.aborted) setError(describeError(failure));
      });
    return () => abort.abort();
  }, [cursor, refresh, refreshKey, csrfToken, activeJobId, active]);

  useEffect(() => {
    if (!active || !selectedId) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const job = await getCorpusJob(selectedId, abort.signal);
        if (abort.signal.aborted) return;
        setSelected(job);
        setAcknowledged(false);
        setError("");
        setItems((previous) =>
          previous.map((item) => (item.job_id === job.job_id ? job : item)),
        );

        if (TERMINAL_STATUSES.has(job.status)) {
          const notification = `${job.job_id}:${job.status}:${job.updated_at ?? ""}`;
          if (
            !notified.current.has(notification) &&
            (pendingJobs.current.has(job.job_id) || activeJobId === job.job_id)
          ) {
            notified.current.add(notification);
            pendingJobs.current.delete(job.job_id);
            callbacks.current.onCompleted(job);
          }
        } else {
          pendingJobs.current.add(job.job_id);
          timer = setTimeout(() => {
            void poll();
          }, 2000);
        }
      } catch (failure) {
        if (abort.signal.aborted) return;
        setError(describeError(failure));
        if (!(failure instanceof ApiError && [401, 403, 404, 410].includes(failure.status))) {
          const delay =
            failure instanceof AdminRequestError && failure.status === 429
              ? (failure.retryAfterSeconds ?? 60) * 1000
              : 5000;
          timer = setTimeout(() => {
            void poll();
          }, delay);
        }
      }
    };

    void poll();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [selectedId, refresh, csrfToken, active, activeJobId]);

  const act = async (action: "cancel" | "retry") => {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      const job =
        action === "cancel"
          ? await cancelCorpusJob(selected.job_id, csrfToken)
          : await retryCorpusJob(selected.job_id, acknowledged, csrfToken);
      setSelected(job);
      setAcknowledged(false);
      setRefresh((value) => value + 1);
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setBusy(false);
    }
  };

  const importId = selected?.result?.import_id ?? selected?.import_id;

  return (
    <section aria-label="背景工作紀錄" className="space-y-3 rounded-lg border border-outline/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-bold">背景工作紀錄</h4>
        <button
          className="text-sm text-primary underline"
          onClick={() => setRefresh((value) => value + 1)}
        >
          重新整理工作
        </button>
      </div>
      <p className="text-xs text-on-surface/65">
        重新登入或重新載入後，可從伺服器恢復查看解析、匯入與刪除工作的進度。
      </p>
      {error && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
      <div className="space-y-2">
        {items.map((job) => (
          <button
            key={job.job_id}
            aria-pressed={selectedId === job.job_id}
            onClick={() => {
              setChoice({ id: job.job_id, activeId: activeJobId });
              setAcknowledged(false);
            }}
            className={`block w-full rounded-lg border p-3 text-left text-sm ${
              selectedId === job.job_id
                ? "border-primary bg-primary-container/30"
                : "border-outline/20"
            }`}
          >
            <span className="font-medium">
              {KIND_LABELS[job.kind ?? ""] ?? "資料工作"} ·{" "}
              {STATUS_LABELS[job.status] ?? job.status}
            </span>
            <span className="mt-1 block break-all font-mono text-xs text-on-surface/60">
              {job.job_id}
            </span>
            {job.created_at && (
              <time className="text-xs text-on-surface/60">
                {new Date(job.created_at).toLocaleString()}
              </time>
            )}
          </button>
        ))}
        {!items.length && <p className="text-sm text-on-surface/60">目前沒有背景工作紀錄。</p>}
      </div>
      <div className="flex gap-3 text-sm">
        <button
          disabled={pages.length < 2}
          className="text-primary disabled:opacity-40"
          onClick={() => setPages((value) => value.slice(0, -1))}
        >
          上一頁工作
        </button>
        <button
          disabled={!nextCursor}
          className="text-primary disabled:opacity-40"
          onClick={() => setPages((value) => [...value, nextCursor])}
        >
          下一頁工作
        </button>
      </div>
      {selected && (
        <article aria-label="工作詳細狀態" className="space-y-3 border-t border-outline/30 pt-3">
          <p role="status" className="text-sm">
            {KIND_LABELS[selected.kind ?? ""] ?? "資料工作"}：
            {STATUS_LABELS[selected.status] ?? selected.status}
            {selected.cancel_requested ? "（已要求取消，等待目前步驟結束）" : ""}
          </p>
          {selected.progress && Object.keys(selected.progress).length > 0 && (
            <pre className="whitespace-pre-wrap break-words text-xs">
              {JSON.stringify(selected.progress, null, 2)}
            </pre>
          )}
          {selected.error && (
            <p className="text-sm text-error">
              {(typeof selected.error === "string" ? selected.error : selected.error.detail) ??
                "工作未完成，請檢查後再操作。"}
            </p>
          )}
          {importId && (
            <button
              className="text-sm text-primary underline"
              onClick={() => onOpenImport(importId)}
            >
              開啟匯入草稿
            </button>
          )}
          {selected.status === "needs_review" && (
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
                className="mt-1"
              />
              我已檢查工作紀錄，理解上次外部請求的結果不明；重試可能產生重複費用。
            </label>
          )}
          <div className="flex flex-wrap gap-3">
            {["queued", "running"].includes(selected.status) && (
              <button
                disabled={busy || selected.cancel_requested}
                className="rounded border border-error px-3 py-2 text-sm text-error disabled:opacity-40"
                onClick={() => {
                  void act("cancel");
                }}
              >
                取消工作
              </button>
            )}
            {["queued", "failed", "needs_review"].includes(selected.status) && (
              <button
                disabled={busy || (selected.status === "needs_review" && !acknowledged)}
                className="rounded border border-primary px-3 py-2 text-sm text-primary disabled:opacity-40"
                onClick={() => {
                  void act("retry");
                }}
              >
                重試工作
              </button>
            )}
          </div>
        </article>
      )}
    </section>
  );
}
