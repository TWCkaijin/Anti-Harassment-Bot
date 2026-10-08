/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import { describe, expect, it } from "vitest";
import { fixtureWorkbook } from "../test/scenarioFixture";
import { columnName, exportScenarioPredictions, parseScenarioWorkbook, scenarioColumns, selectScenarioRows } from "./scenarioWorkbook";

describe("local scenario workbooks", () => {
  it("selects an arbitrary column and skips blanks without losing Excel row numbers", () => {
    const workbook = parseScenarioWorkbook(fixtureWorkbook(), "any.xlsx");
    expect(workbook.sheets.map(sheet => sheet.name)).toEqual(["情境集", "其他"]);
    expect(scenarioColumns(workbook.sheets[0], 1)).toEqual([{ index: 0, label: "A · 法規標籤" }, { index: 2, label: "C · 情境" }]);
    expect(selectScenarioRows(workbook.sheets[0], 2, 1).map(row => row.row)).toEqual([2, 5]);
    expect(selectScenarioRows(workbook.sheets[1], 0, 1)).toEqual([{ row: 2, text: "另一工作表情境" }]);
    expect(columnName(26)).toBe("AA");
  });
  it("appends results with row alignment and preserves every other ZIP member and original cell", () => {
    const workbook = parseScenarioWorkbook(fixtureWorkbook(), "any.xlsx");
    const bytes = exportScenarioPredictions(workbook, workbook.sheets[0], 1, [
      { row: 2, prediction: "性工法、性騷法", status: "已預測", elapsedSeconds: 1.25 },
      { row: 5, prediction: "", status: "資訊不足", elapsedSeconds: 2 },
    ]);
    const exported = parseScenarioWorkbook(bytes, "output.xlsx");
    for (const [name, entry] of Object.entries(workbook.entries)) {
      if (name !== workbook.sheets[0].path) expect(exported.entries[name]).toEqual(entry);
    }
    for (const [row, cells] of workbook.sheets[0].rows) for (const [column, value] of cells) expect(exported.sheets[0].rows.get(row)?.get(column)).toBe(value);
    expect(exported.sheets[0].rows.get(1)?.get(3)).toBe("預測的適用法條");
    expect(exported.sheets[0].rows.get(2)?.get(3)).toBe("性工法、性騷法");
    expect(exported.sheets[0].rows.get(5)?.get(3)).toBe("");
    expect(exported.sheets[0].rows.get(5)?.get(4)).toBe("資訊不足");
    expect(exported.sheets[0].rows.get(3)?.has(3)).toBe(false);
  });
  it("reads shared/rich strings, custom header rows and preserves formulas", () => {
    const entries = unzipSync(fixtureWorkbook());
    entries["xl/sharedStrings.xml"] = strToU8('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><r><t>合成</t></r><r><t>情境</t></r></si></sst>');
    entries["xl/worksheets/sheet1.xml"] = strToU8('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="3"><c r="B3" t="inlineStr"><is><t>場景</t></is></c></row><row r="7"><c r="A7"><f>1+1</f><v>2</v></c><c r="B7" t="s"><v>0</v></c></row></sheetData></worksheet>');
    const workbook = parseScenarioWorkbook(zipSync(entries), "shared.xlsx");
    expect(selectScenarioRows(workbook.sheets[0], 1, 3)).toEqual([{ row: 7, text: "合成情境" }]);
    const result = unzipSync(exportScenarioPredictions(workbook, workbook.sheets[0], 3, []));
    expect(strFromU8(result["xl/worksheets/sheet1.xml"])).toContain("<f>1+1</f>");
  });
  it.each([["chatgpt_scene.xlsx", "Sheet1", 335], ["gemini_scene.xlsx", "工作表1", 299]] as const)("reads the actual %s source without modification", (file, name, count) => {
    const bytes = readFileSync(resolve("..", "resource", file));
    const workbook = parseScenarioWorkbook(bytes, file);
    const sheet = workbook.sheets.find(item => item.name === name)!;
    expect(selectScenarioRows(sheet, 1, 1)).toHaveLength(count);
    const output = parseScenarioWorkbook(exportScenarioPredictions(workbook, sheet, 1, [{ row: 2, prediction: "性工法", status: "合成測試", elapsedSeconds: 0 }]), "result.xlsx");
    const copy = output.sheets.find(item => item.name === name)!;
    for (const [row, cells] of sheet.rows) for (const [column, value] of cells) expect(copy.rows.get(row)?.get(column)).toBe(value);
    for (const [part, data] of Object.entries(workbook.entries)) if (part !== sheet.path) expect(output.entries[part]).toEqual(data);
  }, 20000);
  it("rejects invalid archives, unsupported formats, unsafe XML and oversized workbooks", () => {
    expect(() => parseScenarioWorkbook(new Uint8Array([1, 2]), "bad.xlsx")).toThrow("無法讀取");
    expect(() => parseScenarioWorkbook(fixtureWorkbook(), "bad.xls")).toThrow(".xlsx");
    expect(() => parseScenarioWorkbook(new Uint8Array(10 * 1024 * 1024 + 1), "large.xlsx")).toThrow("10 MB");
    const entries = unzipSync(fixtureWorkbook());
    entries["xl/workbook.xml"] = strToU8('<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><x/>');
    expect(() => parseScenarioWorkbook(zipSync(entries), "bad.xlsx")).toThrow("外部實體");
  });
  it("rejects protected sheets before exporting", () => {
    const bytes = fixtureWorkbook('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/><sheetProtection sheet="1"/></worksheet>');
    const workbook = parseScenarioWorkbook(bytes, "protected.xlsx");
    expect(() => exportScenarioPredictions(workbook, workbook.sheets[0], 1, [])).toThrow("受保護");
  });
  it("sizes result columns while preserving broad existing column formats outside the result range", () => {
    const entries = unzipSync(fixtureWorkbook());
    entries["xl/worksheets/sheet1.xml"] = strToU8(strFromU8(entries["xl/worksheets/sheet1.xml"]).replace("<sheetData>", '<cols><col min="1" max="100" width="9" customWidth="1" style="0"/></cols><sheetData>'));
    const workbook = parseScenarioWorkbook(zipSync(entries), "columns.xlsx");
    const output = unzipSync(exportScenarioPredictions(workbook, workbook.sheets[0], 1, []));
    const document = new DOMParser().parseFromString(strFromU8(output["xl/worksheets/sheet1.xml"]), "application/xml");
    const columns = Array.from(document.getElementsByTagNameNS("*", "col"));
    expect(columns.map(col => [col.getAttribute("min"), col.getAttribute("max"), col.getAttribute("width")])).toEqual([
      ["1", "3", "9"], ["4", "4", "28"], ["5", "5", "48"], ["6", "6", "14"], ["7", "100", "9"],
    ]);
    expect(columns.every(col => col.getAttribute("style") === "0")).toBe(true);
  });
});

