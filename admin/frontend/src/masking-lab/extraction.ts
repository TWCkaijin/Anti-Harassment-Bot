import type { EntitySpan } from "./types";

export class MaskingError extends Error {
  readonly code: string;
  constructor(code: string) { super(code); this.name = "MaskingError"; this.code = code; }
}

const LABELS = { person: "人物", organization: "單位", address: "地址", contact: "聯絡資訊", identifier: "識別號" };

/** Lab preview only. One left-to-right pass prevents cascading replacements. */
export function previewMask(text: string, spans: EntitySpan[]): string {
  const merged: EntitySpan[] = [];
  for (const span of [...spans].sort((a, b) => a.start - b.start || b.end - a.end)) {
    const last = merged.at(-1);
    if (last && span.start < last.end) last.end = Math.max(last.end, span.end);
    else merged.push({ ...span });
  }
  const counts = new Map<string, number>();
  const aliases = new Map<string, string>();
  let offset = 0;
  let result = "";
  for (const span of merged) {
    const key = JSON.stringify([span.type, text.slice(span.start, span.end)]);
    let alias = aliases.get(key);
    if (!alias) {
      const next = (counts.get(span.type) ?? 0) + 1;
      counts.set(span.type, next);
      alias = `[${LABELS[span.type]}${next}]`;
      aliases.set(key, alias);
    }
    result += text.slice(offset, span.start) + alias;
    offset = span.end;
  }
  return result + text.slice(offset);
}
