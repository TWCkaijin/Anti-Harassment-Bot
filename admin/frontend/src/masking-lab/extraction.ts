import { CHUNK_CHARACTERS, CHUNK_OVERLAP, ENTITY_TYPES, type Entity } from "./config";
import type { ExtractionRound } from "./pipeline";

export class MaskingError extends Error {
  readonly code: string;
  rounds: ExtractionRound[] = [];
  generatedOutput?: string;
  constructor(code: string) { super(code); this.name = "MaskingError"; this.code = code; }
}

/** A malformed or hallucinated extraction is a failed check, never an empty result. */
export function parseEntities(raw: string, source: string): Entity[] {
  let parsed: unknown;
  // WebLLM 0.2.85 includes its injected empty Qwen3 thinking header in content.
  // Strip that exact protocol prefix only; never search for a JSON substring.
  const prefix = "<think>\n\n</think>\n\n";
  const content = raw.startsWith(prefix) ? raw.slice(prefix.length) : raw;
  try { parsed = JSON.parse(content); } catch { throw new MaskingError("invalid_json"); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)
    || Object.keys(parsed).length !== 1 || !("entities" in parsed) || !Array.isArray(parsed.entities)
    || parsed.entities.length > 100) throw new MaskingError("invalid_entities");
  const result: Entity[] = [];
  for (const entity of parsed.entities) {
    if (!entity || typeof entity !== "object" || Array.isArray(entity)
      || Object.keys(entity).sort().join(",") !== "text,type"
      || typeof entity.text !== "string" || !entity.text.trim() || entity.text.length > 500
      || !ENTITY_TYPES.includes(entity.type) || !source.includes(entity.text)) throw new MaskingError("invalid_entity_span");
    if (!result.some(item => item.text === entity.text)) result.push({ text: entity.text, type: entity.type });
  }
  return result;
}

/** Sentence/newline boundaries first; only unpunctuated long runs need overlap.
 * ASCII dots inside emails, URLs and decimals are not sentence boundaries.
 * Keep every chunk an exact source substring (including its punctuation).
 */
export function splitText(text: string): string[] {
  const characters = Array.from(text);
  const result: string[] = [];
  const appendSentence = (start: number, end: number) => {
    while (end - start > CHUNK_CHARACTERS) {
      const limit = start + CHUNK_CHARACTERS;
      let boundary = limit;
      while (boundary > start && !/[，,；;]/u.test(characters[boundary - 1])) boundary -= 1;
      const hardCut = boundary === start;
      if (hardCut) boundary = limit;
      const chunk = characters.slice(start, boundary).join("");
      if (chunk.trim()) result.push(chunk);
      start = hardCut ? boundary - CHUNK_OVERLAP : boundary;
    }
    const chunk = characters.slice(start, end).join("");
    if (chunk.trim()) result.push(chunk);
  };
  let start = 0;
  for (let index = 0; index < characters.length; index += 1) {
    const char = characters[index];
    const asciiStop = char === "." && (index + 1 === characters.length || /\s/u.test(characters[index + 1]));
    if (char === "。" || char === "\n" || char === "\r" || asciiStop) {
      // Treat Windows line endings as one boundary.
      if (char === "\r" && characters[index + 1] === "\n") index += 1;
      appendSentence(start, index + 1);
      start = index + 1;
    }
  }
  appendSentence(start, characters.length);
  return result;
}

const LABELS = { person: "人物", organization: "單位", address: "地址", contact: "聯絡資訊", identifier: "識別號" };

/** Lab preview only. One left-to-right pass prevents cascading replacements. */
export function previewMask(text: string, entities: Entity[]): string {
  const counts = new Map<string, number>();
  const replacements = [...entities].sort((a, b) => b.text.length - a.text.length).map(entity => {
    const next = (counts.get(entity.type) ?? 0) + 1;
    counts.set(entity.type, next);
    return { original: entity.text, replacement: `[${LABELS[entity.type]}${next}]` };
  });
  let result = "";
  for (let offset = 0; offset < text.length;) {
    const entry = replacements.find(item => text.startsWith(item.original, offset));
    if (entry) { result += entry.replacement; offset += entry.original.length; }
    else { result += text[offset]; offset += 1; }
  }
  return result;
}
