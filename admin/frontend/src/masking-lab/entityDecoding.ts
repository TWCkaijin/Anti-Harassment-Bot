import { MaskingError } from "./extraction";
import type { EntitySpan } from "./types";
import type { Token } from "./wordpiece";

/** Output width of the pinned CKIP Tiny NER head. */
export const LABEL_COUNT = 73;
// B, I, E, S label ids for the two entity types the masking lab keeps.
const PERSON = [14, 32, 50, 68];
const ORGANIZATION = [12, 30, 48, 66];
const BEGIN = 0, END = 2, SINGLE = 3;

/**
 * Turn one window of per-token logits into person/organization spans: argmax per
 * token, then BIES tags joined over contiguous source offsets. Special tokens
 * ([CLS]/[SEP], empty offsets) never start or extend a span.
 */
export function decodeEntitySpans(tokens: readonly Token[], logits: ArrayLike<number>): EntitySpan[] {
  if (logits.length !== tokens.length * LABEL_COUNT) throw new MaskingError("tiny_invalid_output");
  const spans: EntitySpan[] = [];
  let current: EntitySpan | undefined;
  const flush = () => { if (current) spans.push(current); current = undefined; };
  for (const [index, token] of tokens.entries()) {
    const offset = index * LABEL_COUNT;
    let label = 0;
    for (let next = 1; next < LABEL_COUNT; next++) {
      if (logits[offset + next] > logits[offset + label]) label = next;
    }
    const person = PERSON.indexOf(label), organization = ORGANIZATION.indexOf(label);
    const kind = token.end <= token.start ? null : person >= 0 ? "person" : organization >= 0 ? "organization" : null;
    const tag = kind === "person" ? person : organization;
    if (!kind || tag === BEGIN || tag === SINGLE || (current && (current.type !== kind || current.end !== token.start))) flush();
    if (kind) {
      current = { start: current?.start ?? token.start, end: token.end, type: kind };
      if (tag === END || tag === SINGLE) flush();
    }
  }
  flush();
  return spans;
}
