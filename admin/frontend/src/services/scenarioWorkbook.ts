import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";

const NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
const REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
export const MAX_WORKBOOK_BYTES = 10 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 50 * 1024 * 1024;
export interface ScenarioSheet {
  name: string;
  path: string;
  rows: Map<number, Map<number, string>>;
  maxColumn: number;
  maxRow: number;
}
export interface ScenarioWorkbook { fileName: string; entries: Record<string, Uint8Array>; sheets: ScenarioSheet[] }
export interface ScenarioRow { row: number; text: string; reference?: string }
export interface PredictionCell { row: number; prediction: string; status: string; elapsedSeconds: number | null; evaluation?: string; matchedLaws?: string; reason?: string }

function xml(bytes: Uint8Array | undefined): XMLDocument {
  if (!bytes) throw new Error("Excel 缺少必要的工作表資料。");
  const source = strFromU8(bytes);
  if (/<!DOCTYPE|<!ENTITY/i.test(source)) throw new Error("不支援含外部實體的 Excel。");
  const document = new DOMParser().parseFromString(source, "application/xml");
  if (document.getElementsByTagName("parsererror").length) throw new Error("Excel XML 格式不正確。");
  return document;
}
const elements = (node: Document | Element, name: string) => Array.from(node.getElementsByTagNameNS("*", name));
const richText = (node: Element) => elements(node, "t").filter(t => t.parentElement?.localName !== "rPh").map(t => t.textContent ?? "").join("");

export function columnName(index: number): string {
  let result = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) result = String.fromCharCode(65 + (n - 1) % 26) + result;
  return result;
}
function coordinate(ref: string): [number, number] {
  const match = /^([A-Z]+)([1-9]\d*)$/.exec(ref);
  if (!match) throw new Error("Excel 儲存格座標不正確。");
  const column = [...match[1]].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0) - 1;
  const row = Number(match[2]);
  if (column >= 16384 || row > 1048576) throw new Error("Excel 儲存格超過範圍。");
  return [column, row];
}
function resolvePart(target: string): string {
  const parts: string[] = target.startsWith("/") ? [] : ["xl"];
  for (const part of target.split("/")) {
    if (part === "..") { if (!parts.length) throw new Error("Excel 工作表路徑不正確。"); parts.pop(); }
    else if (part && part !== ".") parts.push(part);
  }
  return parts.join("/");
}

