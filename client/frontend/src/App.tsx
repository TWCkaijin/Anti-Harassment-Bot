/**
 * App — 性騷擾防治智能 AI 主應用程式
 * 整合 Sidebar + ChatArea + EmergencyFab + SettingsPanel，
 * 使用 useConversation hook 管理所有狀態。
 */
import { useState, useCallback, useEffect } from "react";
import { useConversation } from "./hooks/useConversation";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import SettingsPanel from "./components/SettingsPanel";
import PrivacyReviewDialog from "./components/PrivacyReviewDialog";
import { useEmotionPreference } from "./hooks/useEmotionPreference";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 1024px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => {
      setDesktop(media.matches);
      setSidebarOpen(false);
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
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

  const handleOpenSettings = useCallback(() => {
    setSettingsOpen(true);
    setSidebarOpen(false);
  }, []);


  return (
    <div className="flex w-full h-dvh bg-background overflow-hidden">
      {/* 側邊欄 */}
      <Sidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        isOpen={sidebarOpen}
        isDesktop={desktop}
        isCollapsed={sidebarCollapsed}
        onClose={() => setSidebarOpen(false)}
        onCollapse={() => setSidebarCollapsed(true)}
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
        sidebarExpanded={desktop ? !sidebarCollapsed : sidebarOpen}
        sidebarModalOpen={!desktop && sidebarOpen}
        onOpenSidebar={() => {
          if (desktop) setSidebarCollapsed(collapsed => !collapsed);
          else setSidebarOpen(true);
        }}
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
