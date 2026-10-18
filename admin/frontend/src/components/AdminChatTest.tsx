import { useEffect, useRef, useState } from "react";
import type { RuntimeConfig } from "../services/api";
import { DEFAULT_PIPELINE } from "../services/pipeline";
import { reproducibleOverrides } from "../services/adminDiagnostics";
import { temporaryOverrides } from "../services/runtimeConfig";
import {
  useDiagnosticChat,
  predictionText,
  type TestMode,
  type TestRun,
} from "../hooks/useDiagnosticChat";
import { I18nProvider } from "../../../../client/frontend/src/i18n";
import ChatInput from "../../../../client/frontend/src/components/ChatInput";
import MessageItem from "../../../../client/frontend/src/components/MessageItem";
import CaseSummary from "../../../../client/frontend/src/components/CaseSummary";
import TypingIndicator from "../../../../client/frontend/src/components/TypingIndicator";
import MaterialIcon from "./MaterialIcon";
import DiagnosticSettings from "./DiagnosticSettings";

const DIAGNOSTIC_SECTIONS = [
  ["effective_config", "生效設定"],
  ["pii", "PII 處理"],
  ["input_stages", "輸入處理前後"],
  ["history", "歷史裁切"],
  ["retrieval", "檢索結果"],
  ["rewrites", "內容改寫"],
] as const;

export default function AdminChatTest(props: {
  csrfToken: string;
  config: RuntimeConfig;
  visible?: boolean;
}) {
  return (
    <I18nProvider manageDocument={false}>
      <DiagnosticConversation {...props} />
    </I18nProvider>
  );
}

