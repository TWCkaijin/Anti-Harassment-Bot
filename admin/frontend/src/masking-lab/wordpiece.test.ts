import { describe, expect, it } from "vitest";
import { TinyTokenizer } from "./wordpiece";

const VOCAB = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "王", "小", "明", "call", "##ing", "me", ",", "a"].join("\n");
const ids = (tokenizer: TinyTokenizer, text: string) => tokenizer.encode(text)[0].map(token => token.id);

describe("TinyTokenizer", () => {
  const tokenizer = new TinyTokenizer(VOCAB);

  it("rejects a vocabulary without the BERT special tokens", () => {
    expect(() => new TinyTokenizer("a\nb")).toThrow("Invalid BERT vocabulary");
  });

  it("splits CJK characters and punctuation, and uses greedy WordPiece for words", () => {
    expect(ids(tokenizer, "王小明 calling me,")).toEqual([2, 4, 5, 6, 7, 8, 9, 10, 3]);
  });

  it("keeps UTF-16 offsets of the original text, including astral characters", () => {
    const [window] = tokenizer.encode("𠀀王 me");
    expect(window.slice(1, -1)).toEqual([
      { id: 1, start: 0, end: 2 },
      { id: 4, start: 2, end: 3 },
      { id: 9, start: 4, end: 6 },
    ]);
  });

  it("maps unknown words and over-long words to a single [UNK] spanning the word", () => {
    expect(tokenizer.encode("zzz")[0].slice(1, -1)).toEqual([{ id: 1, start: 0, end: 3 }]);
    expect(tokenizer.encode("a".repeat(101))[0].slice(1, -1)).toEqual([{ id: 1, start: 0, end: 101 }]);
  });

  it("drops control and replacement characters without shifting later offsets", () => {
    expect(tokenizer.encode("\u0000�王")[0].slice(1, -1)).toEqual([{ id: 4, start: 2, end: 3 }]);
  });

  it("windows long input with overlap, each framed by [CLS] and [SEP]", () => {
    const windows = tokenizer.encode("王".repeat(300));
    expect(windows).toHaveLength(2);
    expect(windows[0]).toHaveLength(256);
    expect(windows[0][0].id).toBe(2);
    expect(windows[0].at(-1)!.id).toBe(3);
    // The second window starts 222 tokens in (254 - 32 overlap) and holds the rest.
    expect(windows[1].slice(1, -1)[0].start).toBe(222);
    expect(windows[1]).toHaveLength(300 - 222 + 2);
  });
});
