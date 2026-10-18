import { strToU8, zipSync } from "fflate";
import { columnName } from "./scenarioWorkbook";
import {
  behaviorStatistics,
  currentBehaviorReview,
  sanitizeBehaviorData,
} from "./behaviorRecords";
import {
  BEHAVIOR_HEADERS,
  BEHAVIOR_STATUS,
  REVIEW_LABELS,
  type BehaviorRun,
} from "./behaviorTests";

type Cell = string | number | null;
const NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

const XML_ENTITY_MAP: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&apos;",
};

function escapeXml(value: string): string {
  return Array.from(value)
    .filter((char) => char.codePointAt(0)! >= 32 || "\t\r\n".includes(char))
    .join("")
    .replace(/[&<>"']/g, (char) => XML_ENTITY_MAP[char]!);
}

function chunks(value: Cell): Cell[] {
  if (typeof value !== "string" || value.length <= 30000) return [value];
  const result: string[] = [];
  let start = 0;
  while (start < value.length) {
    let end = Math.min(start + 30000, value.length);
    if (end < value.length && /[\uD800-\uDBFF]/.test(value[end - 1])) end--;
    result.push(value.slice(start, end));
    start = end;
  }
  return result;
}

/** A long cell continues on another physical row; identity columns are repeated. */
function splitRow(values: Cell[], identityColumns: number): Cell[][] {
  const parts = values.map(chunks);
  const count = Math.max(...parts.map((part) => part.length));
  return Array.from({ length: count }, (_, index) => [
    ...parts.map((part, column) =>
      column < identityColumns || part.length === 1 ? part[0] : (part[index] ?? null),
    ),
    `${index + 1}/${count}`,
  ]);
}

function worksheet(rows: Cell[][], widths: number[]): string {
  const last = `${columnName(Math.max(...rows.map((row) => row.length)) - 1)}${rows.length}`;
  const colsXml = widths
    .map((width, i) => `<col min="${i + 1}" max="${i + 1}" width="${width}" customWidth="1"/>`)
    .join("");
  const rowsXml = rows
    .map(
      (row, r) =>
        `<row r="${r + 1}">${row
          .map((value, c) => {
            const attributes = `r="${columnName(c)}${r + 1}" s="${r === 0 ? 1 : 2}"`;
            return typeof value === "number"
              ? `<c ${attributes}><v>${value}</v></c>`
              : `<c ${attributes} t="inlineStr"><is><t xml:space="preserve">${escapeXml(value ?? "")}</t></is></c>`;
          })
          .join("")}</row>`,
    )
    .join("");

  return [
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    `<worksheet xmlns="${NS}">`,
    `<dimension ref="A1:${last}"/>`,
    `<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>`,
    `<cols>${colsXml}</cols>`,
    `<sheetData>${rowsXml}</sheetData>`,
    `<autoFilter ref="A1:${last}"/>`,
    `</worksheet>`,
  ].join("");
}

/** A local report only. Every string is inline text, including formula-looking output. */
export function exportBehaviorWorkbook(original: BehaviorRun): Uint8Array {
  const run = sanitizeBehaviorData(original) as BehaviorRun;
  const stats = behaviorStatistics(run);
  const summary: Cell[][] = [
    [
      ...BEHAVIOR_HEADERS,
      "測次",
      "執行狀態",
      "規則提示",
      "重大失敗",
      "覆核理由",
      "覆核者",
      "覆核時間",
      "證據 SHA256",
      "原始描述",
      "來源",
      "分段",
    ],
  ];
  const turns: Cell[][] = [
    [
      "ID",
      "測次",
      "輪次",
      "嘗試",
      "狀態",
      "開始時間",
      "完整回覆耗時（秒）",
      "實際送出訊息",
      "送出歷史",
      "送出摘要",
      "回覆",
      "摘要更新",
      "接受後摘要",
      "引用來源",
      "規則證據",
      "診斷",
      "操作錯誤",
      "分段",
    ],
  ];

  for (const record of run.records) {
    const test = run.cases.find((item) => item.id === record.caseId)!;
    const review = currentBehaviorReview(record);
    summary.push(
      ...splitRow(
        [
          test.id,
          test.category,
          test.turns.map((message, i) => `第 ${i + 1} 輪：${message}`).join("\n\n"),
          test.expected,
          test.criticalFailure,
          record.turns
            .map(
              (turn) =>
                `第 ${turn.number} 輪／嘗試 ${turn.attempt}：${
                  turn.result?.response.reply ??
                  (turn.prediction ? JSON.stringify(turn.prediction) : undefined) ??
                  turn.error ??
                  "等待回覆"
                }`,
            )
            .join("\n\n"),
          review
            ? REVIEW_LABELS[review.verdict]
            : record.status === "completed"
              ? "待覆核"
              : "尚未完成",
          record.repetition,
          BEHAVIOR_STATUS[record.status],
          record.turns
            .flatMap((turn) =>
              turn.issues.map((issue) => `第 ${turn.number} 輪：${issue.text}\n證據：${issue.evidence}`),
            )
            .join("\n"),
          review ? (review.critical ? "是" : "否") : "待覆核",
          review?.reason ?? "",
          review?.reviewer ?? "",
          review?.reviewedAt ?? "",
          record.evidenceSha256,
          test.originalPrompt,
          `${test.source.file}／${test.source.sheet}／第 ${test.source.row} 列`,
        ],
        2,
      ),
    );

    for (const turn of record.turns) {
      turns.push(
        ...splitRow(
          [
            test.id,
            record.repetition,
            turn.number,
            turn.attempt,
            BEHAVIOR_STATUS[turn.status],
            turn.startedAt,
            turn.elapsedMs === null ? null : Math.round(turn.elapsedMs / 10) / 100,
            turn.request.message,
            JSON.stringify(turn.request.history),
            JSON.stringify(turn.request.case_context),
            turn.result?.response.reply ??
              (turn.prediction ? JSON.stringify(turn.prediction) : ""),
            JSON.stringify(turn.result?.response.summary_update ?? null),
            JSON.stringify(turn.contextAfter ?? null),
            JSON.stringify(turn.result?.response.rag_used.sources ?? []),
            JSON.stringify(turn.issues),
            JSON.stringify(turn.result?.diagnostics ?? turn.lawDiagnostics ?? {}),
            turn.error ?? "",
          ],
          4,
        ),
      );
    }
  }

  const settingsRows: Array<[string, Cell]> = [
    ["執行 ID", run.runId],
    ["建立時間", run.createdAt],
    ["環境", run.environment],
    ["模型", run.overrides.openrouter_model ?? "unknown"],
    ["套件版本", run.scriptVersion],
    ["每案重複次數", run.repetitions],
    ["對話契約指紋", run.contractSha256],
    ["資料庫版本", run.corpusVersion],
    ["完整設定", JSON.stringify(run.overrides)],
    [
      "已覆核可判定通過率",
      stats.passRate === null
        ? "—"
        : `${(stats.passRate * 100).toFixed(2)}% (${stats.pass}/${stats.pass + stats.fail})`,
    ],
    ["重大失敗／已覆核", `${stats.critical}/${stats.reviewed}`],
    ["待覆核／已完成", `${stats.unreviewed}/${stats.completed}`],
    ["無法判定／已覆核", `${stats.indeterminate}/${stats.reviewed}`],
    ["操作錯誤／案例測次", `${stats.operationErrors}/${stats.total}`],
    ["已發出請求（含重試）", stats.requests],
    ["預定對話請求", stats.plannedRequests],
    ["已知模型呼叫", stats.modelCalls],
    ["模型呼叫數未知的請求", stats.unknownModelCalls],
    ["計時範圍", "完整非串流回覆耗時；不代表首字延遲。"],
    ["判分說明", "規則只提供證據。未執行、待覆核與無法判定不計入可判定通過率分母。"],
  ];

  const settings: Cell[][] = [
    ["項目", "值", "分段"],
    ...settingsRows.flatMap((values) => splitRow(values, 1)),
  ];

  const names = ["測試總表", "逐輪紀錄", "執行設定"];
  const entries: Record<string, Uint8Array> = {};
  const put = (path: string, data: string) => {
    entries[path] = strToU8(data);
  };

  put(
    "[Content_Types].xml",
    [
      `<?xml version="1.0" encoding="UTF-8"?>`,
      `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`,
      `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>`,
      `<Default Extension="xml" ContentType="application/xml"/>`,
      `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>`,
      `<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>`,
      names
        .map(
          (_, i) =>
            `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
        )
        .join(""),
      `</Types>`,
    ].join(""),
  );

  put(
    "_rels/.rels",
    `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
  );

  put(
    "xl/workbook.xml",
    `<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="${NS}" xmlns:r="${REL}"><sheets>${names
      .map((name, i) => `<sheet name="${name}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`)
      .join("")}</sheets></workbook>`,
  );

  put(
    "xl/_rels/workbook.xml.rels",
    `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${names
      .map(
        (_, i) =>
          `<Relationship Id="rId${i + 1}" Type="${REL}/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`,
      )
      .join("")}<Relationship Id="rId4" Type="${REL}/styles" Target="styles.xml"/></Relationships>`,
  );

  put(
    "xl/styles.xml",
    [
      `<?xml version="1.0"?>`,
      `<styleSheet xmlns="${NS}">`,
      `<fonts count="2">`,
      `<font><sz val="11"/><name val="Microsoft JhengHei"/></font>`,
      `<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft JhengHei"/></font>`,
      `</fonts>`,
      `<fills count="3">`,
      `<fill><patternFill patternType="none"/></fill>`,
      `<fill><patternFill patternType="gray125"/></fill>`,
      `<fill><patternFill patternType="solid"><fgColor rgb="FF794117"/><bgColor indexed="64"/></patternFill></fill>`,
      `</fills>`,
      `<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>`,
      `<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>`,
      `<cellXfs count="3">`,
      `<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>`,
      `<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>`,
      `<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>`,
      `</cellXfs>`,
      `<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>`,
      `</styleSheet>`,
    ].join(""),
  );

  [summary, turns, settings].forEach((rows, index) =>
    put(
      `xl/worksheets/sheet${index + 1}.xml`,
      worksheet(
        rows,
        index === 2 ? [32, 95, 12] : rows[0].map((_, i) => (i < 2 ? 15 : i < 7 ? 40 : 25)),
      ),
    ),
  );
  return zipSync(entries, { level: 6 });
}
