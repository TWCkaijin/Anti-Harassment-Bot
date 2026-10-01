/** Local-only review of the complete chat payload. No text leaves this module. */
import type { ChatRequest } from "./api";
import { FACT_KEYS, isCaseContext, isFactKey, isRecord, type CaseContext, type ClarificationAnswer } from "./caseFacts";
import { validateImageDataUrl } from "./imageUpload";

export interface PrivacyField { path: string[]; label: string; text: string; readOnly?: boolean }
export interface PrivacyFinding { path: string[]; type: string; count: number }
export interface PrivacyDraft { request: ChatRequest; fields: PrivacyField[]; findings: PrivacyFinding[]; warnings: string[] }
export class ClientPrivacyError extends Error {
  readonly code: string;
  constructor(code: string, message: string) { super(message); this.name = "ClientPrivacyError"; this.code = code; }
}
const drafts = new WeakMap<PrivacyDraft, { body: string; hiddenTerms: string[] }>();
const approved = new WeakMap<ChatRequest, string>();
const invalid = () => new ClientPrivacyError("invalid_request", "送出內容格式不正確，請重新整理後再試。");
const integer = (value: unknown, min = 0): value is number => Number.isSafeInteger(value) && Number(value) >= min;
const textValue = (value: unknown): string => { if (typeof value !== "string") throw invalid(); return value; };
const canonicalText = (value: string) => value.replace(/[Ａ-Ｚａ-ｚ０-９＠．＿％＋－]/g, char => String.fromCharCode(char.charCodeAt(0) - 0xFEE0)).replace(/[\u200B-\u200D\u2060\uFEFF]/g, "");

function datePair(value: string, prefix: string): boolean {
  if (/(?:信用卡|卡號|card(?:\s+number)?)\s*[:：]?\s*$/i.test(prefix)) return false;
  const match = /^((?:19|20)\d{6})[-\s]((?:19|20)\d{6})$/.exec(value);
  return !!match && match.slice(1).every(part => {
    const y = Number(part.slice(0, 4)), m = Number(part.slice(4, 6)), d = Number(part.slice(6));
    const parsed = new Date(Date.UTC(y, m - 1, d));
    return parsed.getUTCFullYear() === y && parsed.getUTCMonth() === m - 1 && parsed.getUTCDate() === d;
  });
}

/** Conservative format checks plus explicit local hidden terms; never a claim of complete anonymization. */
function redact(value: string, path: string[], hiddenTerms: string[], findings: PrivacyFinding[]): string {
  let result = canonicalText(value);
  if (result !== value) findings.push({ path, type: "正規化全形字元或隱藏字元", count: 1 });
  const replace = (pattern: RegExp, replacement: string, type: string, keep?: (match: string, start: number, source: string) => boolean) => {
    let count = 0;
    const source = result;
    result = source.replace(pattern, (match: string, ...args: unknown[]) => {
      const start = args[args.length - 2] as number;
      if (keep?.(match, start, source)) return match;
      count += 1;
      return replacement;
    });
    if (count) findings.push({ path, type, count });
  };
  // Literal replacements happen before format checks so the same local alias is used everywhere.
  hiddenTerms.map((term, index) => ({ term: canonicalText(term), index })).sort((a, b) => b.term.length - a.term.length).forEach(({ term, index }) => {
    if (!term) return;
    const parts = result.split(term);
    if (parts.length > 1) {
      result = parts.join(`[已隱藏資訊${index + 1}]`);
      findings.push({ path, type: "自訂隱藏內容", count: parts.length - 1 });
    }
  });
  replace(/https?:\/\/[^\s<>「」『』，。；]+/gi, "[連結已隱藏]", "連結");
  replace(/(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])/g, "[電子郵件]", "電子郵件");
  replace(/(?<![A-Za-z0-9])(?:09\d{2}[-\s]?\d{3}[-\s]?\d{3}|\+886[-\s]?9\d{2}[-\s]?\d{3}[-\s]?\d{3})(?![A-Za-z0-9])/g, "[手機號碼]", "手機號碼");
  replace(/(?<![A-Za-z0-9])(?:\(0[2-8]\d?\)\s*|0[2-8]\d?[-\s]?)[0-9]{3,4}[-\s]?[0-9]{4}(?![A-Za-z0-9])/g, "[電話號碼]", "電話號碼");
  replace(/(?<![A-Za-z0-9])(?:[A-Z][1289]\d{8}|[A-Z][A-D]\d{8})(?![A-Za-z0-9])/gi, "[身分證件號碼]", "身分證件號碼");
  replace(/(?<![A-Za-z0-9])\d(?:[-\s]?\d){12,18}(?![A-Za-z0-9])/g, "[卡號或長編號]", "卡號或長編號", (match, start, source) => datePair(match, source.slice(Math.max(0, start - 20), start)));
  replace(/(?<![A-Za-z0-9.])(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?![A-Za-z0-9.])/g, "[IP位址]", "IP位址", (match, start, source) => {
    const prefix = source.slice(Math.max(0, start - 24), start), suffix = source.slice(start + match.length, start + match.length + 16);
    if (/(?:IPv?4?|IP位址|IP地址|網路位址|主機位址|來源位址)\s*[:：]?\s*$/i.test(prefix)) return false;
    return /(?:小節|章節|條款|段落|編號|條目|版本|版號)\s*[:：]?\s*$/.test(prefix) || /^\s*(?:小節|章節|條款|段落|版本|版號|節|章|款|項)/.test(suffix);
  });
  replace(/(?<![A-Za-z0-9:])(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}(?![A-Za-z0-9:])/g, "[IP位址]", "IP位址");
  replace(/(?<![A-Za-z0-9:])(?:[A-Fa-f0-9]{1,4}:){0,6}[A-Fa-f0-9]{0,4}::(?:[A-Fa-f0-9]{1,4}:){0,6}[A-Fa-f0-9]{0,4}(?![A-Za-z0-9:])/g, "[IP位址]", "IP位址");
  replace(/(?:真實姓名|姓名|住家地址|聯絡地址|居住地址|地址)\s*[:：]\s*[^\n，。；;、[\]]+/g, "[姓名或地址已隱藏]", "標示的姓名或地址");
  return result;
}