/** Read locally. Keep every original ZIP member for a lossless copy of unrelated sheets/assets. */
export function parseScenarioWorkbook(bytes: Uint8Array, fileName: string): ScenarioWorkbook {
  if (!/\.xlsx$/i.test(fileName)) throw new Error("請選擇 .xlsx 檔案；舊版 .xls 請先另存為 .xlsx。");
  if (bytes.byteLength > MAX_WORKBOOK_BYTES) throw new Error("Excel 檔案需小於 10 MB。");
  let expanded = 0, count = 0;
  let entries: Record<string, Uint8Array>;
  try {
    entries = unzipSync(bytes, { filter: entry => {
      expanded += entry.originalSize;
      if (++count > 2000 || expanded > MAX_EXPANDED_BYTES) throw new Error("archive_limit");
      return true;
    } });
  } catch { throw new Error("無法讀取 Excel；請確認檔案未加密、未損壞，且解壓縮後小於 50 MB。"); }
  if (Object.values(entries).reduce((sum, entry) => sum + entry.length, 0) > MAX_EXPANDED_BYTES) throw new Error("Excel 解壓縮後過大。");
  const workbook = xml(entries["xl/workbook.xml"]);
  const rels = elements(xml(entries["xl/_rels/workbook.xml.rels"]), "Relationship");
  const sharedRel = rels.find(rel => rel.getAttribute("Type")?.endsWith("/sharedStrings") && rel.getAttribute("TargetMode") !== "External");
  const sharedPart = sharedRel ? resolvePart(sharedRel.getAttribute("Target") ?? "") : "xl/sharedStrings.xml";
  const strings = entries[sharedPart] ? elements(xml(entries[sharedPart]), "si").map(richText) : [];
  let cellsRead = 0;
  const sheets = elements(workbook, "sheet").flatMap(sheet => {
    const rel = rels.find(item => item.getAttribute("Id") === sheet.getAttributeNS(REL_NS, "id"));
    if (!rel || rel.getAttribute("TargetMode") === "External") throw new Error("Excel 工作表關聯不正確。");
    // Chart-only sheets are preserved in the output but cannot be selected as scenarios.
    if (!rel.getAttribute("Type")?.endsWith("/worksheet")) return [];
    const path = resolvePart(rel.getAttribute("Target") ?? "");
    const document = xml(entries[path]);
    const rows = new Map<number, Map<number, string>>();
    let maxColumn = -1, maxRow = 1;
    for (const cell of elements(document, "c")) {
      if (++cellsRead > 200000) throw new Error("Excel 超過 200,000 個儲存格，請先縮小檔案。");
      const [column, row] = coordinate(cell.getAttribute("r") ?? "");
      maxColumn = Math.max(maxColumn, column); maxRow = Math.max(maxRow, row);
      const type = cell.getAttribute("t");
      const raw = elements(cell, "v")[0]?.textContent ?? "";
      let value = type === "inlineStr" ? richText(cell) : raw;
      if (type === "s") {
        if (!/^\d+$/.test(raw) || strings[Number(raw)] === undefined) throw new Error("Excel 共用文字索引不正確。");
        value = strings[Number(raw)];
      }
      if (type === "e") value = "";
      if (!rows.has(row)) rows.set(row, new Map());
      if (rows.get(row)!.has(column)) throw new Error("Excel 含重複的儲存格座標。");
      rows.get(row)!.set(column, value);
    }
    // Include blank styled/merged extents so appended predictions never intersect them.
    for (const range of [...elements(document, "dimension"), ...elements(document, "mergeCell")]) {
      const ref = range.getAttribute("ref");
      if (ref) { const [column, row] = coordinate(ref.split(":").at(-1)!); maxColumn = Math.max(maxColumn, column); maxRow = Math.max(maxRow, row); }
    }
    return [{ name: sheet.getAttribute("name") ?? "工作表", path, rows, maxColumn, maxRow }];
  });
  if (!sheets.length) throw new Error("Excel 沒有可讀取的工作表。");
  return { fileName, entries, sheets };
}

export function scenarioColumns(sheet: ScenarioSheet, headerRow: number): { index: number; label: string }[] {
  const used = new Set<number>();
  for (const [row, cells] of sheet.rows) if (row >= headerRow) for (const [col, value] of cells) if (value.trim()) used.add(col);
  return [...used].sort((a, b) => a - b).map(index => ({ index, label: `${columnName(index)} · ${sheet.rows.get(headerRow)?.get(index)?.trim() || "無標題"}` }));
}
export function selectScenarioRows(sheet: ScenarioSheet, column: number, headerRow: number): ScenarioRow[] {
  if (!Number.isSafeInteger(headerRow) || headerRow < 1 || headerRow > sheet.maxRow) throw new Error("請指定有效的標題列。");
  const rows = [...sheet.rows].sort(([a], [b]) => a - b).flatMap(([row, cells]) => {
    const text = cells.get(column)?.trim();
    return row > headerRow && text ? [{ row, text }] : [];
  });
  if (rows.length > 5000) throw new Error("一次最多預測 5,000 筆情境，請先拆分檔案。");
  return rows;
}