function DiagnosticConversation({
  csrfToken,
  config,
  visible = true,
}: {
  csrfToken: string;
  config: RuntimeConfig;
  visible?: boolean;
}) {
  const [temporary, setTemporary] = useState(() => ({
    ...structuredClone(config),
    pipeline: { ...DEFAULT_PIPELINE, ...config.pipeline },
  }));
  const [mode, setMode] = useState<TestMode>("chat");
  const [showEmotions, setShowEmotions] = useState(true);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelTab, setPanelTab] = useState<"settings" | "diagnostics">("settings");
  const [composerKey, setComposerKey] = useState(0);

  const chat = useDiagnosticChat(csrfToken, temporary, mode);
  const end = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const settingsForm = useRef<HTMLFormElement>(null);

  const latest = chat.runs.at(-1);
  const lastMessage = chat.messages.at(-1);
  const replyPrompt =
    mode === "chat" &&
    lastMessage?.role === "assistant" &&
    !lastMessage.isError &&
    !lastMessage.isCancelled &&
    !lastMessage.superseded
      ? lastMessage
      : undefined;
  const latestAssistant = [...chat.messages]
    .reverse()
    .find(
      (item) =>
        item.role === "assistant" && !item.isError && !item.isCancelled && !item.superseded,
    )?.id;
  const comparisonConfig = reproducibleOverrides(latest?.result.diagnostics.effective_config);
  const changed =
    JSON.stringify(temporaryOverrides(temporary)) !== JSON.stringify(temporaryOverrides(config));
  const canRegenerate =
    mode === "chat" &&
    chat.messages.some(
      (item) =>
        item.role === "user" && item.requestKind === "request" && item.diagnosticMode === "chat",
    );

  useEffect(() => {
    if (visible) {
      end.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
    }
  }, [chat.messages, chat.running, visible]);

  useEffect(() => {
    if (!panelOpen || !visible) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => previous?.focus();
  }, [panelOpen, visible]);

  const closePanel = () => setPanelOpen(false);
  const reset = () => {
    if (
      chat.messages.length &&
      !window.confirm("清除這次診斷對話、摘要與結果？右側暫時設定會保留。")
    ) {
      return;
    }
    chat.clear();
    setComposerKey((value) => value + 1);
  };

  return (
    <section
      aria-label="本次診斷測試"
      className="flex h-full min-h-0 flex-col bg-surface-container-low"
    >
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-outline/15 bg-white px-4 py-3 lg:px-6">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-base font-bold">
            <MaterialIcon icon="science" size={20} />
            本次診斷測試
          </h3>
          <p className="mt-1 text-xs text-on-surface/55">
            多輪對話 · 使用 {config.environment_document_id ?? "目前環境"} 作為起點
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            data-summary-trigger
            aria-label="顯示及更改摘要"
            onClick={() => setSummaryOpen(true)}
            className="flex min-h-10 items-center gap-1 rounded-lg px-3 text-sm text-primary hover:bg-primary/5"
          >
            <MaterialIcon icon="description" size={18} />
            摘要
          </button>
          <button
            type="button"
            onClick={reset}
            className="min-h-10 rounded-lg px-3 text-sm hover:bg-surface-container"
          >
            新對話
          </button>
          <button
            type="button"
            onClick={() => setPanelOpen(true)}
            aria-label="開啟暫時設定面板"
            className="flex min-h-10 items-center gap-1 rounded-lg border border-outline/25 px-3 text-sm xl:hidden"
          >
            <MaterialIcon icon="tune" size={18} />
            設定
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-outline/10 px-4 py-2 text-xs text-on-surface/60 lg:px-6">
            <span className="min-w-0 truncate" title={temporary.openrouter_model}>
              {temporary.openrouter_model}
            </span>
            <span
              className={`shrink-0 rounded-full px-2 py-1 ${
                changed ? "bg-primary-container text-primary" : "bg-surface-container"
              }`}
            >
              {changed ? "已套用暫時設定" : "沿用環境設定"}
            </span>
          </div>

          <div
            aria-label="診斷對話訊息"
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-5 lg:px-6"
          >
            {!chat.messages.length && (
              <div className="mx-auto flex h-full max-w-lg flex-col justify-center py-5">
                <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-primary-container text-primary">
                  <MaterialIcon icon="chat_bubble" size={24} />
                </span>
                <h4 className="text-xl font-bold">像一般對話一樣開始測試</h4>
                <p className="mt-3 text-sm leading-7 text-on-surface/65">
                  輸入情境或附加圖片，接續選項回答，並查看心情分析與摘要。右側設定只影響這個診斷對話的後續請求。
                </p>
                <p className="mt-4 text-xs leading-6 text-on-surface/45">
                  對話與暫時設定只保留在本頁；切換功能時會繼續執行並保留內容，重新載入或登出後會清除。
                </p>
              </div>
            )}

            <div className="space-y-7">
              {chat.messages.map((message) => (
                <MessageItem
                  key={message.id}
                  message={message}
                  showEmotions={showEmotions}
                  showReasoning={temporary.enable_reasoning_display === true}
                  onOpenSummary={
                    message.id === latestAssistant ? () => setSummaryOpen(true) : undefined
                  }
                />
              ))}
              {chat.running && <TypingIndicator message="正在執行診斷，等待回覆…" />}
              <div ref={end} />
            </div>
          </div>

          {chat.error && (
            <p
              role="alert"
              className="mx-4 mb-3 rounded-lg bg-error-container/40 px-3 py-2 text-sm text-error"
            >
              {chat.error}
            </p>
          )}

          <ChatInput
            key={composerKey}
            inputLabel="測試訊息"
            onSend={(...args) => {
              if (settingsForm.current && !settingsForm.current.reportValidity()) {
                setPanelTab("settings");
                setPanelOpen(true);
                return false;
              }
              return chat.send(...args);
            }}
            isLoading={chat.running}
            onStop={chat.stop}
            replyPrompt={replyPrompt}
            caseRevision={chat.context.revision}
            activeContractVersion={4}
            allowImageUpload={temporary.enable_image_upload}
            onClarificationDraftChange={chat.saveClarificationDraft}
          />
        </div>

        {panelOpen && (
          <button
            type="button"
            tabIndex={-1}
            aria-label="關閉設定面板背景"
            onClick={closePanel}
            className="fixed inset-0 z-40 bg-black/25 xl:hidden"
          />
        )}

        <aside
          ref={panel}
          tabIndex={panelOpen ? -1 : undefined}
          role={panelOpen ? "dialog" : "complementary"}
          aria-modal={panelOpen || undefined}
          aria-label="暫時設定與診斷"
          className={`${
            panelOpen ? "fixed inset-y-0 right-0 z-50 flex w-[min(92vw,380px)] shadow-xl" : "hidden"
          } min-h-0 shrink-0 flex-col border-l border-outline/20 bg-white xl:static xl:z-auto xl:flex xl:w-[340px] xl:shadow-none 2xl:w-[380px]`}
          onKeyDown={(event) => {
            if (!panelOpen) return;
            if (event.key === "Escape") {
              event.preventDefault();
              closePanel();
            }
            if (event.key !== "Tab") return;
            const elements = Array.from(
              panel.current?.querySelectorAll<HTMLElement>(
                "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary",
              ) ?? [],
            ).filter((item) => item.getClientRects().length);
            const first = elements[0];
            const last = elements.at(-1);
            if (
              event.shiftKey &&
              (document.activeElement === first || document.activeElement === panel.current)
            ) {
              event.preventDefault();
              last?.focus();
            } else if (
              !event.shiftKey &&
              (document.activeElement === last || document.activeElement === panel.current)
            ) {
              event.preventDefault();
              first?.focus();
            }
          }}
        >
          <div className="shrink-0 border-b border-outline/15 p-4">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-bold">本次暫時設定</h4>
              <button
                type="button"
                aria-label="關閉暫時設定面板"
                onClick={closePanel}
                className="flex h-9 w-9 items-center justify-center rounded-lg hover:bg-surface-container xl:hidden"
              >
                <MaterialIcon icon="close" size={18} />
              </button>
            </div>
            <p className="mt-1 text-xs text-on-surface/55">
              不會儲存至環境；新請求使用送出當下的設定。
            </p>
            <div
              className="mt-4 flex rounded-lg bg-surface-container p-1"
              role="tablist"
              aria-label="診斷側欄"
            >
              <button
                role="tab"
                aria-selected={panelTab === "settings"}
                onClick={() => setPanelTab("settings")}
                className={`flex-1 rounded-md py-2 text-xs font-semibold ${
                  panelTab === "settings" ? "bg-white text-primary shadow-sm" : "text-on-surface/60"
                }`}
              >
                暫時設定
              </button>
              <button
                role="tab"
                aria-selected={panelTab === "diagnostics"}
                onClick={() => setPanelTab("diagnostics")}
                className={`flex-1 rounded-md py-2 text-xs font-semibold ${
                  panelTab === "diagnostics"
                    ? "bg-white text-primary shadow-sm"
                    : "text-on-surface/60"
                }`}
              >
                診斷結果{chat.runs.length ? ` · ${chat.runs.length}` : ""}
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">
            <form
              ref={settingsForm}
              hidden={panelTab !== "settings"}
              onSubmit={(event) => event.preventDefault()}
              className="space-y-5"
            >
              <label className="block text-sm">
                測試模式
                <select
                  className="input"
                  value={mode}
                  disabled={chat.running}
                  onChange={(event) => {
                    if (!chat.running) setMode(event.target.value as TestMode);
                  }}
                >
                  <option value="chat">一般對話</option>
                  <option value="applicable_laws_v1">適用法規預測</option>
                </select>
              </label>
              {mode === "applicable_laws_v1" && (
                <p className="text-xs leading-6 text-on-surface/60">
                  依主要處理法規優先順序預測，不追問；結果可在診斷頁籤查看。
                </p>
              )}
              <DiagnosticSettings
                config={temporary}
                onChange={(next) =>
                  setTemporary({ ...next, pipeline: { ...DEFAULT_PIPELINE, ...next.pipeline } })
                }
                csrfToken={csrfToken}
                disabled={chat.running}
                showEmotions={showEmotions}
                onShowEmotions={setShowEmotions}
              />
              <button
                type="button"
                disabled={chat.running || !changed}
                onClick={() =>
                  setTemporary({
                    ...structuredClone(config),
                    pipeline: { ...DEFAULT_PIPELINE, ...config.pipeline },
                  })
                }
                className="min-h-10 w-full rounded-lg border border-outline/30 text-sm disabled:opacity-40"
              >
                還原環境設定
              </button>
            </form>

            <div hidden={panelTab !== "diagnostics"} className="space-y-4">
              <p className="text-xs leading-6 text-on-surface/55">
                最近兩次請求的設定與處理結果。PII 比較重用原請求，不會追加到對話或更動摘要。
              </p>
              {latest && (
                <button
                  type="button"
                  disabled={chat.running || !comparisonConfig}
                  onClick={() => {
                    if (comparisonConfig) {
                      chat.compare(latest, {
                        ...comparisonConfig,
                        enable_anonymization: !comparisonConfig.enable_anonymization,
                      });
                    }
                  }}
                  className="min-h-10 w-full rounded-lg border border-primary/30 px-3 py-2 text-sm text-primary disabled:opacity-40"
                >
                  只切換後端 PII 再測一次
                </button>
              )}
              {!latest && (
                <p className="rounded-lg bg-surface-container-low p-4 text-sm text-on-surface/60">
                  送出第一則訊息後，可在這裡查看診斷。
                </p>
              )}
              {latest && !comparisonConfig && (
                <p className="text-xs text-on-surface/60">
                  本次未提供完整設定，無法建立 PII 比較。
                </p>
              )}
              {chat.running && (
                <button
                  type="button"
                  onClick={chat.stop}
                  className="min-h-10 rounded-lg border border-outline/30 px-3 text-sm"
                >
                  停止比較／測試
                </button>
              )}
              {!!chat.runs.length && (
                <div aria-label="最近兩次測試比較" className="space-y-4">
                  {chat.runs.map((run) => (
                    <DiagnosticResult key={run.id} run={run} />
                  ))}
                </div>
              )}
            </div>
          </div>
        </aside>
      </div>

      {visible && summaryOpen && (
        <CaseSummary
          context={chat.context}
          onSave={chat.saveSummary}
          onStartEdit={chat.stop}
          onClose={() => setSummaryOpen(false)}
          canRegenerate={canRegenerate}
        />
      )}
    </section>
  );
}

