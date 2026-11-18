/**
 * App — 性騷擾防治智能 AI 主應用程式
 * 整合 Sidebar + ChatArea + EmergencyFab + SettingsPanel，
 * 使用 useConversation hook 管理所有狀態。
 */
import { useState, useCallback, useEffect, useRef } from "react";
import { useConversation } from "./hooks/useConversation";
import Sidebar from "./components/Sidebar";
import MaterialIcon from "./components/MaterialIcon";
import { useI18n } from "./i18n";
import ChatArea from "./components/ChatArea";
import SettingsPanel from "./components/SettingsPanel";
import PrivacyReviewDialog from "./components/PrivacyReviewDialog";
import { useEmotionPreference } from "./hooks/useEmotionPreference";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { t } = useI18n();
  const [sidebarPinned, setSidebarPinned] = useState(false);
  const [sidebarPeek, setSidebarPeek] = useState(false);
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
  } = useConversation();

  const closeSidebar = useCallback(() => {
    window.clearTimeout(peekTimer.current);
    setSidebarPeek(false);
    setSidebarOpen(false);
  }, []);
  const unpinSidebar = () => {
    closeSidebar();
    setSidebarPinned(false);
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
    <div className="flex w-full h-dvh bg-background overflow-hidden">
      {desktop && (
        <div className="relative z-[55] flex w-14 shrink-0 justify-center border-r border-neutral-300 bg-neutral-50 pt-2.5">
          <button
            type="button"
            data-sidebar-trigger
            aria-label={sidebarPinned ? t.unpinSidebar : t.pinSidebar}
            aria-expanded={sidebarPinned || sidebarPeek}
            aria-controls="conversation-sidebar"
            aria-pressed={sidebarPinned}
            title={sidebarPinned ? t.unpinSidebar : t.pinSidebar}
            onPointerEnter={event => beginPeek(event.pointerType)}
            onPointerLeave={endPeek}
            onClick={event => {
              if (event.detail > 1) return;
              if (sidebarPinned) unpinSidebar();
              else {
                closeSidebar();
                setSidebarPinned(true);
              }
            }}
            onDoubleClick={unpinSidebar}
            className={`flex h-11 w-11 items-center justify-center rounded-xl border text-on-surface/75 transition-colors hover:bg-neutral-200 focus-visible:outline-2 focus-visible:outline-primary ${sidebarPinned ? "border-neutral-400 bg-neutral-200" : "border-neutral-300 bg-white"}`}
          >
            <MaterialIcon icon={sidebarPinned ? "keep" : "side_navigation"} size={22} />
          </button>
        </div>
      )}
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
        showSidebarTrigger={!desktop}
        onOpenSidebar={() => setSidebarOpen(true)}
      />



      {/* 設定面板 */}
      <SettingsPanel
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        sessions={sessions}
        onClearAll={clearAllSessions}
        storageChanged={storageIssue === "changed"}
        showEmotions={showEmotions}
        onShowEmotionsChange={updateShowEmotions}
        preferenceSaveFailed={preferenceSaveFailed}
      />

      {privacyReview && <PrivacyReviewDialog initialDraft={privacyReview} onConfirm={confirmPrivacyReview} onCancel={cancelPrivacyReview} />}


    </div>
  );
}
