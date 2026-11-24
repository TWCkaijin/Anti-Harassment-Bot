/** Small presentational pieces of the corpus admin view. */
import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@rag/shared/browser";
import { getRecord, type CorpusKind, type CorpusRecord } from "../services/corpus";
import { KIND_LABELS } from "../services/corpusLabels";

export function KindSelect({
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

export function RecordDetail({
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
      if (!controller.signal.aborted) setError(errorMessage(failure, "資料操作失敗"));
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