export function exportScenarioPredictions(workbook: ScenarioWorkbook, sheet: ScenarioSheet, headerRow: number, results: PredictionCell[], summary?: (string | number | null)[][]): Uint8Array {
  const scored = summary !== undefined || results.some(row => row.evaluation !== undefined);
  const withReasons = scored || results.some(row => row.reason !== undefined);
  const resultCount = (scored ? 5 : 3) + (withReasons ? 1 : 0);
  if (sheet.maxColumn + resultCount >= 16384) throw new Error("工作表右側沒有足夠空間新增結果欄。");
  const document = xml(workbook.entries[sheet.path]);
  const data = elements(document, "sheetData")[0];
  if (!data || elements(document, "sheetProtection").length) throw new Error("工作表受保護或格式不支援，請解除保護後再試。");
  const ns = document.documentElement.namespaceURI ?? NS;
  // Give appended columns usable widths without changing existing column styles.
  const firstColumn = sheet.maxColumn + 2, lastColumn = firstColumn + resultCount - 1;
  let cols = elements(document, "cols")[0];
  if (!cols) { cols = document.createElementNS(ns, "cols"); data.parentNode!.insertBefore(cols, data); }
  const originalColumns = Array.from(cols.children);
  const resultStyles = Array.from({ length: resultCount }, (_, index) => index).map(index => originalColumns.find(col => Number(col.getAttribute("min")) <= firstColumn + index && Number(col.getAttribute("max")) >= firstColumn + index)?.getAttribute("style"));
  for (const col of originalColumns) {
    const min = Number(col.getAttribute("min")), max = Number(col.getAttribute("max"));
    if (max < firstColumn || min > lastColumn) continue;
    if (min < firstColumn) { const before = col.cloneNode(true) as Element; before.setAttribute("max", String(firstColumn - 1)); cols.append(before); }
    if (max > lastColumn) { const after = col.cloneNode(true) as Element; after.setAttribute("min", String(lastColumn + 1)); cols.append(after); }
    col.remove();
  }
  [28, 48, 14, ...(scored ? [32, 28] : []), ...(withReasons ? [65] : [])].forEach((width, index) => {
    const col = document.createElementNS(ns, "col");
    col.setAttribute("min", String(firstColumn + index)); col.setAttribute("max", String(firstColumn + index));
    col.setAttribute("width", String(width)); col.setAttribute("customWidth", "1");
    if (resultStyles[index]) col.setAttribute("style", resultStyles[index]);
    cols.append(col);
  });
  Array.from(cols.children).sort((a, b) => Number(a.getAttribute("min")) - Number(b.getAttribute("min"))).forEach(col => cols.append(col));
  const rowElements = new Map(elements(data, "row").map(row => [Number(row.getAttribute("r")), row]));
  const writeRow = (number: number, values: (string | number | null)[]) => {
    let row = rowElements.get(number);
    if (!row) {
      row = document.createElementNS(ns, "row"); row.setAttribute("r", String(number));
      const next = Array.from(data.children).find(child => Number(child.getAttribute("r")) > number);
      data.insertBefore(row, next ?? null); rowElements.set(number, row);
    }
    row.removeAttribute("spans");
    values.forEach((value, index) => {
      const cell = document.createElementNS(ns, "c");
      cell.setAttribute("r", `${columnName(sheet.maxColumn + 1 + index)}${number}`);
      if (typeof value === "number") {
        const v = document.createElementNS(ns, "v"); v.textContent = String(value); cell.append(v);
      } else {
        cell.setAttribute("t", "inlineStr");
        const inline = document.createElementNS(ns, "is"), text = document.createElementNS(ns, "t");
        text.setAttributeNS("http://www.w3.org/XML/1998/namespace", "xml:space", "preserve");
        text.textContent = value ?? ""; inline.append(text); cell.append(inline);
      }
      row!.append(cell);
    });
  };
  writeRow(headerRow, ["預測的適用法條", "預測狀態", "耗時（秒）", ...(scored ? ["比對結果", "命中法規"] : []), ...(withReasons ? ["無法預測原因"] : [])]);
  for (const result of results) writeRow(result.row, [result.prediction, result.status, result.elapsedSeconds, ...(scored ? [result.evaluation ?? "", result.matchedLaws ?? ""] : []), ...(withReasons ? [result.reason ?? ""] : [])]);
  const dimension = elements(document, "dimension")[0];
  if (dimension) dimension.setAttribute("ref", `A1:${columnName(sheet.maxColumn + resultCount)}${Math.max(sheet.maxRow, headerRow)}`);
  const entries = { ...workbook.entries, [sheet.path]: strToU8(new XMLSerializer().serializeToString(document)) };
  if (summary !== undefined) appendSummarySheet(entries, summary);
  return zipSync(entries, { level: 6 });
}

