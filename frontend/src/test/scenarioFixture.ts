import { strToU8, zipSync } from "fflate";
import type { RuntimeConfig } from "../services/api";
import { DEFAULT_PIPELINE } from "../services/pipeline";

export const batchConfig: RuntimeConfig = {
  pipeline: { ...DEFAULT_PIPELINE }, openrouter_model: "fixture/no-network", rag_retrieval_top_k: 3,
  rag_distance_threshold: null, enable_anonymization: true, temperature: 0.2, top_p: 1,
  max_tokens: 1200, reasoning_effort: "none", agent_prompt_sections: {},
  rag_collections: { law: "law", judgment: "judgment", remedy: "remedy" },
  enable_image_upload: true, development_mode: false, source: "local",
  applicable_laws_contract_sha256: "a".repeat(64),
};
const NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
export function fixtureWorkbook(sheetXml?: string): Uint8Array {
  return zipSync({
    "[Content_Types].xml": strToU8('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'),
    "_rels/.rels": strToU8('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
    "xl/workbook.xml": strToU8(`<workbook xmlns="${NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="情境集" sheetId="1" r:id="rId1"/><sheet name="其他" sheetId="2" r:id="rId2"/></sheets></workbook>`),
    "xl/_rels/workbook.xml.rels": strToU8('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet2.xml"/></Relationships>'),
    "xl/worksheets/sheet1.xml": strToU8(sheetXml ?? `<worksheet xmlns="${NS}"><dimension ref="A1:C5"/><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>法規標籤</t></is></c><c r="C1" t="inlineStr"><is><t>情境</t></is></c></row>
      <row r="2"><c r="A2" t="inlineStr"><is><t>gold-never-send</t></is></c><c r="C2" t="inlineStr"><is><t>合成聯絡人 test@example.com</t></is></c></row>
      <row r="3"><c r="C3" t="inlineStr"><is><t>  </t></is></c></row>
      <row r="5"><c r="C5" t="inlineStr"><is><t>合成第二筆情境</t></is></c></row>
      </sheetData></worksheet>`),
    "xl/worksheets/sheet2.xml": strToU8(`<worksheet xmlns="${NS}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>情境</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>另一工作表情境</t></is></c></row></sheetData></worksheet>`),
  });
}
