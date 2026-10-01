/**
 * App — 性騷擾防治智能 AI 主應用程式
 * 整合 Sidebar + ChatArea + EmergencyFab + SettingsPanel，
 * 使用 useConversation hook 管理所有狀態。
 */
import { useState, useCallback } from "react";
import { useConversation } from "./hooks/useConversation";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import SettingsPanel from "./components/SettingsPanel";
import AdminPanel from "./components/AdminPanel";
import { useEmotionPreference } from "./hooks/useEmotionPreference";
import PrivacyReviewDialog from "./components/PrivacyReviewDialog";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarLayout, setSidebarLayout] = useState({ scope: "", collapsed: false });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
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
    caseFacts, pendingFacts, saveCaseFacts, contractV2, contractVersion, clientSettings, saveClarificationDraft, isBackendConnected, reconnectBackend, storageIssue, incompatibleSummary, summaryRequiresConnection, canRegenerate,
  } = useConversation();

  // A first message or a different conversation starts with an unobstructed
  // reading area. Streaming updates keep the user's explicit pin choice.
  const layoutScope = `${currentSessionId}:${messages.length > 0}`;
  const sidebarCollapsed = sidebarLayout.scope === layoutScope ? sidebarLayout.collapsed : messages.length > 0;

  const handleOpenSettings = useCallback(() => {
    setSettingsOpen(true);
    setSidebarOpen(false);
  }, []);

  const handleOpenAdmin = useCallback(() => {
    setAdminOpen(true);
    setSidebarOpen(false);
  }, []);

  return (
    <div className="flex w-full h-dvh bg-background overflow-hidden">
      {/* 側邊欄 */}
      <Sidebar
        key={layoutScope}
        sessions={sessions}
        currentSessionId={currentSessionId}
        isOpen={sidebarOpen}
        isCollapsed={sidebarCollapsed}
        onClose={() => setSidebarOpen(false)}
        onToggleCollapsed={() => setSidebarLayout({ scope: layoutScope, collapsed: !sidebarCollapsed })}
        onSelectSession={setCurrentSessionId}
        onNewSession={createNewSession}
        onDeleteSession={deleteSession}
        onRenameSession={renameSession}
        onOpenSettings={handleOpenSettings}
        onOpenAdmin={handleOpenAdmin}
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
          incompatibleSummary={incompatibleSummary}
          summaryRequiresConnection={summaryRequiresConnection}
          canRegenerate={canRegenerate}
          allowImageUpload={clientSettings?.enable_image_upload ?? true}
        onSend={sendMessage}
        onOpenSidebar={() => {
          if (window.matchMedia("(min-width: 1024px) and (pointer: fine)").matches) {
            setSidebarLayout({ scope: layoutScope, collapsed: !sidebarCollapsed });
          } else setSidebarOpen(true);
        }}
      />



      {/* 設定面板 */}
      <SettingsPanel
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        sessions={sessions}
        onClearAll={clearAllSessions}
        showEmotions={showEmotions}
        onShowEmotionsChange={updateShowEmotions}
        preferenceSaveFailed={preferenceSaveFailed}
      />

      <AdminPanel
        isOpen={adminOpen}
        onClose={() => setAdminOpen(false)}
        onRuntimeConfigChanged={() => { void reconnectBackend(); }}
      />
      <PrivacyReviewDialog />
    </div>
  );
}
