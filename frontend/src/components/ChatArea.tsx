/**
 * ChatArea — 主要對話區域（Stitch 新版對話內頁 v0.1）
 * 包含頂部導航列、訊息列表（或 WelcomeHero）、打字動畫、底部輸入框。
 */
import { useRef, useEffect, useState} from "react";
import MaterialIcon from "./MaterialIcon";
import BrandMark from "./BrandMark";
import { useI18n } from "../i18n";
import type { ConversationMessage, ReplyContext } from "../hooks/useConversation";
import MessageItem from "./MessageItem";
import WelcomeHero from "./WelcomeHero";
import ChatInput from "./ChatInput";
import TypingIndicator from "./TypingIndicator";
import { checkHealth } from "../services/api";
import { hasCaseContext, type CaseContext, type FactUpdate, type ClarificationDraft } from "../services/caseFacts";
import type { StorageIssue } from "../hooks/conversationStorage";
import CaseSummary from "./CaseSummary";
import type { SendOutcome } from "../services/sendOutcome";

interface ChatAreaProps {
  messages: ConversationMessage[];
  isLoading: boolean;
  retryStatus?: string | null;
  onSend: (message: string, imageBase64?: string, imageUrl?: string, replyContext?: ReplyContext) => SendOutcome;
  onOpenSidebar: () => void;
  onStop: () => void;
  caseFacts?: CaseContext;
  pendingFacts?: FactUpdate[];
  onSaveCaseFacts?: (context: CaseContext, answerAgain?: boolean) => void;
  backendConnected?: boolean | null;
  contractVersion?: 1 | 2 | 3 | 4;
  onReconnect?: () => void;
  storageIssue?: StorageIssue;
  showEmotions?: boolean;
  incompatibleSummary?: boolean;
  summaryRequiresConnection?: boolean;
  canRegenerate?: boolean;
  allowImageUpload?: boolean;
  onClarificationDraftChange?: (messageId: string, draft: ClarificationDraft) => void;
}

