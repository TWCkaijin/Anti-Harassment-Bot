/**
 * App — 性騷擾防治智能 AI 主應用程式
 * 整合 Sidebar + ChatArea + EmergencyFab + SettingsPanel，
 * 使用 useConversation hook 管理所有狀態。
 */
import { useState, useCallback, useEffect, useRef } from "react";
import { useConversation } from "./hooks/useConversation";
import Sidebar from "./components/Sidebar";
import { MaterialIcon } from "@rag/shared/chat-ui";
import { useI18n } from "@rag/shared/chat-ui";
import ChatArea from "./components/ChatArea";
import SettingsPanel from "./components/SettingsPanel";
import PrivacyReviewDialog from "./components/PrivacyReviewDialog";
import { useEmotionPreference } from "./hooks/useEmotionPreference";
import { useTextSizePreference } from "./hooks/useTextSizePreference";
import { usePrivacyReviewPreference } from "./hooks/usePrivacyReviewPreference";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { t } = useI18n();
  const [sidebarPinned, setSidebarPinned] = useState(false);
  const [sidebarPeek, setSidebarPeek] = useState(false);
  const desktopTriggerRef = useRef<HTMLButtonElement>(null);
  const peekTimer = useRef<number | undefined>(undefined);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 1024px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => {
      setDesktop(media.matches);
      setSidebarOpen(false);
      window.clearTimeout(peekTimer.current);
      setSidebarPeek(false);
    };
    media.addEventListener("change", update);
    return () => {
      media.removeEventListener("change", update);
      window.clearTimeout(peekTimer.current);
    };
  }, []);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { showEmotions, updateShowEmotions, preferenceSaveFailed } = useEmotionPreference();
  const { textSize, updateTextSize, textSizeSaveFailed } = useTextSizePreference();
  const { confirmPrivacyBeforeSending, updateConfirmPrivacy, privacyReviewSaveFailed } = usePrivacyReviewPreference();

  const {
    sessions,
    currentSessionId,
    messages,
    isLoading,
    retryStatus,
    stopCurrentResponse,
    sendMessage,
    createNewSession,
    setCurrentSessionId,
    deleteSession,
    renameSession,
    clearAllSessions,
    caseFacts, pendingFacts, saveCaseFacts, contractV2, contractVersion, clientSettings, saveClarificationDraft, isBackendConnected, reconnectBackend, storageIssue, incompatibleSummary, summaryRequiresConnection, canRegenerate, privacyReview, confirmPrivacyReview, cancelPrivacyReview,
  } = useConversation(undefined, confirmPrivacyBeforeSending);

  const closeSidebar = useCallback(() => {
    window.clearTimeout(peekTimer.current);
    setSidebarPeek(false);
    setSidebarOpen(false);
  }, []);
  const unpinSidebar = () => {
    closeSidebar();
    setSidebarPinned(false);
    desktopTriggerRef.current?.focus();
  };
  const beginPeek = (pointerType: string) => {
    if (pointerType !== "mouse") return;
    window.clearTimeout(peekTimer.current);
    if (!sidebarPinned) setSidebarPeek(true);
  };
  const endPeek = () => {
    window.clearTimeout(peekTimer.current);
    peekTimer.current = window.setTimeout(() => {
      if (!document.querySelector("#conversation-sidebar :focus-visible")) setSidebarPeek(false);
    }, 160);
  };
  const handleOpenSettings = useCallback(() => {
    closeSidebar();
    setSettingsOpen(true);
  }, [closeSidebar]);

  return (
    <div className="conversation-layout flex h-dvh w-full overflow-hidden bg-background" data-sidebar-pinned={desktop && sidebarPinned}>
      {/* 側邊欄 */}
      <Sidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        isOpen={sidebarOpen}
        isDesktop={desktop}
        isCollapsed={!sidebarPinned}
        isPeeking={desktop && sidebarPeek}
        onPeekEnter={beginPeek}
        onPeekLeave={endPeek}
        onClose={closeSidebar}
        onCollapse={unpinSidebar}
        onSelectSession={setCurrentSessionId}
        onNewSession={createNewSession}
        onDeleteSession={deleteSession}
        onRenameSession={renameSession}
        onOpenSettings={handleOpenSettings}
      />

      {/* 主要對話區 */}
      <ChatArea
          key={currentSessionId}
          messages={messages}
          isLoading={isLoading}
          retryStatus={retryStatus}
          onStop={stopCurrentResponse}
          caseFacts={contractV2 || caseFacts.schema_version === 3 ? caseFacts : undefined}
          pendingFacts={pendingFacts}
          onSaveCaseFacts={saveCaseFacts}
          onClarificationDraftChange={saveClarificationDraft}
          backendConnected={isBackendConnected}
          contractVersion={contractVersion}
          onReconnect={reconnectBackend}
          storageIssue={storageIssue}
          showEmotions={showEmotions}
          showReasoning={clientSettings?.enable_reasoning_display === true}
          incompatibleSummary={incompatibleSummary}
          summaryRequiresConnection={summaryRequiresConnection}
          canRegenerate={canRegenerate}
          allowImageUpload={false}
        onSend={sendMessage}
        sidebarExpanded={desktop ? sidebarPinned : sidebarOpen}
        sidebarModalOpen={!desktop && sidebarOpen}
        desktopSidebarTrigger={desktop ? (
          <button
            ref={desktopTriggerRef}
            type="button"
            data-sidebar-trigger
            data-pinned={sidebarPinned}
            aria-label={sidebarPinned ? t.unpinSidebar : t.pinSidebar}
            aria-expanded={sidebarPinned || sidebarPeek}
            aria-controls="conversation-sidebar"
            aria-pressed={sidebarPinned}
            title={sidebarPinned ? t.unpinSidebar : t.pinSidebar}
            onPointerEnter={event => beginPeek(event.pointerType)}
            onPointerLeave={endPeek}
            onKeyDown={event => {
              if (event.key === "Escape" && (sidebarPinned || sidebarPeek)) {
                event.preventDefault();
                unpinSidebar();
              }
            }}
            onClick={event => {
              if (event.detail > 1) return;
              if (sidebarPinned) {
                unpinSidebar();
              } else {
                closeSidebar();
                setSidebarPinned(true);
                if (event.detail === 0) {
                  requestAnimationFrame(() => document.querySelector<HTMLButtonElement>("#conversation-sidebar [data-sidebar-new]")?.focus());
                }
              }
            }}
            onDoubleClick={unpinSidebar}
            className="sidebar-trigger"
          >
            <MaterialIcon icon={sidebarPinned ? "keep" : "side_navigation"} size={21} />
          </button>
        ) : undefined}
        onOpenSidebar={() => setSidebarOpen(true)}
      />



      {/* 設定面板 */}
      <SettingsPanel
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        sessions={sessions}
        onClearAll={clearAllSessions}
        storageChanged={storageIssue === "changed"}
        textSize={textSize}
        onTextSizeChange={updateTextSize}
        textSizeSaveFailed={textSizeSaveFailed}
        showEmotions={showEmotions}
        onShowEmotionsChange={updateShowEmotions}
        preferenceSaveFailed={preferenceSaveFailed}
        confirmPrivacyBeforeSending={confirmPrivacyBeforeSending}
        onConfirmPrivacyChange={updateConfirmPrivacy}
        privacyReviewSaveFailed={privacyReviewSaveFailed}
      />

      {privacyReview && <PrivacyReviewDialog initialDraft={privacyReview} onConfirm={confirmPrivacyReview} onCancel={cancelPrivacyReview} />}


    </div>
  );
}
