import { adminFetch } from './session';
import type { CorpusKind, CorpusRecord } from './corpus';
import type { RuntimeConfig } from './api';
import { retrievalSettings } from './retrievalSettings';

export type Kind = CorpusKind;
export type Relevance = 'relevant' | 'irrelevant' | 'unjudged';
export interface Evidence {
  key: string; kind: Kind; collection: string; doc_id: string; content: string;
  metadata: Record<string, unknown>; source_id: string | null; version: string | null;
  unit_id: string | null; content_sha256: string; evidence_sha256: string; manual_unit_id: string | null;
  retrieval_method?: string; distance?: number | null;
}
export interface Annotation { document: Evidence; relevance: Relevance }
export interface EvaluationCase {
  id: string; query: string; query_sha256: string; data_types: Kind[];
  privacy_confirmed: true; annotations: Annotation[]; reviewed: boolean; no_relevant: boolean;
}
export interface Dataset {
  id: string; name: string; archived: boolean; revision: number; published_revision: number | null;
  case_count: number; cases: EvaluationCase[];
}
export interface Settings {
  model: string; candidate_k: number; top_k: number; timeout_seconds: number;
  max_chars_per_doc: number; distance_threshold: number | null;
  provider_sort: 'auto' | 'latency' | 'throughput' | 'price'; preserve_data_types: boolean;
}
export type Stage = 'candidate' | 'before' | 'model' | 'final';
export interface Metric { hits: number | null; relevant: number; recall: number | null; hit_ids: string[] | null; returned_chunks: number | null }
export interface Aggregate { evaluated_cases: number; macro: number | null; micro: number | null }
export interface Summary {
  total: number; finished: number; measured: number; completion_rate: number; measurement_rate: number; unmeasured: number;
  statuses: Record<string, number>; stages: Record<Stage, Aggregate>; per_kind: Record<Kind, Record<Stage, Aggregate>>;
  mixed: Record<Stage, Aggregate>; p95_ms: number | null;
}
export interface CaseResult {
  case_id: string; query: string; data_types: Kind[]; status: string; elapsed_ms: number;
  case: EvaluationCase; candidates?: Evidence[]; candidate_ref?: unknown; retrieval_binding: string;
  model_order?: string[] | null; final_order?: string[]; scores?: Record<string, number>;
  selection_reasons?: Record<string, string[]>; excerpts?: { key: string; text: string; sha256: string }[];
  rerank?: { status: string; model: string; elapsed_ms: number; failure_type?: string };
  error?: { code: string; detail: string };
  metrics: { stages: Record<Stage, Metric>; delta: number | null; missing_candidates: string[];
    missing_final: string[]; dropped: string[]; gained: string[]; unmapped_keys: string[] } | null;
}
export interface Run {
  id: string; dataset_id: string; dataset_revision: number; dataset_sha256: string; job_id: string;
  status: string; settings: Settings; results: CaseResult[]; summary: Summary; total: number;
  created_at: string; error?: { code: string; detail: string } | null; archived: boolean;
}
export interface Preparation { job_id?: string; status: string; result: { documents: Evidence[] } | null; error?: { detail: string } }
export interface Page<T> { items: T[]; next_cursor: string | null }
const root = '/rerank-tests';
const json = (value: unknown) => JSON.stringify(value);
const match = (revision: number) => ({ 'If-Match': String(revision) });
export const defaultSettings = (config: RuntimeConfig): Settings => {
  const value = retrievalSettings(config);
  return { model: value.rag_rerank_model, candidate_k: Math.max(config.rag_retrieval_top_k, value.rag_rerank_candidate_k),
    top_k: config.rag_retrieval_top_k, timeout_seconds: value.rag_rerank_timeout_seconds,
    max_chars_per_doc: value.rag_rerank_max_chars_per_doc, distance_threshold: config.rag_distance_threshold ?? null,
    provider_sort: value.openrouter_provider_sort, preserve_data_types: true };
};
export function validateSettings(value: Settings, types: Kind[][]): string | null {
  if (!value.model.trim()) return '請指定 rerank 模型。';
  if (!Number.isInteger(value.candidate_k) || value.candidate_k < 1 || value.candidate_k > 50) return 'N 必須為 1–50 的整數。';
  if (!Number.isInteger(value.top_k) || value.top_k < 1 || value.top_k > 20 || value.top_k > value.candidate_k) return 'K 必須為 1–20 的整數，且不能超過 N。';
  if (value.preserve_data_types && types.some(kinds => kinds.length > value.top_k)) return 'K 不得小於選取的資料類型數。';
  if (!Number.isFinite(value.timeout_seconds) || value.timeout_seconds < 0.1 || value.timeout_seconds > 10) return '等待上限須為 0.1–10 秒。';
  if (!Number.isInteger(value.max_chars_per_doc) || value.max_chars_per_doc < 100 || value.max_chars_per_doc > 20000) return '文件節錄上限須為 100–20000 字。';
  if (value.distance_threshold !== null && (!Number.isFinite(value.distance_threshold) || value.distance_threshold < 0 || value.distance_threshold > 2)) return '距離門檻須為 0–2，或留空。';
  return null;
}
export const listDatasets = (cursor?: string | null) => adminFetch<Page<Dataset>>(`${root}/datasets${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
export const getDataset = (id: string) => adminFetch<Dataset>(`${root}/datasets/${id}`);
export const createDataset = (name: string, token: string) => adminFetch<Dataset>(`${root}/datasets`, { method: 'POST', body: json({ name }) }, token);
export const updateDataset = (d: Dataset, name: string, archived: boolean, token: string) => adminFetch<Dataset>(`${root}/datasets/${d.id}`, { method: 'PATCH', headers: match(d.revision), body: json({ name, archived }) }, token);
export function saveCase(d: Dataset, value: Omit<EvaluationCase, 'id' | 'query_sha256'>, id: string | null, token: string) {
  const annotations = value.annotations.map(a => ({ kind: a.document.kind, doc_id: a.document.doc_id, relevance: a.relevance, evidence_sha256: a.document.evidence_sha256, manual_unit_id: a.document.manual_unit_id }));
  return adminFetch<Dataset>(`${root}/datasets/${d.id}/cases${id ? `/${id}` : ''}`, { method: id ? 'PUT' : 'POST', headers: match(d.revision), body: json({ ...value, annotations }) }, token);
}
export const deleteCase = (d: Dataset, id: string, token: string) => adminFetch<Dataset>(`${root}/datasets/${d.id}/cases/${id}`, { method: 'DELETE', headers: match(d.revision) }, token);
export const publishDataset = (d: Dataset, token: string) => adminFetch<{ revision: number }>(`${root}/datasets/${d.id}/publish`, { method: 'POST', headers: match(d.revision) }, token);
export const resolveDocument = (kind: Kind, doc_id: string, token: string, manual_unit_id: string | null = null) => adminFetch<Evidence>(`${root}/documents/resolve`, { method: 'POST', body: json({ kind, doc_id, manual_unit_id }) }, token);
export const prepareCandidates = (d: Dataset, case_id: string, settings: Settings, token: string, key: string) => adminFetch<Preparation>(`${root}/datasets/${d.id}/prepare`, { method: 'POST', headers: { ...match(d.revision), 'Idempotency-Key': key }, body: json({ case_id, settings, external_confirmed: true }) }, token);
export const getPreparation = (dataset: string, caseId: string) => adminFetch<Preparation>(`${root}/datasets/${dataset}/cases/${caseId}/preparation`);
export const listRuns = (cursor?: string | null) => adminFetch<Page<Run>>(`${root}/runs${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
export const getRun = (id: string) => adminFetch<Run>(`${root}/runs/${id}`);
export const startRun = (d: Dataset, settings: Settings, token: string, key: string, case_ids: string[] | null, reuse_run_id: string | null) => adminFetch<Run>(`${root}/runs`, { method: 'POST', headers: { 'Idempotency-Key': key }, body: json({ dataset_id: d.id, revision: d.published_revision, settings, case_ids, reuse_run_id, external_confirmed: true }) }, token);
export const archiveRun = (id: string, token: string) => adminFetch<Run>(`${root}/runs/${id}/archive`, { method: 'POST' }, token);
export const downloadRun = (id: string, format: 'json' | 'csv') => adminFetch<Blob>(`${root}/runs/${id}/export?format=${format}`);
export const kindLabels: Record<Kind, string> = { law: '法規', judgment: '判決', remedy: '救濟' };
export const stageLabels: Record<Stage, string> = { candidate: '候選 Recall@N', before: '重排前 Recall@K', model: '模型排序 Recall@K', final: '最終 Recall@K' };
export const percentage = (value: number | null | undefined) => value == null ? '不適用' : `${(value * 100).toFixed(1)}%`;
export function evidenceTitle(doc: Evidence) { return [doc.metadata.law_name, doc.metadata.article_number ? `第 ${doc.metadata.article_number} 條` : '', doc.metadata.title || doc.metadata.source || doc.source_id || doc.doc_id].filter(Boolean).join(' · '); }
export const getRevision = (id: string, revision: number) => adminFetch<{ cases: EvaluationCase[] }>(`${root}/datasets/${id}/revisions/${revision}`);

export const browseDocuments = (kind: Kind, cursor: string | null) => adminFetch<Page<CorpusRecord>>(`${root}/documents?kind=${kind}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