export default function ChatArea({
  messages,
  isLoading,
  retryStatus,
  onSend,
  onOpenSidebar,
  onStop,
  caseFacts, onSaveCaseFacts, backendConnected, contractVersion, onReconnect, storageIssue, showEmotions = false, incompatibleSummary = false, summaryRequiresConnection = false, canRegenerate = false, allowImageUpload = true, onClarificationDraftChange,
}: ChatAreaProps) {
  const { t } = useI18n();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [isBackendConnected, setIsBackendConnected] = useState<boolean | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);

  // 自動滾動到最下方
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // 測試後端連線
  useEffect(() => {
    if (backendConnected !== undefined) return;
    const controller = new AbortController();
    checkHealth(controller.signal)
      .then(() => {
        if (!controller.signal.aborted) setIsBackendConnected(true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setIsBackendConnected(false);
      });
    return () => {
      controller.abort();
    };
  }, [backendConnected]);
  const connected = backendConnected === undefined ? isBackendConnected : backendConnected;
  const sendingDisabled = connected !== true || incompatibleSummary || summaryRequiresConnection;

  const hasMessages = messages.length > 0;
  const summaryMessageId = [...messages].reverse().find(message => message.role === "assistant" && !message.superseded && !message.isError && !message.isCancelled)?.id;
  const latestMessage = messages.at(-1);
  const replyPrompt =
    latestMessage?.role === "assistant" && !latestMessage.isError && !latestMessage.isCancelled && !latestMessage.isStreaming && !latestMessage.superseded
      ? latestMessage
      : undefined;

  return (
    <main className="flex-1 flex flex-col h-full min-h-0 relative bg-white lg:bg-transparent min-w-0">
      {/* 頂部導航列 */}
      <header className={`flex items-center justify-between border-b border-outline/10 bg-white shrink-0 ${hasMessages ? "px-3 lg:px-6 py-1" : "px-6 lg:px-10 py-6"}`}>
        <div className="flex items-center gap-4 min-w-0">
          {/* 漢堡按鈕（行動端） */}
          <button
            data-sidebar-trigger
            onClick={onOpenSidebar}
            className="flex h-11 w-11 items-center justify-center rounded-xl hover:bg-surface-container-high transition-colors cursor-pointer shrink-0"
            aria-label={t.openSidebar}
          >
            <MaterialIcon icon="menu" size={24} className="text-on-surface/70" />
          </button>
          {!summaryMessageId && caseFacts && onSaveCaseFacts && hasCaseContext(caseFacts) && <button data-summary-trigger type="button" aria-label={t.caseSummaryOpen} title={t.caseSummaryOpen} onClick={() => setSummaryOpen(true)} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-on-surface/60 hover:bg-primary/10"><MaterialIcon icon="description" size={17} /></button>}
          
          {!hasMessages && <div className="flex items-center gap-2.5 min-w-0">
            <BrandMark size={32} className="lg:hidden" />
            <div className="space-y-1 min-w-0">
              <h2 className="text-base lg:text-lg font-bold text-on-surface truncate">
                {t.appTitle}
              </h2>
              <div className="flex items-center gap-1.5 lg:gap-2 text-[10px] lg:text-xs text-on-surface/50 truncate">
                <span className="truncate">{t.appSubtitle}</span>
              </div>
            </div>
          </div>}
        </div>

        {/* 連線狀態指示 */}
        <div className="flex items-center gap-3 shrink-0 pl-2">
          <div
            className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1 rounded-full border transition-colors ${
              isLoading
                ? "text-orange-600 bg-orange-50 border-orange-100"
                : connected === false
                ? "text-red-600 bg-red-50 border-red-100"
                : "text-green-600 bg-green-50 border-green-100"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isLoading
                  ? "bg-orange-500 animate-pulse"
                  : connected === false
                  ? "bg-red-500"
                  : connected === null
                  ? "bg-gray-400 animate-pulse"
                  : "bg-green-500"
              }`}
            ></span>
            <span className="hidden sm:inline">
              {isLoading
                ? t.statusProcessing
                : connected === false
                ? t.statusConnectionFailed
                : connected === null
                ? t.statusConnecting
                : t.statusConnected}
            </span>
          </div>
          {connected === false && onReconnect && <button type="button" onClick={onReconnect} className="rounded-lg border border-outline/20 px-3 py-1 text-xs text-primary hover:bg-primary/5">{t.reconnect}</button>}
        </div>
      </header>
      {storageIssue && <p role="alert" className="border-b border-error/20 bg-error-container/20 px-6 py-3 text-xs text-error">{storageIssue === "migration" ? t.storageMigration : storageIssue === "future" ? t.storageFuture : storageIssue === "invalid" ? t.storageInvalid : t.storageUnavailable}</p>}
      {incompatibleSummary && <p role="alert" className="border-b border-outline/20 bg-surface-container-low px-6 py-3 text-xs">{t.incompatibleSummary}</p>}
      {summaryRequiresConnection && <p role="status" className="border-b border-outline/20 bg-surface-container-low px-6 py-3 text-xs">{connected === null ? t.summaryCheckingConnection : t.summaryConnectionFailed}</p>}
      {summaryOpen && caseFacts && onSaveCaseFacts && <CaseSummary context={caseFacts} onSave={onSaveCaseFacts} onStartEdit={onStop} onClose={() => setSummaryOpen(false)} canRegenerate={canRegenerate} />}

      {/* 訊息區域 */}
      <section className="min-h-0 flex-1 overflow-y-auto chat-scrollbar hero-mesh-gradient flex flex-col">
        {hasMessages ? (
          <div className="w-full px-3 sm:px-6 lg:px-10 py-6 space-y-8">
            {messages.map((msg) => (
              <MessageItem key={msg.id} message={msg} showEmotions={showEmotions} streamingStatus={msg.isStreaming ? retryStatus : undefined} onOpenSummary={caseFacts && onSaveCaseFacts && msg.id === summaryMessageId ? () => setSummaryOpen(true) : undefined} />
            ))}
            {isLoading && !latestMessage?.isStreaming && <TypingIndicator message={retryStatus} />}
            <div ref={messagesEndRef} className="h-4" />
          </div>
        ) : (
          <WelcomeHero onSuggest={onSend} disabled={sendingDisabled} allowImageUpload={allowImageUpload} />
        )}
      </section>

      {/* 底部輸入框（僅在有訊息時顯示，WelcomeHero 有自己的輸入框） */}
      {hasMessages && (
        <ChatInput
          onSend={onSend}
          isLoading={isLoading}
          replyPrompt={replyPrompt}
          streamingPrompt={latestMessage?.role === "assistant" && latestMessage.isStreaming
            && !latestMessage.isError && !latestMessage.isCancelled ? latestMessage : undefined}
          onStop={onStop}
          caseRevision={caseFacts?.revision}
          activeContractVersion={connected === true ? contractVersion : undefined}
          disabled={sendingDisabled}
          allowImageUpload={allowImageUpload}
          onClarificationDraftChange={onClarificationDraftChange}
        />
      )}
    </main>
  );
}
