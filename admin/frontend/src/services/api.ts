export * from "@rag/shared/api";
import {
  ApiError, parseChatResponse, parseAdminApplicableLawTestResult,
  type AdminChatTestResult, type AdminApplicableLawTestResult, type ChatRequest,
  type RuntimeConfig, type RuntimeConfigUpdate, type ScenarioSkill, type ScenarioSkillInput,
} from "@rag/shared/api";
import { reviewedRequestBody } from "@rag/shared/clientPrivacy";
import { adminFetch, AdminRequestError } from "./session";

export interface AdminJob<T = unknown> {
  job_id: string;
  status: "queued" | "pending" | "running" | "succeeded" | "completed" | "failed" | "cancelled" | "interrupted" | "needs_review";
  result?: T;
  error?: { detail?: string; code?: string; status?: number; http_status?: number } | string | null;
}

function waitForPoll(signal?: AbortSignal, delayMs = 1000): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = () => { signal?.removeEventListener("abort", abort); resolve(); };
    const timer = setTimeout(finish, delayMs);
    const abort = () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); reject(signal?.reason ?? new DOMException("Aborted", "AbortError")); };
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
  });
}

/** A single turn is queued once; polling never resubmits a paid model call. */
async function runTestJob(csrfToken: string, body: string, signal?: AbortSignal): Promise<unknown> {
  signal?.throwIfAborted();
  const deadline = Date.now() + 10 * 60_000;
  let jobId: string | undefined;
  const cancel = () => {
    if (jobId) void adminFetch(`/test-jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }, csrfToken).catch(() => undefined);
  };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    const created = await adminFetch<AdminJob>("/test-jobs", {
      method: "POST", body, signal, headers: { "Idempotency-Key": crypto.randomUUID() },
    }, csrfToken);
    if (!created.job_id) throw new ApiError(502, "後端未提供測試工作識別碼");
    jobId = created.job_id;
    while (true) {
      signal?.throwIfAborted();
      if (Date.now() > deadline) {
        cancel();
        throw new ApiError(504, "測試工作等待逾時", "可能已呼叫模型；請確認後手動重試。");
      }
      let job: AdminJob;
      try { job = await adminFetch<AdminJob>(`/test-jobs/${encodeURIComponent(jobId)}`, { signal }); }
      catch (error) {
        if (error instanceof AdminRequestError && error.status === 429) {
          await waitForPoll(signal, (error.retryAfterSeconds ?? 60) * 1000);
          continue;
        }
        throw error;
      }
      if (job.status === "succeeded" || job.status === "completed") {
        if (job.result === undefined) throw new ApiError(502, "測試工作缺少完整結果");
        return job.result;
      }
      if (["failed", "cancelled", "interrupted", "needs_review"].includes(job.status)) {
        const error = typeof job.error === "object" && job.error ? job.error : {};
        const status = error.http_status ?? error.status ?? (error.code?.includes("contract_mismatch") ? 409 : 502);
        throw new ApiError(status, "測試工作未完成", error.detail ?? "請檢查結果後手動重試；系統不會自動重送模型請求。");
      }
      if (!["pending", "queued", "running"].includes(job.status)) throw new ApiError(502, "測試工作狀態格式不正確");
      await waitForPoll(signal);
    }
  } finally { signal?.removeEventListener("abort", cancel); }
}

export async function runAdminChatTest(csrfToken: string, request: ChatRequest, overrides: RuntimeConfigUpdate, signal?: AbortSignal, expectedContractSha256?: string): Promise<AdminChatTestResult> {
  const reviewedBody = reviewedRequestBody(request);
  const result = await runTestJob(csrfToken, `{"request":${reviewedBody},"overrides":${JSON.stringify(overrides)}${expectedContractSha256 ? `,"expected_chat_test_contract_sha256":${JSON.stringify(expectedContractSha256)}` : ""}}`, signal) as AdminChatTestResult;
  if (!result || typeof result.diagnostics !== "object" || !result.diagnostics || Array.isArray(result.diagnostics)) throw new ApiError(502, "診斷回應格式不正確");
  return { response: parseChatResponse(result.response, request.contract_version ?? 1), diagnostics: result.diagnostics };
}
export async function runAdminApplicableLawTest(csrfToken: string, request: ChatRequest, overrides: RuntimeConfigUpdate, signal?: AbortSignal, expectedContractSha256?: string): Promise<AdminApplicableLawTestResult> {
  const reviewedBody = reviewedRequestBody(request);
  if (!csrfToken.trim()) throw new ApiError(401, "請先登入管理員");
  if (request.contract_version !== 4) throw new ApiError(422, "適用法規預測只支援 v4");
  const result = await runTestJob(csrfToken, `{"request":${reviewedBody},"overrides":${JSON.stringify(overrides)},"output_contract":"applicable_laws_v1"${expectedContractSha256 ? `,"expected_applicable_laws_contract_sha256":${JSON.stringify(expectedContractSha256)}` : ""}}`, signal);
  return parseAdminApplicableLawTestResult(result);
}
export function getRuntimeConfig(csrfToken?: string): Promise<RuntimeConfig> { return adminFetch("/config", {}, csrfToken); }
const revisionHeaders = (revision?: string): Record<string, string> => revision === undefined ? {} : { "If-Match": revision };
export function updateRuntimeConfig(csrfToken: string, request: RuntimeConfigUpdate & { revision?: string }): Promise<RuntimeConfig> {
  const { revision, ...updates } = request;
  return adminFetch("/config", { method: "PUT", headers: revisionHeaders(revision), body: JSON.stringify(updates) }, csrfToken);
}
export function seedRuntimeConfig(csrfToken: string, revision?: string): Promise<RuntimeConfig> { return adminFetch("/config/seed", { method: "POST", headers: revisionHeaders(revision) }, csrfToken); }
export function resetRuntimeConfig(csrfToken: string, revision?: string): Promise<RuntimeConfig> { return adminFetch("/config/reset", { method: "POST", headers: revisionHeaders(revision) }, csrfToken); }
export function seedScenarioScripts(csrfToken: string, revision?: number): Promise<{ script_ids: string[]; revision?: number }> { return adminFetch("/scenario-scripts/seed", { method: "POST", headers: revisionHeaders(revision?.toString()) }, csrfToken); }
export function getScenarioSkills(csrfToken?: string): Promise<{ skills: ScenarioSkill[]; revision?: number }> { return adminFetch("/scenario-scripts", {}, csrfToken); }
export function updateScenarioSkill(csrfToken: string, id: string, skill: ScenarioSkillInput & { catalog_revision?: number }): Promise<ScenarioSkill> {
  const { catalog_revision, ...input } = skill;
  return adminFetch(`/scenario-scripts/${encodeURIComponent(id)}`, { method: "PUT", headers: revisionHeaders(catalog_revision?.toString()), body: JSON.stringify(input) }, csrfToken);
}
export function deleteScenarioSkill(csrfToken: string, id: string, revision?: number): Promise<void> { return adminFetch(`/scenario-scripts/${encodeURIComponent(id)}`, { method: "DELETE", headers: revisionHeaders(revision?.toString()) }, csrfToken); }
export function getRerankModels(csrfToken?: string): Promise<{ data: { id: string; name: string }[] }> { return adminFetch("/rerank-models", {}, csrfToken); }