it("exports scoring plus a collision-safe summary sheet without overwriting original data", () => {
  const workbook = parseScenarioWorkbook(fixtureWorkbook(), "source.xlsx");
  const result = [{ row: 2, prediction: "性平法", status: "已預測", elapsedSeconds: 1, evaluation: "正確", matchedLaws: "性平法" }];
  const bytes = exportScenarioPredictions(workbook, workbook.sheets[0], 1, result, [["總正確率", "50.00%", 1, 2], ["設定", "=not-a-formula"]]);
  const output = parseScenarioWorkbook(bytes, "result.xlsx");
  expect(output.sheets.map(sheet => sheet.name)).toEqual(["情境集", "其他", "預測統計"]);
  expect(output.sheets[0].rows.get(2)?.get(6)).toBe("正確");
  expect(output.sheets[0].rows.get(2)?.get(7)).toBe("性平法");
  expect(output.sheets[2].rows.get(1)?.get(2)).toBe("1");
  expect(output.sheets[2].rows.get(2)?.get(1)).toBe("=not-a-formula");
  expect(output.entries["xl/worksheets/sheet2.xml"]).toEqual(workbook.entries["xl/worksheets/sheet2.xml"]);
  for (const [r, cells] of workbook.sheets[0].rows) for (const [c, value] of cells) expect(output.sheets[0].rows.get(r)?.get(c)).toBe(value);
  const second = parseScenarioWorkbook(exportScenarioPredictions(output, output.sheets[0], 1, [], []), "second.xlsx");
  expect(second.sheets.at(-1)?.name).toBe("預測統計（2）");
});


it("exports a model reason as literal text and includes its column in the sheet bounds", () => {
  const workbook = parseScenarioWorkbook(fixtureWorkbook(), "source.xlsx");
  const bytes = exportScenarioPredictions(workbook, workbook.sheets[0], 1, [{ row: 2, prediction: "", status: "資訊不足", elapsedSeconds: 1, reason: "=literal <unknown> & relation" }], []);
  const copy = parseScenarioWorkbook(bytes, "result.xlsx");
  expect(copy.sheets[0].rows.get(2)?.get(8)).toBe("=literal <unknown> & relation");
  const document = new DOMParser().parseFromString(strFromU8(copy.entries[workbook.sheets[0].path]), "application/xml");
  expect(document.querySelector('c[r="I2"]')?.getAttribute("t")).toBe("inlineStr");
  expect(document.querySelector('c[r="I2"] f')).toBeNull();
  expect(document.querySelector("dimension")?.getAttribute("ref")).toMatch(/:I/);
});
