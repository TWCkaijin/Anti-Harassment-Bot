import type { EntityType } from "./config";
import type { EntitySpan } from "./types";

const han = "一-龯";
const surnames = "王李張劉陳楊黃趙吳周徐孫馬朱胡林郭何高羅鄭梁謝宋唐許韓鄧曹彭曾蕭肖田董潘袁葉程蘇魏呂丁任沈姚盧姜崔鐘鍾譚陸汪范金石廖賴侯戴江白余游顧孟秦尹薛段麥簡柳龔杜溫方柯洪蔡施詹龐鄒錢歐池邱丘涂卓湯傅鄴顏藍俞康黎翁紀萬連巫殷于";
const name = `(?:歐陽|司馬|[${surnames}])[${han}]{1,2}`;
const orgEnd = "股份有限公司|有限責任公司|有限公司|高級中學|工作室|基金會|事務所|顧問公司|補習班|中學|高中|國中|國小|大學|診所|醫院|協會|公司|企業|集團|中心|科技";
const city = "(?:[臺台]北|新北|桃園|[臺台]中|[臺台]南|高雄|基隆|新竹|嘉義|苗栗|彰化|南投|雲林|屏東|宜蘭|花蓮|[臺台]東|澎湖|金門|連江)[市縣]";
const number = "[0-9零〇一二三四五六七八九十百]+";
const house = String.raw`(?:${number}(?:段|巷|弄)\s*){0,3}${number}(?:[-之]${number})?\s*號(?:之${number})?(?:\s*地下)?(?:\s*${number}(?:樓|室)(?:之${number})?){0,2}`;
const road = String.raw`[${han}0-9]{1,8}?(?:大道|路|街|道)${house}`;

const patterns: { type: EntityType; regex: RegExp; group: 0 | 1 }[] = [
  { type: "contact", regex: /(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9-])/gid, group: 0 },
  { type: "contact", regex: /https?:\/\/[A-Z0-9][\x21-\x7e]*/gid, group: 0 },
  { type: "contact", regex: /(?<![A-Z0-9])(?:09\d{2}|\+886[- ]?9\d{2})[- .]?\d{3}[- .]?\d{3}(?![A-Z0-9])/gid, group: 0 },
  { type: "contact", regex: /(?<![A-Z0-9])(?:\(0[2-8]\d?\)[ \t]*|0[2-8]\d?[- ])\d{3,4}[- ]?\d{4}(?![A-Z0-9])/gid, group: 0 },
  { type: "contact", regex: /(?:(?:LINE|IG|Instagram|Telegram)(?:\s*(?:ID|帳號))?|聯絡帳號)\s*(?:為|是|[:：=])?\s*(@[A-Z0-9_][A-Z0-9_.-]{2,30})/gid, group: 1 },
  { type: "identifier", regex: /(?<![A-Z0-9])(?:[A-Z][1289][ -]?\d{4}[ -]?\d{4}|[A-Z][A-D][ -]?\d{8})(?![A-Z0-9])/gid, group: 0 },
  { type: "identifier", regex: /(?:學號|工號|員工編號|身分證(?:字號)?|證號|帳號|案號|(?:會員|病歷|客戶|識別)?編號|代碼|登入名稱|Employee\s+ID)\s*(?:為|是|[:：=])?\s*([A-Za-z0-9][A-Za-z0-9_-]{2,39})(?![A-Za-z0-9_-])/gid, group: 1 },
  { type: "identifier", regex: /(?<![A-Z0-9-])[A-Z]{2,8}-(?:[A-Z]{1,8}|\d{1,3})-\d{4,}(?![A-Z0-9-])/gid, group: 0 },
  { type: "address", regex: new RegExp(`${city}(?:[${han}]{1,4}?(?:區|市|鎮|鄉))?${road}`, "gid"), group: 0 },
  { type: "address", regex: new RegExp(String.raw`(?:地址|住址|住在|寄到|送到|寄件處)\s*(?:為|是|改成|[:：=])?\s*(${road})`, "gid"), group: 1 },
  { type: "person", regex: new RegExp(String.raw`(?:我叫|我是|名叫|名字[是叫]|姓名\s*(?:是|為|[:：=])|聯絡人\s*[:：=]?|收件人\s*[:：=]?|當事人\s*[:：=])\s*(${name})(?=[\s，。！？、；／」\],;.]|$)`, "gid"), group: 1 },
  { type: "person", regex: new RegExp(String.raw`(?:主管|同事|老師|學生|經理|同學|對方)\s*(?:名叫|叫|[:：=])?\s*(${name})(?=(?:說|在|的|任職|跟|和|經理|老師|[\s，。！？、；／」：:])|$)`, "gid"), group: 1 },
  { type: "person", regex: new RegExp(String.raw`(?:^|[\n，。；！？」：、])\s*(${name})(?=(?:說|在|任職|傳訊|寄信|是我|提到|跟我|和我|主任|經理|老師))`, "gid"), group: 1 },
  { type: "person", regex: new RegExp(String.raw`(?:^|[\n，。；！？「：、／])\s*(${name})(?=[，。；！」、／])`, "gid"), group: 1 },
  { type: "organization", regex: new RegExp(String.raw`(?:^|[\s／，。；：:=「、]|(?<=任職)|(?<=就讀)|(?<=工作於)|(?<=寫到)|(?<=供職於))(?:(?:我在|他在|她在|並在|在|我是|對方是|單位為|單位是|到了|來自|寄給|寄到|轉寄給|只寫到|提到|原本在)\s*)?([${han}A-Z0-9]{1,13}?(?:${orgEnd}))(?=\s*(?:工作|上班|就讀|的|擔任|協助|[\n／，。；、！？：:」]|$))`, "gid"), group: 1 },
];

