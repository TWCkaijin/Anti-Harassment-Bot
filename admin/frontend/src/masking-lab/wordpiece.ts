export interface Token { id: number; start: number; end: number }
interface Character { text: string; start: number; end: number }
const punctuation = /[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e\p{P}]/u;
const whitespace = /[\t\n\r\p{Zs}]/u;
const control = /\p{C}/u;
const WINDOW = 254;
const OVERLAP = 32;

function isChinese(value: number) {
  // The same ranges as the pinned model's BertNormalizer.
  return (value >= 0x4e00 && value <= 0x9fff) || (value >= 0x3400 && value <= 0x4dbf)
    || (value >= 0x20000 && value <= 0x2a6df) || (value >= 0x2a700 && value <= 0x2b73f)
    || (value >= 0x2b740 && value <= 0x2b81f) || (value >= 0x2b820 && value <= 0x2ceaf)
    || (value >= 0xf900 && value <= 0xfaff) || (value >= 0x2f800 && value <= 0x2fa1f);
}

/** The pinned cased BERT vocabulary uses greedy WordPiece with original UTF-16 offsets. */
export class TinyTokenizer {
  private readonly vocabulary: Map<string, number>;
  private readonly cls: number;
  private readonly sep: number;
  private readonly unk: number;

  constructor(vocab: string) {
    this.vocabulary = new Map(vocab.trimEnd().split(/\r?\n/).map((piece, id) => [piece, id]));
    const cls = this.vocabulary.get("[CLS]"), sep = this.vocabulary.get("[SEP]"), unk = this.vocabulary.get("[UNK]");
    if (cls === undefined || sep === undefined || unk === undefined) throw new Error("Invalid BERT vocabulary");
    this.cls = cls; this.sep = sep; this.unk = unk;
  }

  encode(source: string): Token[][] {
    const tokens: Token[] = [];
    let word: Character[] = [];
    const append = (chars: Character[]) => {
      if (chars.length === 0) return;
      const unknown = { id: this.unk, start: chars[0].start, end: chars[chars.length - 1].end };
      if (chars.length > 100) { tokens.push(unknown); return; }
      const pieces: Token[] = [];
      let start = 0;
      while (start < chars.length) {
        let match: Token | undefined;
        let end = chars.length;
        for (; end > start; end--) {
          const piece = (start === 0 ? "" : "##") + chars.slice(start, end).map(char => char.text).join("");
          const id = this.vocabulary.get(piece);
          if (id !== undefined) { match = { id, start: chars[start].start, end: chars[end - 1].end }; break; }
        }
        if (!match) { tokens.push(unknown); return; }
        pieces.push(match);
        start = end;
      }
      tokens.push(...pieces);
    };
    const flush = () => { append(word); word = []; };

    let offset = 0;
    for (const text of source) {
      const char = { text, start: offset, end: offset + text.length };
      offset = char.end;
      if (whitespace.test(text)) { flush(); continue; }
      if (text === "\uFFFD" || control.test(text)) continue;
      if (isChinese(text.codePointAt(0)!) || punctuation.test(text)) { flush(); append([char]); }
      else word.push(char);
    }
    flush();

    const windows: Token[][] = [];
    for (let start = 0; start < tokens.length; start += WINDOW - OVERLAP) {
      const end = start + WINDOW;
      windows.push([{ id: this.cls, start: 0, end: 0 }, ...tokens.slice(start, end), { id: this.sep, start: 0, end: 0 }]);
      if (end >= tokens.length) break;
    }
    return windows;
  }
}
