import { useEffect, useRef, useState } from "react";
import { getRuntimeConfig } from "../services/api";
import {
  bindColumn,
  defaultTemplate,
  importBatchTemplate,
  materializeCases,
  prepareScriptRun,
  scriptInputEnd,
  type BatchTemplate,
  type MetadataKey,
  type Parallelism,
  type ScriptCase,
  type ScriptRun,
} from "../services/batchScript";
import { refreshBatchEvidence, runScriptBatch } from "../services/batchRunner";
import {
  asBehaviorRun,
  exportBatchWorkbook,
  exportScriptRun,
  importScriptRun,
  lawRows,
  resultHeaders,
} from "../services/batchRecords";
import { behaviorStatistics, currentBehaviorReview } from "../services/behaviorRecords";
import {
  BEHAVIOR_STATUS,
  REVIEW_LABELS,
  builtinBehaviorCases,
  type BehaviorReview,
} from "../services/behaviorTests";
import { exportBehaviorWorkbook } from "../services/behaviorWorkbook";
import { formatBatchRate, LAW_MATCH_RULE, scoreLawBatch } from "../services/lawBatchScoring";
import {
  columnName,
  downloadLocalFile,
  insertScenarioResults,
  MAX_WORKBOOK_BYTES,
  parseScenarioWorkbook,
  scenarioColumns,
  type ScenarioWorkbook,
} from "../services/scenarioWorkbook";
import ReviewForm from "./BatchReviewForm";
import { BATCH_STATUS_LABELS } from "../services/applicableLawBatch";

interface Props {
  csrfToken: string;
  onHasDataChange: (hasData: boolean) => void;
}

const BUTTON_CLASS =
  "min-h-11 rounded-lg border border-outline/25 px-3 py-2 text-sm font-semibold disabled:opacity-40";
const PRIMARY_BUTTON_CLASS = `${BUTTON_CLASS} bg-primary text-white`;
const METADATA_LABELS: [MetadataKey, string][] = [
  ["id", "案例 ID"],
  ["category", "分類"],
  ["reference", "參考答案"],
  ["expected", "預期行為"],
  ["criticalFailure", "重大失敗條件"],
];
const PAGE_SIZE = 10;