function freeze<T>(value: T): T {
  if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}

function decodeToken(token: string, version: number): Record<string, unknown> | null {
  try {
    const parts = /^([A-Za-z0-9_-]{1,8100})\.([a-f0-9]{64})$/.exec(token);
    if (!parts) return null;
    const bytes = Uint8Array.from(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")), char => char.charCodeAt(0));
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const data: unknown = JSON.parse(decoded);
    const common = ["question_id", "selection_mode", "max_selections", "context_scope", "allowed_values"];
    if (!isRecord(data) || Object.keys(data).some(key => ![...common, ...(version === 4 ? ["contract_version", "context_revision"] : ["fact_key"])].includes(key))) return null;
    if (typeof data.question_id !== "string" || !/^[a-zA-Z0-9_.:-]{1,80}$/.test(data.question_id)
      || !["single", "multiple"].includes(String(data.selection_mode)) || !integer(data.max_selections, 1) || data.max_selections > 4
      || !["personal", "scenario"].includes(String(data.context_scope)) || !Array.isArray(data.allowed_values)
      || data.allowed_values.length > 8 || !data.allowed_values.every(value => typeof value === "string" && value.length <= 300)) return null;
    if (version === 4 ? data.contract_version !== 4 || !integer(data.context_revision) : !isFactKey(data.fact_key)) return null;
    // The server emits compact JSON. Unknown whitespace/extra data is not sent back blindly.
    if (JSON.stringify(data) !== decoded) return null;
    return data;
  } catch { return null; }
}

function buildDraft(input: ChatRequest, hiddenTerms: string[]): PrivacyDraft {
  if (!isRecord(input) || !Array.isArray(input.history) || typeof input.use_rag !== "boolean") throw invalid();
  const findings: PrivacyFinding[] = [], fields: PrivacyField[] = [], warnings: string[] = [];
  const field = (value: unknown, path: string[], label: string) => {
    const text = redact(textValue(value), path, hiddenTerms, findings);
    fields.push({ path, label, text }); return text;
  };
  const request: ChatRequest = {
    message: field(input.message, ["message"], "本次訊息"),
    history: input.history.map((item, index) => {
      if (!isRecord(item) || !["user", "assistant"].includes(String(item.role))) throw invalid();
      return { role: item.role as "user" | "assistant", content: field(item.content, ["history", String(index), "content"], `歷史 ${index + 1}・${item.role === "user" ? "使用者" : "AI"}`) };
    }),
    use_rag: input.use_rag,
  };
  if (input.image_base64 !== undefined && input.image_base64 !== null) {
    request.image_base64 = validateImageDataUrl(input.image_base64);
    warnings.push("圖片將以原圖傳送，不會自動遮蔽其中的姓名、臉孔或其他資訊。請確認預覽內容。");
  }
  if (input.contract_version !== undefined) {
    if (![2, 3, 4].includes(input.contract_version)) throw invalid();
    request.contract_version = input.contract_version;
  }
  if (input.regenerate_from_summary !== undefined) {
    if (typeof input.regenerate_from_summary !== "boolean") throw invalid();
    request.regenerate_from_summary = input.regenerate_from_summary;
  }
  if (input.case_context !== undefined) {
    const context = input.case_context;
    if (!isRecord(context) || !integer(context.revision) || !isRecord(context.facts)) throw invalid();
    if (context.schema_version === 3) {
      if (!["user", "model", "migration"].includes(context.summary_origin)) throw invalid();
      request.case_context = { schema_version: 3, revision: context.revision, facts: {}, summary_origin: context.summary_origin,
        summary: field(context.summary, ["case_context", "summary"], "本次共同摘要") };
    } else {
      if (context.schema_version !== 1 && context.schema_version !== 2) throw invalid();
      const facts: CaseContext["facts"] = {};
      for (const key of FACT_KEYS) {
        const fact = context.facts[key];
        if (fact === undefined) continue;
        if (!isRecord(fact) || !["provided", "unknown", "declined"].includes(String(fact.status))) throw invalid();
        const path = ["case_context", "facts", key, "value"];
        facts[key] = fact.status !== "provided" ? { status: fact.status as "unknown" | "declined" } : { status: "provided",
          value: Array.isArray(fact.value) ? fact.value.map((value, index) => field(value, [...path, String(index)], `舊摘要・${key} ${index + 1}`)) : field(fact.value, path, `舊摘要・${key}`) };
      }
      request.case_context = { schema_version: context.schema_version, revision: context.revision, facts };
    }
  }
  if (input.clarification_answer !== undefined) {
    const answer = input.clarification_answer;
    if (!isRecord(answer)) throw invalid();
    let safe = isRecord(answer) && typeof answer.question_id === "string" && /^[a-zA-Z0-9_.:-]{1,80}$/.test(answer.question_id)
      && ["provided", "unknown", "declined"].includes(String(answer.status));
    const value = answer.value;
    safe = safe && (answer.status === "provided" ? typeof value === "string" || Array.isArray(value) && value.every(item => typeof item === "string") : value == null);
    const candidate: ClarificationAnswer = { question_id: answer.question_id, status: answer.status, ...(answer.fact_key !== undefined ? { fact_key: answer.fact_key } : {}) };
    if (value != null) candidate.value = Array.isArray(value) ? [...value] : value;
    if (answer.fact_key !== undefined) safe = safe && isFactKey(answer.fact_key);
    if (answer.context_revision !== undefined) { safe = safe && integer(answer.context_revision); candidate.context_revision = answer.context_revision; }
    if (answer.selection_mode !== undefined) { safe = safe && ["single", "multiple"].includes(answer.selection_mode); candidate.selection_mode = answer.selection_mode; }
    if (answer.max_selections !== undefined) { safe = safe && integer(answer.max_selections, 1) && answer.max_selections <= 4; candidate.max_selections = answer.max_selections; }
    if (answer.context_scope !== undefined) { safe = safe && ["personal", "scenario"].includes(answer.context_scope); candidate.context_scope = answer.context_scope; }
    if (answer.allowed_values !== undefined) { safe = safe && Array.isArray(answer.allowed_values) && answer.allowed_values.every(item => typeof item === "string"); candidate.allowed_values = answer.allowed_values; }
    let decoded: Record<string, unknown> | null = null;
    if (answer.validation_token !== undefined) {
      decoded = typeof answer.validation_token === "string" ? decodeToken(answer.validation_token, input.contract_version ?? 2) : null;
      safe = safe && decoded !== null;
      if (decoded) {
        for (const key of ["question_id", "fact_key", "context_revision", "selection_mode", "max_selections", "context_scope", "allowed_values"] as const) {
          if (candidate[key] !== undefined && JSON.stringify(candidate[key]) !== JSON.stringify(decoded[key])) safe = false;
        }
      }
    } else if (input.contract_version === 3 || input.contract_version === 4) safe = false;
    const readableData = { answer: candidate, ...(decoded ? { signed_options: decoded } : {}) };
    // Check original leaves, before JSON escaping can conceal newlines or quotes
    // from the same checks applied to message/history/summary text.
    const checkLeaves = (value: unknown, path: string[]) => {
      if (typeof value === "string") {
        if (redact(value, path, hiddenTerms, findings) !== value) safe = false;
      } else if (value && typeof value === "object") {
        Object.entries(value).forEach(([key, child]) => checkLeaves(child, [...path, key]));
      }
    };
    checkLeaves(readableData, ["clarification_answer"]);
    const readable = JSON.stringify(readableData, null, 2);
    if (safe) {
      if (answer.validation_token) candidate.validation_token = answer.validation_token;
      request.clarification_answer = candidate;
      fields.push({ path: ["clarification_answer"], label: "追問選項資料（含簽章中可讀的內容）", text: readable, readOnly: true });
    } else warnings.push("追問選項資料含待隱藏內容或無法完整檢查，已移除；本次會以畫面上的文字訊息送出。");
  }
  warnings.push("自動遮罩可能漏掉姓名、地址、單位及事件細節；請檢查下方全部文字，必要時以角色或概略地點替代。自由文字仍有再識別風險。");
  const draft: PrivacyDraft = { request: freeze(request), fields: freeze(fields), findings: freeze(findings), warnings: freeze(warnings) };
  drafts.set(draft, { body: JSON.stringify(request), hiddenTerms: [...hiddenTerms] });
  return draft;
}

export function createPrivacyDraft(request: ChatRequest, hiddenTerms: string[] = []): PrivacyDraft {
  return buildDraft(request, [...new Set(hiddenTerms.map(term => term.trim()).filter(Boolean))]);
}
export function updatePrivacyField(draft: PrivacyDraft, path: string[], text: string): PrivacyDraft {
  const saved = drafts.get(draft);
  if (!saved || JSON.stringify(draft.request) !== saved.body) throw invalid();
  const field = draft.fields.find(item => JSON.stringify(item.path) === JSON.stringify(path));
  if (!field || field.readOnly) throw invalid();
  const request = JSON.parse(saved.body) as ChatRequest;
  let target: Record<string, unknown> = request as unknown as Record<string, unknown>;
  for (const part of path.slice(0, -1)) target = target[part] as Record<string, unknown>;
  target[path[path.length - 1]] = text;
  return buildDraft(request, saved.hiddenTerms);
}
export function removePrivacyClarification(draft: PrivacyDraft): PrivacyDraft {
  const saved = drafts.get(draft);
  if (!saved || JSON.stringify(draft.request) !== saved.body) throw invalid();
  const request = JSON.parse(saved.body) as ChatRequest;
  delete request.clarification_answer;
  return buildDraft(request, saved.hiddenTerms);
}
/** Freeze a prepared snapshot after manual review or the admin-enabled automatic path. */
export function approvePrivacyDraft(draft: PrivacyDraft): ChatRequest {
  const saved = drafts.get(draft);
  if (!saved || JSON.stringify(draft.request) !== saved.body) throw invalid();
  if (!draft.request.message.trim() || draft.request.message.length > 2000) throw new ClientPrivacyError("invalid_message", "請確認本次訊息不為空白，且不超過 2,000 字元。");
  if (draft.request.case_context && !isCaseContext(draft.request.case_context)) throw new ClientPrivacyError("invalid_context", "摘要最多 4,000 字元；舊摘要的每項內容須為 1–300 字元。請修正後再確認，原本資料仍保留。");
  if (draft.request.history.length > 200 || draft.request.history.some(item => !item.content.trim() || item.content.length > (item.role === "user" ? 2000 : 6000))
    || draft.request.history.reduce((total, item) => total + item.content.length, 0) > 120000) {
    throw new ClientPrivacyError("invalid_history", "歷史文字超過長度限制或含空白欄位。要略去內容可填入「[內容已略去]」，再確認送出。");
  }
  const request = freeze(JSON.parse(saved.body) as ChatRequest);
  approved.set(request, saved.body);
  return request;
}
/** Both chat transports require the very object approved above, including retries and admin comparisons. */
export function reviewedRequestBody(request: ChatRequest): string {
  const body = approved.get(request);
  if (!body || JSON.stringify(request) !== body) throw new ClientPrivacyError("review_required", "請先完成瀏覽器中的隱私檢查，再確認送出。");
  return body;
}