function DiagnosticResult({ run }: { run: TestRun }) {
  return (
    <article aria-label={`測試 ${run.id}`} className="space-y-3 rounded-xl border border-outline/20 p-3">
      <h4 className="text-sm font-bold">
        測試 {run.id}
        {run.comparison ? " · 僅切換後端 PII" : ""}
      </h4>
      <p className="text-xs text-on-surface/55">
        {run.mode === "chat" ? "一般對話" : "適用法規預測"}
      </p>
      {"prediction" in run.result ? (
        <div>
          <h5 className="text-sm font-semibold">預測的適用法條</h5>
          <p className="mt-2 whitespace-pre-wrap text-sm">{predictionText(run.result)}</p>
          {run.result.prediction.decision_reason && (
            <p className="mt-2 text-xs">{run.result.prediction.decision_reason}</p>
          )}
        </div>
      ) : (
        <details>
          <summary className="cursor-pointer text-sm">本次回覆</summary>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">
            {run.result.response.reply}
          </p>
        </details>
      )}
      {DIAGNOSTIC_SECTIONS.map(([key, label]) => (
        <details key={key} className="rounded-lg border border-outline/15 p-2">
          <summary className="cursor-pointer text-xs font-medium">{label}</summary>
          <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-[11px]">
            {JSON.stringify(run.result.diagnostics[key] ?? "未提供", null, 2)}
          </pre>
        </details>
      ))}
    </article>
  );
}
