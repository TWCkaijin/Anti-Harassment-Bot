/** API error type and the server error envelope parser. */

// ── API 錯誤類別 ─────────────────────────────────────────────────────────

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;
  readonly retryable: boolean;
  readonly debugMessage?: string;
  readonly code?: string;

  constructor(
    status: number,
    message: string,
    detail?: string,
    retryable = false,
    debugMessage?: string,
    code?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryable = retryable;
    this.debugMessage = debugMessage;
    this.code = code;
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeValidationIssue(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (!isRecord(value)) return undefined;

  const legacyLocation = Array.isArray(value.loc)
    ? value.loc.filter((part): part is string | number =>
        typeof part === "string" || typeof part === "number"
      ).join(".")
    : "";
  const location = typeof value.field === "string" ? value.field : legacyLocation;
  const message = typeof value.message === "string"
    ? value.message
    : typeof value.msg === "string"
      ? value.msg
      : "";
  const combined = [location, message].filter(Boolean).join(": ");
  return combined || undefined;
}

export function normalizeApiErrorDetail(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    const issues = value.map(normalizeValidationIssue).filter((issue): issue is string => Boolean(issue));
    return issues.length > 0 ? issues.join("; ") : undefined;
  }
  if (!isRecord(value)) return undefined;

  const issue = normalizeValidationIssue(value);
  if (issue) return issue;

  try {
    return JSON.stringify(value);
  } catch {
    return undefined;
  }
}

export async function throwResponseError(response: Response): Promise<never> {
  let detail: string | undefined;
  let retryable = false;
  let debugMessage: string | undefined;
  let code: string | undefined;
  try {
    const errorData: unknown = await response.json();
    if (isRecord(errorData)) {
      const summary = normalizeApiErrorDetail(errorData.detail);
      const validationErrors = normalizeApiErrorDetail(errorData.errors);
      detail = validationErrors
        ? [summary, validationErrors].filter(Boolean).join(": ")
        : summary;
      retryable = errorData.retryable === true;
      debugMessage = typeof errorData.debug_message === "string"
        ? errorData.debug_message
        : undefined;
      code = typeof errorData.code === "string" ? errorData.code : undefined;
    }
  } catch {
    // 非 JSON 回應
  }
  throw new ApiError(
    response.status,
    `API 請求失敗 (${response.status})`,
    detail,
    retryable,
    debugMessage,
    code,
  );
}

export function invalidStream(): ApiError {
  return new ApiError(502, "回覆串流中斷", "回覆未完整接收，請重新送出訊息", true);
}