function contains(outer: EntitySpan, inner: EntitySpan): boolean {
  return outer.start <= inner.start && outer.end >= inner.end;
}

export function compactSpans(spans: EntitySpan[]): EntitySpan[] {
  const unique = [...new Map(spans.map(span => [`${span.start}:${span.end}:${span.type}`, span])).values()];
  return unique.filter(span => !unique.some(other => other !== span && other.type === span.type && contains(other, span)));
}

export function detectRuleSpans(source: string): EntitySpan[] {
  // Fold full-width punctuation/digits without losing original UTF-16 offsets.
  const offsets = [0];
  let original = 0, folded = "";
  for (const char of source) {
    const normalized = char.normalize("NFKC");
    const piece = [...normalized].length === 1 ? normalized : char;
    original += char.length;
    folded += piece;
    offsets[folded.length] = original;
  }
  const found: EntitySpan[] = [];
  for (const { type, regex, group } of patterns) {
    for (const match of folded.matchAll(regex)) {
      const bounds = match.indices?.[group];
      if (!bounds) continue;
      const [start, end] = bounds;
      const value = folded.slice(start, end);
      if (type === "identifier" && (!/\d/.test(value) || /^(?:19|20)\d{2}-\d{2}-\d{2}$/.test(value))) continue;
      const stop = type === "contact" && /^https?:\/\//i.test(value) ? start + value.replace(/[.,;:!?)\]}]+$/g, "").length : end;
      found.push({ start: offsets[start], end: offsets[stop], type });
    }
  }
  return compactSpans(found);
}

export function prioritizeRules(rules: EntitySpan[], candidates: EntitySpan[]): EntitySpan[] {
  const covered = new Set<number>();
  for (const span of rules) {
    for (let index = span.start; index < span.end; index++) covered.add(index);
  }
  const additions = candidates.filter(span => {
    for (let index = span.start; index < span.end; index++) {
      if (!covered.has(index)) return true;
    }
    return false;
  });
  return compactSpans([...rules, ...additions]);
}
