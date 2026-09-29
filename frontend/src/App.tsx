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

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
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
    caseFacts, pendingFacts, saveCaseFacts, contractV2, isBackendConnected, storageIssue, incompatibleSummary, canRegenerate,
  } = useConversation();

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
        sessions={sessions}
        currentSessionId={currentSessionId}
        isOpen={sidebarOpen}
        isCollapsed={sidebarCollapsed}
        onClose={() => setSidebarOpen(false)}
        onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
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
          caseFacts={contractV2 ? caseFacts : undefined}
          pendingFacts={pendingFacts}
          onSaveCaseFacts={saveCaseFacts}
          backendConnected={isBackendConnected}
          storageIssue={storageIssue}
          showEmotions={showEmotions}
          incompatibleSummary={incompatibleSummary}
          canRegenerate={canRegenerate}
        onSend={sendMessage}
        onOpenSidebar={() => setSidebarOpen(true)}
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
      />
    </div>
  );
}
