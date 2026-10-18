import type { ChatCompletionMessageParam } from "@mlc-ai/web-llm";
import { CLASSIFICATION_SCHEMA, CLASSIFY_PROMPT, ENTITY_TYPES, LOCATE_PROMPT, SPAN_SCHEMA, type Entity } from "./config";
import { MaskingError } from "./extraction";

export interface ExtractionRound {
  stage: "locate" | "classify";
  chunk: number;
  candidate?: string;
  raw: string;
}
export type Generate = (messages: ChatCompletionMessageParam[], schema: string, maxTokens: number) => Promise<string>;

function parseObject(raw: string): Record<string, unknown> {
  const prefix = "<think>\n\n</think>\n\n";
  let value: unknown;
  try { value = JSON.parse(raw.startsWith(prefix) ? raw.slice(prefix.length) : raw); }
  catch { throw new MaskingError("invalid_json"); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new MaskingError("invalid_stage_output");
  return value as Record<string, unknown>;
}

export function parseSpans(raw: string, source: string): string[] {
  const value = parseObject(raw);
  if (Object.keys(value).join() !== "spans" || !Array.isArray(value.spans) || value.spans.length > 100) throw new MaskingError("invalid_spans");
  if (value.spans.some(span => typeof span !== "string" || !span.trim() || span.length > 500 || !source.includes(span))) throw new MaskingError("invalid_entity_span");
  return [...new Set(value.spans as string[])];
}

export function parseClassification(raw: string): Entity["type"] | "none" {
  const value = parseObject(raw);
  if (Object.keys(value).join() !== "type" || ![...ENTITY_TYPES, "none"].includes(value.type as Entity["type"])) throw new MaskingError("invalid_classification");
  return value.type as Entity["type"] | "none";
}

export async function extractInStages(source: string, generate: Generate, chunk = 0): Promise<{ entities: Entity[]; rounds: ExtractionRound[] }> {
  const rounds: ExtractionRound[] = [];
  let stage: ExtractionRound["stage"] = "locate", currentCandidate: string | undefined;
  try {
    const raw = await generate([
      { role: "system", content: LOCATE_PROMPT },
      { role: "user", content: JSON.stringify({ text: source }) },
    ], SPAN_SCHEMA, 512);
    rounds.push({ stage: "locate", chunk, raw });
    const candidates = parseSpans(raw, source);
    const entities: Entity[] = [];
    for (const candidate of candidates) {
      stage = "classify"; currentCandidate = candidate;
      const classification = await generate([
        { role: "system", content: CLASSIFY_PROMPT },
        { role: "user", content: JSON.stringify({ text: source, candidate }) },
      ], CLASSIFICATION_SCHEMA, 32);
      rounds.push({ stage: "classify", chunk, candidate, raw: classification });
      const type = parseClassification(classification);
      if (type !== "none") entities.push({ text: candidate, type });
    }
    return { entities, rounds };
  } catch (failure) {
    const error = failure instanceof MaskingError ? failure : new MaskingError("model_execution_failed");
    if (error.generatedOutput !== undefined) rounds.push({ stage, chunk, candidate: currentCandidate, raw: error.generatedOutput });
    error.rounds = rounds;
    throw error;
  }
}
