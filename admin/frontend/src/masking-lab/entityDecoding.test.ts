import { describe, expect, it } from "vitest";
import { decodeEntitySpans, LABEL_COUNT } from "./entityDecoding";
import type { Token } from "./wordpiece";

// Label ids: person B/I/E/S = 14/32/50/68, organization B/I/E/S = 12/30/48/66, 0 = outside.
const P = { B: 14, I: 32, E: 50, S: 68 }, O = { B: 12, E: 48 };
const CLS: Token = { id: 2, start: 0, end: 0 }, SEP: Token = { id: 3, start: 0, end: 0 };
const char = (start: number): Token => ({ id: 10, start, end: start + 1 });

function logitsFor(labels: number[]): Float32Array {
  const data = new Float32Array(labels.length * LABEL_COUNT);
  labels.forEach((label, index) => { data[index * LABEL_COUNT + label] = 1; });
  return data;
}

describe("decodeEntitySpans", () => {
  it("joins B-I-E person tags into one span and keeps single-token organizations", () => {
    const tokens = [CLS, char(0), char(1), char(2), char(3), SEP];
    expect(decodeEntitySpans(tokens, logitsFor([0, P.B, P.I, P.E, 66, 0]))).toEqual([
      { start: 0, end: 3, type: "person" },
      { start: 3, end: 4, type: "organization" },
    ]);
  });

  it("splits on a new begin tag, a type change or a gap in offsets", () => {
    const tokens = [char(0), char(1), char(2), { id: 10, start: 4, end: 5 }];
    expect(decodeEntitySpans(tokens, logitsFor([P.I, P.B, O.B, O.E]))).toEqual([
      { start: 0, end: 1, type: "person" },
      { start: 1, end: 2, type: "person" },
      { start: 2, end: 3, type: "organization" },
      { start: 4, end: 5, type: "organization" },
    ]);
  });

  it("never lets special tokens carry an entity", () => {
    expect(decodeEntitySpans([CLS, SEP], logitsFor([P.S, P.S]))).toEqual([]);
  });

  it("rejects output whose size does not match the window", () => {
    expect(() => decodeEntitySpans([char(0)], new Float32Array(LABEL_COUNT - 1))).toThrow();
  });
});