export default function AdminBatchPrediction({ csrfToken, onHasDataChange }: Props) {
  const [template, setTemplate] = useState<BatchTemplate>(defaultTemplate);
  const [workbook, setWorkbook] = useState<ScenarioWorkbook | null>(null);
  const [sheetIndex, setSheetIndex] = useState(0);
  const [headerRow, setHeaderRow] = useState(1);
  const [presets, setPresets] = useState<ScriptCase[] | null>(null);
  const [hiddenTerms, setHiddenTerms] = useState("");
  const [run, setRun] = useState<ScriptRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [page, setPage] = useState(0);

  const controller = useRef<AbortController | null>(null);
  const pauseRequested = useRef(false);
  const alive = useRef(true);

  const sheet = workbook?.sheets[sheetIndex];
  const columns = sheet ? scenarioColumns(sheet, headerRow) : [];
  const locked = busy || running;
  const editingLocked = locked || !!run;
  const hasData = !!workbook || !!run || !!presets;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);

  useEffect(() => {
    onHasDataChange(hasData);
  }, [hasData, onHasDataChange]);

  useEffect(() => {
    if (!hasData) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [hasData]);

  const reportError = (failure: unknown) => {
    if (alive.current) {
      setError(failure instanceof Error ? failure.message : "無法完成操作。");
    }
  };

  const safeAction = (action: () => void) => {
    try {
      action();
    } catch (failure) {
      reportError(failure);
    }
  };

  const configureSheet = (book: ScenarioWorkbook, index: number, header: number) => {
    const target = book.sheets[index];
    const options = scenarioColumns(target, header);
    const input = options.find((item) => /情境|場景|訊息|Prompt/.test(item.label)) ?? options[0];
    const reference = options.find((item) => /適用法規|參考答案|正確答案/.test(item.label));
    setSheetIndex(index);
    setHeaderRow(header);
    setTemplate((previous) => ({
      ...previous,
      builtinCaseIds: undefined,
      rounds: [{ columns: input ? [bindColumn(target, header, input.index)] : [], text: "" }],
      metadata: reference ? { reference: bindColumn(target, header, reference.index) } : {},
    }));
  };

  const load = async (file: File | undefined, kind: "xlsx" | "template" | "run") => {
    if (!file || editingLocked) return;
    setBusy(true);
    setError("");
    try {
      const maxSize =
        kind === "xlsx"
          ? MAX_WORKBOOK_BYTES
          : kind === "template"
            ? 1024 * 1024
            : 20 * 1024 * 1024;
      if (file.size > maxSize) throw new Error("檔案超過大小限制。");

      if (kind === "xlsx") {
        const book = parseScenarioWorkbook(new Uint8Array(await file.arrayBuffer()), file.name);
        if (!alive.current) return;
        setWorkbook(book);
        setPresets(null);
        configureSheet(book, 0, 1);
      } else if (kind === "template") {
        const next = importBatchTemplate(await file.text());
        if (alive.current) {
          setTemplate(next);
          setPresets(
            next.builtinCaseIds
              ? builtinBehaviorCases()
                  .filter((test) => next.builtinCaseIds!.includes(test.id))
                  .map((test) => ({ ...test, confirmed: true }))
              : null,
          );
          if (next.builtinCaseIds) setWorkbook(null);
        }
      } else {
        const imported = await importScriptRun(await file.text());
        if (alive.current) {
          setRun(imported);
          setTemplate(imported.template);
          setWorkbook(null);
          setPresets(null);
          setSelected(0);
          setPage(0);
          setConfirmed(false);
          setBlocked(null);
        }
      }
    } catch (failure) {
      reportError(failure);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const prepare = async () => {
    if (editingLocked) return;
    setBusy(true);
    setError("");
    try {
      const cases =
        presets ??
        (workbook && sheet
          ? materializeCases(workbook, sheet, headerRow, template, hiddenTerms.split("\n"))
          : null);
      if (!cases?.length) throw new Error("請先匯入 Excel 或選擇內建案例。");
      // Exercise the full export path, including workbook protection, before retrieving settings or submitting jobs.
      if (workbook && sheet) {
        insertScenarioResults(
          workbook,
          sheet,
          headerRow,
          scriptInputEnd(template),
          resultHeaders({ cases, mode: template.mode, repetitions: template.repetitions }),
          [],
          [],
        );
      }
      const config = await getRuntimeConfig(csrfToken);
      const prepared = await refreshBatchEvidence(prepareScriptRun(cases, template, config));
      if (alive.current) {
        setRun(prepared);
        setConfirmed(false);
        setBlocked(null);
        setSelected(0);
        setPage(0);
      }
    } catch (failure) {
      reportError(failure);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const start = async (retryFailed = false) => {
    if (!run || controller.current || !confirmed || blocked === "contract" || blocked === csrfToken) {
      return;
    }
    const abort = new AbortController();
    controller.current = abort;
    pauseRequested.current = false;
    setRunning(true);
    setPausing(false);
    setError("");
    try {
      const config = await getRuntimeConfig(csrfToken);
      const contract =
        run.mode === "chat"
          ? config.chat_test_contract_sha256
          : config.applicable_laws_contract_sha256;
      if (abort.signal.aborted || !alive.current) return;
      if (contract !== run.contractSha256) {
        setBlocked("contract");
        throw new Error("測試契約已變更，請下載紀錄並重新準備。");
      }
      const result = await runScriptBatch({
        run,
        token: csrfToken,
        signal: abort.signal,
        shouldPause: () => pauseRequested.current,
        retryFailed,
        hiddenTerms: hiddenTerms.split("\n"),
        onUpdate: (next) => {
          if (alive.current) setRun(next);
        },
      });
      if (alive.current) {
        setRun(result.run);
        if (result.stoppedReason) {
          setError(result.stoppedReason);
          if (result.stoppedReason.includes("契約已變更")) setBlocked("contract");
          else if (result.stoppedReason.includes("驗證失效")) setBlocked(csrfToken);
        }
      }
    } catch (failure) {
      reportError(failure);
    } finally {
      controller.current = null;
      if (alive.current) {
        setRunning(false);
        setPausing(false);
      }
    }
  };

  const saveReview = async (review: Omit<BehaviorReview, "evidenceSha256" | "reviewedAt">) => {
    if (!run || locked) return;
    setBusy(true);
    try {
      const next = await refreshBatchEvidence(run);
      next.records[selected] = {
        ...next.records[selected],
        review: {
          ...review,
          evidenceSha256: next.records[selected].evidenceSha256,
          reviewedAt: new Date().toISOString(),
        },
      };
      if (alive.current) setRun(next);
    } catch (failure) {
      reportError(failure);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const downloadExcel = () =>
    safeAction(() => {
      if (!run) return;
      const bytes =
        workbook && sheet
          ? exportBatchWorkbook(run, workbook, sheet, headerRow, scriptInputEnd(run.template))
          : exportBehaviorWorkbook(asBehaviorRun(run));
      const filename = workbook
        ? workbook.fileName.replace(/\.xlsx$/i, ".predicted.xlsx")
        : `batch-${run.runId}.xlsx`;
      downloadLocalFile(
        bytes,
        filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      );
    });

  const stats = run && behaviorStatistics(asBehaviorRun(run));
  const lawStats = run?.mode === "law" ? scoreLawBatch(lawRows(run)) : null;
  const record = run?.records[selected];
  const test = run?.cases.find((item) => item.id === record?.caseId);
  const blockedNow = blocked === "contract" || blocked === csrfToken;

  const editRound = (index: number, change: Partial<BatchTemplate["rounds"][number]>) =>
    setTemplate((previous) => ({
      ...previous,
      rounds: previous.rounds.map((round, i) => (i === index ? { ...round, ...change } : round)),
    }));

  const moveRound = (index: number, direction: number) =>
    setTemplate((previous) => {
      const rounds = [...previous.rounds];
      [rounds[index], rounds[index + direction]] = [rounds[index + direction], rounds[index]];
      return { ...previous, rounds };
    });

  return (
    <section aria-label="批次預測" className="mx-auto max-w-5xl space-y-5">
      <header>
        <h3 className="text-xl font-bold">批次預測</h3>
        <p className="mt-2 text-sm leading-relaxed text-on-surface/65">
          一般對話與適用法規共用多輪腳本。選擇欄位、預覽送出內容，再執行與覆核。
        </p>
      </header>

      {error && (
        <p
          role="alert"
          className="whitespace-pre-wrap break-words rounded-lg bg-error/10 p-3 text-sm text-error"
        >
          {error}
        </p>
      )}

      <details open={!run} className="rounded-xl border border-outline/20 p-4">
        <summary className="cursor-pointer text-sm font-semibold">
          欄位與輪次設定{run ? "（已固定）" : ""}
        </summary>
        <fieldset disabled={editingLocked} className="mt-4 space-y-4 disabled:opacity-60">
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-sm font-medium">
              測試模式
              <select
                aria-label="測試模式"
                value={template.mode}
                className="input mt-2"
                onChange={(event) => {
                  setTemplate({
                    ...template,
                    builtinCaseIds: undefined,
                    mode: event.target.value as BatchTemplate["mode"],
                  });
                  setPresets(null);
                }}
              >
                <option value="law">適用法規預測</option>
                <option value="chat">一般對話</option>
              </select>
            </label>
            <label className="text-sm font-medium">
              並行案例
              <select
                aria-label="並行案例"
                value={template.parallelism}
                className="input mt-2"
                onChange={(event) =>
                  setTemplate({
                    ...template,
                    parallelism: Number(event.target.value) as Parallelism,
                  })
                }
              >
                {[1, 2, 4, 8].map((value) => (
                  <option key={value} value={value}>
                    {value} 個
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm font-medium">
              每案重複次數
              <select
                aria-label="每案重複次數"
                value={template.repetitions}
                className="input mt-2"
                onChange={(event) =>
                  setTemplate({
                    ...template,
                    repetitions: Number(event.target.value) as 1 | 3,
                  })
                }
              >
                <option value={1}>1 次</option>
                <option value={3}>3 次</option>
              </select>
            </label>
          </div>

          <label className="block text-sm font-semibold">
            Excel 檔案
            <input
              aria-label="Excel 檔案"
              type="file"
              accept=".xlsx"
              onChange={(event) => {
                void load(event.target.files?.[0], "xlsx");
                event.target.value = "";
              }}
              className="mt-2 block w-full min-w-0 rounded-lg border border-dashed border-outline/30 p-3 text-xs"
            />
          </label>
          <p className="text-xs text-on-surface/60">
            .xlsx 最多 10 MB；一般對話 100 案、法規預測 5,000 案；每案最多 10 輪，每輪 2,000 字。每輪依選取順序組合欄位，再接上固定文字。
          </p>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">
              匯入腳本範本 JSON
              <input
                aria-label="匯入腳本範本 JSON"
                type="file"
                accept=".json"
                onChange={(event) => {
                  void load(event.target.files?.[0], "template");
                  event.target.value = "";
                }}
                className="mt-2 block w-full min-w-0 text-xs"
              />
            </label>
            <label className="text-sm">
              匯入執行紀錄 JSON
              <input
                aria-label="匯入執行紀錄 JSON"
                type="file"
                accept=".json"
                onChange={(event) => {
                  void load(event.target.files?.[0], "run");
                  event.target.value = "";
                }}
                className="mt-2 block w-full min-w-0 text-xs"
              />
            </label>
          </div>

          {template.mode === "chat" && (
            <details className="rounded-lg border border-outline/20 p-3">
              <summary className="cursor-pointer text-sm">內建行為案例</summary>
              <p className="my-2 text-xs">
                可選擇一項或完整五項，載入完整對話腳本與覆核條件。
              </p>
              <div className="flex flex-wrap gap-2">
                {[{ id: "all", category: "全部五項" }, ...builtinBehaviorCases()].map((item) => (
                  <button
                    key={item.id}
                    className={BUTTON_CLASS}
                    onClick={() => {
                      const matched = builtinBehaviorCases().filter(
                        (candidate) => item.id === "all" || candidate.id === item.id,
                      );
                      setWorkbook(null);
                      setPresets(matched.map((candidate) => ({ ...candidate, confirmed: true })));
                      setTemplate({
                        ...template,
                        builtinCaseIds: matched.map((candidate) => candidate.id),
                        metadata: {},
                        rounds: [{ columns: [], text: "沿用內建案例的逐案腳本" }],
                      });
                    }}
                  >
                    載入 {item.category}
                  </button>
                ))}
              </div>
            </details>
          )}

          {workbook && (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm">
                工作表
                <select
                  aria-label="工作表"
                  className="input mt-2"
                  value={sheetIndex}
                  onChange={(event) => configureSheet(workbook, Number(event.target.value), 1)}
                >
                  {workbook.sheets.map((item, index) => (
                    <option key={item.path} value={index}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-sm">
                標題列
                <input
                  aria-label="標題列"
                  type="number"
                  min={1}
                  max={sheet?.maxRow}
                  className="input mt-2"
                  value={headerRow}
                  onChange={(event) =>
                    configureSheet(workbook, sheetIndex, Number(event.target.value))
                  }
                />
              </label>
            </div>
          )}

          {!presets && (
            <section aria-label="輪次腳本" className="space-y-3">
              {template.rounds.map((round, index) => (
                <div key={index} className="space-y-3 rounded-xl border border-outline/20 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">第 {index + 1} 輪</h4>
                    <div className="flex gap-2">
                      <button
                        aria-label={`上移第 ${index + 1} 輪`}
                        className={BUTTON_CLASS}
                        disabled={!index}
                        onClick={() => moveRound(index, -1)}
                      >
                        上移
                      </button>
                      <button
                        aria-label={`下移第 ${index + 1} 輪`}
                        className={BUTTON_CLASS}
                        disabled={index === template.rounds.length - 1}
                        onClick={() => moveRound(index, 1)}
                      >
                        下移
                      </button>
                      <button
                        aria-label={`刪除第 ${index + 1} 輪`}
                        className={BUTTON_CLASS}
                        disabled={template.rounds.length === 1}
                        onClick={() =>
                          setTemplate({
                            ...template,
                            rounds: template.rounds.filter((_, i) => i !== index),
                          })
                        }
                      >
                        刪除
                      </button>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {round.columns.map((binding, i) => (
                      <button
                        key={binding.index}
                        className={BUTTON_CLASS}
                        onClick={() =>
                          editRound(index, {
                            columns: round.columns.filter((_, j) => i !== j),
                          })
                        }
                        aria-label={`移除第 ${index + 1} 輪欄位 ${columnName(binding.index)}`}
                      >
                        {i + 1}. {columnName(binding.index)} · {binding.header || "無標題"} ×
                      </button>
                    ))}
                  </div>
                  <label className="block text-sm">
                    加入來源欄位
                    <select
                      aria-label={`第 ${index + 1} 輪加入來源欄位`}
                      className="input mt-1"
                      value=""
                      onChange={(event) => {
                        if (sheet && event.target.value !== "") {
                          editRound(index, {
                            columns: [
                              ...round.columns,
                              bindColumn(sheet, headerRow, Number(event.target.value)),
                            ],
                          });
                        }
                      }}
                    >
                      <option value="">選擇欄位（可加入多欄）</option>
                      {columns
                        .filter((col) => !round.columns.some((item) => item.index === col.index))
                        .map((col) => (
                          <option key={col.index} value={col.index}>
                            {col.label}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="block text-sm">
                    固定文字
                    <textarea
                      aria-label={`第 ${index + 1} 輪固定文字`}
                      rows={2}
                      maxLength={2000}
                      className="input mt-1"
                      value={round.text}
                      onChange={(event) => editRound(index, { text: event.target.value })}
                      placeholder="可留空；沒有來源欄位時請填寫完整訊息"
                    />
                  </label>
                </div>
              ))}
              <button
                className={BUTTON_CLASS}
                disabled={template.rounds.length >= 10}
                onClick={() =>
                  setTemplate({
                    ...template,
                    rounds: [...template.rounds, { columns: [], text: "" }],
                  })
                }
              >
                新增一輪
              </button>
            </section>
          )}

          {sheet && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">
                可選欄位：ID、分類、參考答案與覆核條件
              </summary>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                {METADATA_LABELS.map(([key, label]) => (
                  <label key={key} className="text-sm">
                    {label}
                    <select
                      aria-label={`${label}欄位`}
                      className="input mt-1"
                      value={template.metadata[key]?.index ?? ""}
                      onChange={(event) => {
                        const metadata = { ...template.metadata };
                        if (event.target.value === "") delete metadata[key];
                        else {
                          metadata[key] = bindColumn(sheet, headerRow, Number(event.target.value));
                        }
                        setTemplate({ ...template, metadata });
                      }}
                    >
                      <option value="">不設定{key === "id" ? "（自動產生）" : ""}</option>
                      {columns.map((col) => (
                        <option key={col.index} value={col.index}>
                          {col.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              <p className="mt-2 text-xs text-on-surface/60">
                以上欄位只在本機使用；只有輪次引用的內容會送出。
              </p>
            </details>
          )}

          {!presets && (
            <details>
              <summary className="cursor-pointer text-sm">自訂隱藏詞</summary>
              <textarea
                aria-label="自訂隱藏詞"
                rows={3}
                className="input mt-2"
                placeholder="每行一項"
                value={hiddenTerms}
                onChange={(event) => setHiddenTerms(event.target.value)}
              />
            </details>
          )}

          {sheet && (
            <p className="text-xs text-on-surface/60">
              結果將集中插入{" "}
              {scriptInputEnd(template) < 0
                ? "第一欄前"
                : `${columnName(scriptInputEnd(template))} 欄後`}
              ，右側原欄位會向右移動。輸出新副本，原檔不覆寫。
            </p>
          )}

          <button
            className={BUTTON_CLASS}
            onClick={() =>
              safeAction(() =>
                downloadLocalFile(
                  JSON.stringify(importBatchTemplate(JSON.stringify(template)), null, 2),
                  "batch-template.json",
                  "application/json",
                ),
              )
            }
          >
            下載腳本範本
          </button>
        </fieldset>
      </details>

      {!run && (
        <button
          className={PRIMARY_BUTTON_CLASS}
          disabled={locked || (!sheet && !presets)}
          onClick={() => void prepare()}
        >
          {busy ? "準備中…" : "準備預測"}
        </button>
      )}

      {run && (
        <>
          <section
            className="space-y-3 rounded-xl border border-outline/20 bg-surface-container-low p-4"
            aria-label="送出預覽"
          >
            <p className="break-words text-sm font-semibold">
              {run.mode === "chat" ? "一般對話" : "適用法規"} · {run.cases.length} 案 ×{" "}
              {run.repetitions} 次 · 並行 {run.template.parallelism}
            </p>
            <p className="break-all text-sm">
              模型：{run.overrides.openrouter_model} · 經 OpenRouter 執行
            </p>
            <p className="text-xs leading-relaxed text-on-surface/65">
              請檢查全部遮罩後腳本。後續輪次會承接同一案例的歷史與摘要，送出前再次本機遮罩。開始後會產生模型費用；自動遮罩可能漏掉個資。紀錄只保留在本頁，離開前請下載。
            </p>
            {run.cases.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((caseItem) => (
              <details key={caseItem.id} className="rounded-lg border border-outline/15 p-3">
                <summary className="cursor-pointer text-sm">
                  {caseItem.id} · {caseItem.source.sheet} 第 {caseItem.source.row} 列 ·{" "}
                  {caseItem.turns.length} 輪
                </summary>
                {caseItem.turns.map((text, i) => (
                  <pre key={i} className="mt-2 whitespace-pre-wrap break-words text-xs">
                    第 {i + 1} 輪：{text}
                  </pre>
                ))}
              </details>
            ))}
            <div className="flex items-center justify-between gap-2">
              <button className={BUTTON_CLASS} disabled={!page} onClick={() => setPage(page - 1)}>
                上一頁
              </button>
              <span className="text-xs">
                {page + 1} / {Math.ceil(run.cases.length / PAGE_SIZE)}
              </span>
              <button
                className={BUTTON_CLASS}
                disabled={(page + 1) * PAGE_SIZE >= run.cases.length}
                onClick={() => setPage(page + 1)}
              >
                下一頁
              </button>
            </div>
            <details>
              <summary className="cursor-pointer text-xs">固定設定與契約</summary>
              <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all text-xs">
                {JSON.stringify(
                  {
                    environment: run.environment,
                    contractSha256: run.contractSha256,
                    overrides: run.overrides,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <label className="flex min-h-11 items-center gap-3 text-sm">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={locked}
                onChange={(event) => setConfirmed(event.target.checked)}
                className="h-5 w-5 shrink-0 accent-primary"
              />
              已確認本次設定與全部腳本，允許開始或續跑
            </label>
          </section>

          <div className="flex flex-wrap gap-2">
            {!running && (
              <button
                className={PRIMARY_BUTTON_CLASS}
                disabled={
                  busy ||
                  !confirmed ||
                  blockedNow ||
                  !run.records.some((item) => ["pending", "paused"].includes(item.status))
                }
                onClick={() => void start()}
              >
                {run.records.every((item) => item.status === "completed")
                  ? "已完成本次預測"
                  : `確認送出並${
                      run.records.some((item) => item.turns.length) ? "繼續未完成預測" : "開始預測"
                    }`}
              </button>
            )}
            {!running &&
              run.records.some((item) => ["error", "interrupted"].includes(item.status)) && (
                <button
                  className={BUTTON_CLASS}
                  disabled={busy || !confirmed || blockedNow}
                  onClick={() => void start(true)}
                >
                  手動重試失敗輪次（會再次呼叫模型）
                </button>
              )}
            {running && (
              <>
                <button
                  className={BUTTON_CLASS}
                  disabled={pausing}
                  onClick={() => {
                    pauseRequested.current = true;
                    setPausing(true);
                  }}
                >
                  {pausing ? "等待已送出輪次完成…" : "完成本輪後暫停"}
                </button>
                <button className={BUTTON_CLASS} onClick={() => controller.current?.abort()}>
                  停止並取消進行中工作
                </button>
              </>
            )}
            <button
              className={BUTTON_CLASS}
              onClick={() =>
                safeAction(() =>
                  downloadLocalFile(
                    JSON.stringify(
                      run.cases.map((item) => ({
                        id: item.id,
                        source: item.source,
                        turns: item.turns,
                      })),
                      null,
                      2,
                    ),
                    "batch-preview.json",
                    "application/json",
                  ),
                )
              }
            >
              下載送出預覽
            </button>
            <button
              className={BUTTON_CLASS}
              onClick={() =>
                safeAction(() =>
                  downloadLocalFile(
                    exportScriptRun(run),
                    `batch-${run.runId}.json`,
                    "application/json",
                  ),
                )
              }
            >
              下載完整 JSON
            </button>
            <button className={BUTTON_CLASS} onClick={downloadExcel}>
              {workbook ? "下載結果 Excel" : "下載紀錄 Excel"}
            </button>
            <button
              className={BUTTON_CLASS}
              disabled={locked}
              onClick={() => {
                if (window.confirm("清除本次預測？尚未下載的結果將消失。")) {
                  setRun(null);
                  setConfirmed(false);
                  setBlocked(null);
                  setError("");
                }
              }}
            >
              清除本次預測
            </button>
          </div>

          {stats && (
            <section aria-label="批次預測統計" className="space-y-3">
              <p aria-live="polite" className="text-sm">
                完成 {stats.completed} / {stats.total} · 失敗 {stats.operationErrors} · 待執行{" "}
                {stats.pending} · 已送出 {stats.requests} 個請求
              </p>
              <progress
                aria-label="預測進度"
                className="h-2 w-full accent-primary"
                max={stats.total}
                value={stats.completed + stats.operationErrors}
              />
              {run.mode === "chat" && (
                <p className="text-sm">
                  已覆核通過率{" "}
                  {stats.passRate === null ? "—" : `${(stats.passRate * 100).toFixed(1)}%`}（
                  {stats.pass} / {stats.pass + stats.fail}） · 重大失敗 {stats.critical} · 待覆核{" "}
                  {stats.unreviewed} · 無法判定 {stats.indeterminate}
                </p>
              )}
              {lawStats && (
                <>
                  <p className="text-xs text-on-surface/65">
                    {lawStats.pending ? "暫時計算 · " : ""}依每案測次的最後一輪統計。
                    {LAW_MATCH_RULE}
                  </p>
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    {lawStats.metrics.map((metric) => (
                      <div key={metric.label} className="rounded-lg border border-outline/20 p-3">
                        <p className="text-xs">{metric.label}</p>
                        <p className="mt-2 text-xl font-semibold">{formatBatchRate(metric)}</p>
                        <p className="text-xs">
                          {metric.numerator} / {metric.denominator}
                        </p>
                      </div>
                    ))}
                  </div>
                  <p className="text-xs text-on-surface/60">
                    資訊不足 {lawStats.insufficient} · 不適用三法 {lawStats.noTarget} ·
                    參考答案空白 {lawStats.referenceMissing} · 無法識別 {lawStats.referenceInvalid}
                  </p>
                </>
              )}
            </section>
          )}

          <label className="block text-sm font-medium">
            檢視案例測次
            <select
              aria-label="檢視案例測次"
              className="input mt-2"
              value={selected}
              onChange={(event) => setSelected(Number(event.target.value))}
            >
              {run.records.map((item, index) => (
                <option key={`${item.caseId}:${item.repetition}`} value={index}>
                  {item.caseId} · 測次 {item.repetition} · {BEHAVIOR_STATUS[item.status]} ·{" "}
                  {currentBehaviorReview(item)
                    ? REVIEW_LABELS[currentBehaviorReview(item)!.verdict]
                    : "待覆核"}
                </option>
              ))}
            </select>
          </label>

          {record && test && (
            <article className="space-y-3 rounded-xl border border-outline/20 p-4">
              <h4 className="font-semibold">
                {test.id} · {test.category || "未分類"}
              </h4>
              <p className="text-sm">
                預期行為：{test.expected || "未設定"}
                <br />
                重大失敗條件：{test.criticalFailure || "未設定"}
              </p>
              {record.turns.map((turn) => (
                <details
                  key={`${turn.number}:${turn.attempt}`}
                  className="rounded-lg border border-outline/15 p-3"
                >
                  <summary className="cursor-pointer text-sm">
                    第 {turn.number} 輪 · 嘗試 {turn.attempt} · {BEHAVIOR_STATUS[turn.status]} ·{" "}
                    {turn.elapsedMs == null ? "—" : `${(turn.elapsedMs / 1000).toFixed(1)} 秒`}
                  </summary>
                  <p className="mt-3 whitespace-pre-wrap break-words text-sm">
                    送出：{turn.request.message}
                  </p>
                  <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed">
                    {turn.result?.response.reply ?? turn.prediction?.predicted_laws.join("、") ?? ""}
                    {turn.prediction?.decision_reason}
                  </p>
                  {turn.prediction && (
                    <p className="mt-2 text-xs">
                      {BATCH_STATUS_LABELS[turn.prediction.decision_status]}
                    </p>
                  )}
                  {turn.error && <p className="mt-2 text-sm text-error">{turn.error}</p>}
                  {turn.issues.map((issue, i) => (
                    <p key={i} className="mt-2 whitespace-pre-wrap break-words text-xs">
                      {issue.text}
                      <br />
                      {issue.evidence}
                    </p>
                  ))}
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs">完整歷史、摘要與診斷</summary>
                    <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {JSON.stringify(turn, null, 2)}
                    </pre>
                  </details>
                </details>
              ))}
              <ReviewForm
                key={`${record.caseId}:${record.repetition}:${record.evidenceSha256}:${record.review?.reviewedAt}`}
                record={record}
                disabled={locked}
                onSave={saveReview}
              />
            </article>
          )}
        </>
      )}
    </section>
  );
}