export function downloadLocalFile(bytes: Uint8Array | string, name: string, type: string): void {
  const blob = new Blob([typeof bytes === "string" ? bytes : new Uint8Array(bytes)], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Add a worksheet using fresh package relationships, preserving all original cells. */
function appendSummarySheet(entries: Record<string, Uint8Array>, rows: (string | number | null)[][]) {
  const workbook = xml(entries["xl/workbook.xml"]);
  const protection = elements(workbook, "workbookProtection")[0];
  if (protection && ["1", "true"].includes(protection.getAttribute("lockStructure") ?? "")) throw new Error("活頁簿結構受保護，請解除保護後再匯出統計。");
  const sheets = elements(workbook, "sheets")[0];
  if (!sheets) throw new Error("Excel 缺少工作表清單。");
  const names = new Set(elements(sheets, "sheet").map(sheet => sheet.getAttribute("name")));
  let name = "預測統計", suffix = 2;
  while (names.has(name)) name = `預測統計（${suffix++}）`;
  let partNumber = 1;
  while (entries[`xl/worksheets/prediction-summary-${partNumber}.xml`]) partNumber++;
  const part = `xl/worksheets/prediction-summary-${partNumber}.xml`;
  const rels = xml(entries["xl/_rels/workbook.xml.rels"]);
  const ids = new Set(elements(rels, "Relationship").map(rel => rel.getAttribute("Id")));
  let id = "rIdPredictionSummary", number = 2;
  while (ids.has(id)) id = `rIdPredictionSummary${number++}`;
  const relationship = rels.createElementNS(rels.documentElement.namespaceURI, "Relationship");
  relationship.setAttribute("Id", id); relationship.setAttribute("Type", `${REL_NS}/worksheet`); relationship.setAttribute("Target", part.slice(3));
  rels.documentElement.append(relationship);
  const sheet = workbook.createElementNS(workbook.documentElement.namespaceURI ?? NS, "sheet");
  sheet.setAttribute("name", name);
  sheet.setAttribute("sheetId", String(Math.max(0, ...elements(sheets, "sheet").map(s => Number(s.getAttribute("sheetId")))) + 1));
  sheet.setAttributeNS(REL_NS, "r:id", id); sheets.append(sheet);
  const types = xml(entries["[Content_Types].xml"]);
  const override = types.createElementNS(types.documentElement.namespaceURI, "Override");
  override.setAttribute("PartName", `/${part}`); override.setAttribute("ContentType", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml");
  types.documentElement.append(override);
  const output = new DOMParser().parseFromString(`<worksheet xmlns="${NS}"><cols><col min="1" max="1" width="36" customWidth="1"/><col min="2" max="5" width="30" customWidth="1"/></cols><sheetData/></worksheet>`, "application/xml");
  const data = elements(output, "sheetData")[0];
  rows.forEach((values, index) => {
    const row = output.createElementNS(NS, "row"); row.setAttribute("r", String(index + 1));
    values.forEach((value, column) => {
      const cell = output.createElementNS(NS, "c"); cell.setAttribute("r", `${columnName(column)}${index + 1}`);
      if (typeof value === "number") {
        const v = output.createElementNS(NS, "v"); v.textContent = String(value); cell.append(v);
      } else {
        // Literal strings prevent reference cells or config text becoming formulas.
        if ((value?.length ?? 0) > 32767) throw new Error("統計欄位超過 Excel 儲存格文字上限。");
        cell.setAttribute("t", "inlineStr");
        const inline = output.createElementNS(NS, "is"), text = output.createElementNS(NS, "t");
        text.setAttributeNS("http://www.w3.org/XML/1998/namespace", "xml:space", "preserve");
        text.textContent = value ?? ""; inline.append(text); cell.append(inline);
      }
      row.append(cell);
    });
    data.append(row);
  });
  const serialize = (doc: XMLDocument) => strToU8(new XMLSerializer().serializeToString(doc));
  entries["xl/workbook.xml"] = serialize(workbook);
  entries["xl/_rels/workbook.xml.rels"] = serialize(rels);
  entries["[Content_Types].xml"] = serialize(types);
  entries[part] = serialize(output);
}
