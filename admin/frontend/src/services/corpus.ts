import { adminFetch } from "./session";
import type { AdminJob } from "./api";

export type CorpusKind = "law" | "judgment" | "remedy";
export interface CorpusRecord { id: string; kind: CorpusKind; content: string; metadata: Record<string, unknown>; embedding: { dimensions: number }; source_id: string | null }
export interface CorpusSource {
  source_id: string; title: string; filename: string; kind: CorpusKind; status: string;
  revision: number; chunk_count: number; original_available: boolean; chunks?: CorpusRecord[]; next_cursor?: string | null;
}
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface ImportPreview {
  import_id: string; plan_hash: string; filename: string; title: string; kind: CorpusKind;
  chunk_count: number; chunks: Array<{ content: string; metadata: Record<string, unknown> }>;
  warnings: string[]; duplicate_source_id: string | null; expires_at: string; limits: Record<string, unknown>;
}
export const mappingLabels = {
  content: "片段內容（必填）", metadata: "來源資訊 JSON", law_name: "法規名稱", article_number: "條號",
  source_url: "來源網址", version: "版本", effective_date: "生效日期", checked_at: "核對日期",
  case_number: "案號", judgment_date: "裁判日期", cause: "案由", jurisdiction: "管轄機關",
} as const;
export type ColumnMapping = Partial<Record<keyof typeof mappingLabels, string>>;
export interface ImportDraft extends Partial<ImportPreview> {
  import_id: string; filename: string; title: string; kind: CorpusKind; status: string;
  parse_revision: number; parse_job_id?: string; job_id?: string;
  available_fields?: string[]; suggested_mapping?: ColumnMapping; column_mapping?: ColumnMapping; sample_rows?: Record<string, unknown>[];
  error?: { detail?: string; code?: string } | null;
  job?: CorpusJob;
}
export type CorpusJob = AdminJob<{ source_id?: string; import_id?: string; status?: string; parse_revision?: number }> & {
  kind?: string; created_at?: string; updated_at?: string; import_id?: string; source_id?: string;
  progress?: Record<string, unknown>; cancel_requested?: boolean;
};
export interface CorpusOperation { job_id?: string; status: string; source_id?: string; duplicate?: boolean }

export const listSources = (cursor: string | null, signal?: AbortSignal): Promise<Page<CorpusSource>> => adminFetch(`/corpus/sources?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, { signal });
export const listRecords = (kind: CorpusKind, cursor: string | null, signal?: AbortSignal): Promise<Page<CorpusRecord>> => adminFetch(`/corpus/records?kind=${kind}&limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, { signal });
export const getSource = (id: string, signal?: AbortSignal, cursor?: string | null): Promise<CorpusSource> => adminFetch(`/corpus/sources/${encodeURIComponent(id)}?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, { signal });
export const downloadSource = (id: string): Promise<Blob> => adminFetch(`/corpus/sources/${encodeURIComponent(id)}/download`);
export function previewImport(file: File, kind: CorpusKind, title: string, metadata: Record<string, unknown>, csrfToken: string, signal?: AbortSignal): Promise<ImportDraft> {
  const body = new FormData(); body.set("file", file); body.set("kind", kind); body.set("title", title); body.set("metadata", JSON.stringify(metadata));
  return adminFetch("/corpus/imports/preview", { method: "POST", body, signal }, csrfToken);
}
export const confirmImport = (preview: ImportPreview, csrfToken: string): Promise<CorpusOperation> => adminFetch(`/corpus/imports/${encodeURIComponent(preview.import_id)}/confirm`, { method: "POST", body: JSON.stringify({ plan_hash: preview.plan_hash }) }, csrfToken);
export const deleteSource = (source: CorpusSource, csrfToken: string): Promise<CorpusOperation> => adminFetch(`/corpus/sources/${encodeURIComponent(source.source_id)}`, { method: "DELETE", body: JSON.stringify({ revision: source.revision }) }, csrfToken);
export const getCorpusJob = (id: string, signal?: AbortSignal): Promise<CorpusJob> => adminFetch(`/jobs/${encodeURIComponent(id)}`, { signal });
export const listCorpusJobs = (cursor: string | null, signal?: AbortSignal): Promise<Page<CorpusJob>> => adminFetch(`/corpus/jobs?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, { signal });
export const cancelCorpusJob = (id: string, csrfToken: string): Promise<CorpusJob> => adminFetch(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }, csrfToken);
export const retryCorpusJob = (id: string, acknowledge: boolean, csrfToken: string): Promise<CorpusJob> => adminFetch(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST", body: JSON.stringify({ acknowledge_duplicate_cost: acknowledge }) }, csrfToken);
export const listImports = (cursor: string | null, signal?: AbortSignal): Promise<Page<ImportDraft>> => adminFetch(`/corpus/imports?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, { signal });
export const getImport = (id: string, signal?: AbortSignal): Promise<ImportDraft> => adminFetch(`/corpus/imports/${encodeURIComponent(id)}`, { signal });
export const mapImport = (draft: ImportDraft, columnMapping: ColumnMapping, csrfToken: string): Promise<ImportDraft> => adminFetch(`/corpus/imports/${encodeURIComponent(draft.import_id)}/mapping`, { method: "POST", body: JSON.stringify({ column_mapping: columnMapping, parse_revision: draft.parse_revision }) }, csrfToken);
export const resumeParse = (id: string, csrfToken: string): Promise<ImportDraft> => adminFetch(`/corpus/imports/${encodeURIComponent(id)}/parse`, { method: "POST" }, csrfToken);
