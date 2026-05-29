/**
 * Sidebar — Stitch 風格暖色系側邊欄 (v0.1)
 * 導航式設計：品牌標識 → 滿版橘色新增對話按鈕 → 對話歷史清單 (卡片式) → 底部工具列。
 * RWD：桌面端固定左側，行動端可透過漢堡按鈕展開覆蓋。
 */
import { useState } from "react";
import MaterialIcon from "./MaterialIcon";
import { useI18n } from "../i18n";
import type { ConversationSession } from "../hooks/useConversation";

interface SidebarProps {
  sessions: ConversationSession[];
  currentSessionId: string;
  isOpen: boolean;
  onClose: () => void;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, newTitle: string) => void;
  onOpenSettings: () => void;
}

function formatDate(timestamp: number, t: ReturnType<typeof useI18n>["t"]): string {
  const d = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  const diffHour = Math.floor(diffMs / 3_600_000);
  const diffDay = Math.floor(diffMs / 86_400_000);

  if (diffMin < 1) return t.timeJustNow;
  if (diffMin < 60) return t.timeMinAgo(diffMin);
  if (diffHour < 24) return t.timeHourAgo(diffHour);
  if (diffDay < 7) return t.timeDayAgo(diffDay);
  return d.toLocaleDateString("zh-TW", { month: "short", day: "numeric" });
}

function getSessionPreview(session: ConversationSession, t: ReturnType<typeof useI18n>["t"]): string {
  const lastUserMsg = [...session.messages]
    .reverse()
    .find((m) => m.role === "user");
  if (lastUserMsg) {
    return lastUserMsg.content.length > 28
      ? lastUserMsg.content.slice(0, 28) + "…"
      : lastUserMsg.content;
  }
  return session.title || t.newConversation;
}

export default function Sidebar({
  sessions,
  currentSessionId,
  isOpen,
  onClose,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onRenameSession,
  onOpenSettings,
}: SidebarProps) {
  const { t } = useI18n();
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  // 只顯示有訊息的對話，由新到舊排序
  const sortedSessions = [...sessions]
    .filter((s) => s.messages.length > 0)
    .sort((a, b) => b.createdAt - a.createdAt);

  const handleExport = (session: ConversationSession) => {
    // 建立只包含有意義的文字紀錄的簡單匯出版本，或 JSON
    const exportData = session.messages.map(m => `[${m.role === 'user' ? 'User' : 'AI'}]: ${m.content}`).join('\n\n');
    const blob = new Blob([exportData], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat-${session.title || session.id.slice(0, 8)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    setMenuOpenId(null);
  };

  const handleRenameSubmit = (id: string) => {
    if (editingTitle.trim()) {
      onRenameSession(id, editingTitle.trim());
    }
    setEditingId(null);
  };

  return (
    <>
      {/* 行動端遮罩 */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/30 z-40 lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={`
          fixed lg:static inset-y-0 left-0 z-50
          w-[320px] flex flex-col bg-white
          border-r border-outline/20 p-6 gap-6
          transform transition-transform duration-300 ease-in-out
          ${isOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"}
        `}
      >
        {/* 頂部 Logo */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-primary flex items-center justify-center shadow-sm">
              <MaterialIcon icon="shield" size={24} filled className="text-white" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-primary leading-tight">{t.brandName}</h1>
              <p className="text-xs text-on-surface/60">{t.brandSub}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="lg:hidden p-1.5 rounded-lg hover:bg-surface-container transition-colors"
            aria-label={t.close}
          >
            <MaterialIcon icon="close" size={20} className="text-on-surface/60" />
          </button>
        </div>

        {/* 新增對話按鈕 */}
        <button
          onClick={() => {
            onNewSession();
            onClose();
          }}
          className="w-full py-4 px-4 bg-primary text-white font-bold rounded-2xl shadow-sm hover:opacity-90 transition-all flex items-center justify-center gap-2 cursor-pointer active:scale-[0.98]"
        >
          <MaterialIcon icon="add" size={20} />
          <span>{t.newChat}</span>
        </button>

        {/* 對話歷史列表 */}
        <div className="flex-1 overflow-y-auto space-y-3 -mx-2 px-2 chat-scrollbar">
          {sortedSessions.length === 0 && (
            <p className="text-sm text-on-surface/50 text-center py-8">
              {t.noHistory}
            </p>
          )}
          {sortedSessions.map((session) => {
            const isActive = session.id === currentSessionId;
            return (
              <div
                key={session.id}
                className={`
                  group p-4 rounded-2xl cursor-pointer transition-all duration-200 border relative
                  ${
                    isActive
                      ? "bg-primary-container border-primary/20"
                      : "bg-surface hover:bg-surface-container border-transparent"
                  }
                `}
                onClick={() => {
                  if (editingId !== session.id) {
                    onSelectSession(session.id);
                    onClose();
                  }
                }}
              >
                <div className="flex gap-3 pr-8">
                  <MaterialIcon
                    icon="chat_bubble"
                    size={20}
                    className={`shrink-0 ${
                      isActive ? "text-primary" : "text-on-surface/50"
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    {editingId === session.id ? (
                      <input
                        type="text"
                        autoFocus
                        value={editingTitle}
                        onChange={(e) => setEditingTitle(e.target.value)}
                        onBlur={() => handleRenameSubmit(session.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRenameSubmit(session.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        className="w-full text-sm font-medium bg-transparent border-b border-primary outline-none text-on-surface"
                      />
                    ) : (
                      <p
                        className={`text-sm truncate font-medium ${
                          isActive
                            ? "text-primary"
                            : "text-on-surface"
                        }`}
                      >
                        {session.title || getSessionPreview(session, t)}
                      </p>
                    )}
                    <p className={`text-[11px] mt-1 ${isActive ? "text-primary/60" : "text-on-surface/40"}`}>
                      {formatDate(session.createdAt, t)}
                      {session.messages.length > 0 &&
                        ` · ${t.messagesCount(session.messages.length)}`}
                    </p>
                  </div>
                </div>
                
                {/* 更多功能選單按鈕 */}
                <div className="absolute right-2 top-1/2 -translate-y-1/2 opacity-100 lg:opacity-0 lg:group-hover:opacity-100 transition-all">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuOpenId(menuOpenId === session.id ? null : session.id);
                    }}
                    className="p-1.5 rounded-lg hover:bg-surface-container-high text-on-surface/40 hover:text-on-surface"
                  >
                    <MaterialIcon icon="more_vert" size={16} />
                  </button>

                  {/* 選單列表 */}
                  {menuOpenId === session.id && (
                    <>
                      <div className="fixed inset-0 z-40" onClick={(e) => { e.stopPropagation(); setMenuOpenId(null); }} />
                      <div className="absolute right-0 top-full mt-1 w-32 bg-white rounded-xl shadow-lg border border-outline/10 py-1.5 z-50 flex flex-col text-sm">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingTitle(session.title || getSessionPreview(session, t));
                            setEditingId(session.id);
                            setMenuOpenId(null);
                          }}
                          className="px-4 py-2 text-left hover:bg-surface-container flex items-center gap-2 text-on-surface"
                        >
                          <MaterialIcon icon="edit" size={14} />
                          重新命名
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleExport(session);
                          }}
                          className="px-4 py-2 text-left hover:bg-surface-container flex items-center gap-2 text-on-surface"
                        >
                          <MaterialIcon icon="download" size={14} />
                          匯出紀錄
                        </button>
                        <div className="h-px bg-outline/10 my-1"></div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteSession(session.id);
                            setMenuOpenId(null);
                          }}
                          className="px-4 py-2 text-left hover:bg-error-container/50 text-error flex items-center gap-2"
                        >
                          <MaterialIcon icon="delete" size={14} />
                          {t.deleteChat}
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* 底部工具列 */}
        <div className="mt-auto space-y-6">
          {/* 隱私提示 */}
          <div className="text-center space-y-2">
            <p className="text-[11px] text-on-surface/40 flex items-center justify-center gap-1">
              <MaterialIcon icon="lock" size={14} />
              {t.privacyNote}
            </p>
            <p className="text-[11px] text-on-surface/40">
              {t.privacyNoteSub}
            </p>
          </div>

          {/* 緊急求助選單 */}
          <div className="relative group w-full">
            <button
              className="w-full py-4 px-4 bg-secondary text-white rounded-full font-bold flex items-center justify-center gap-2 shadow-md hover:opacity-90 transition-opacity cursor-pointer"
            >
              <MaterialIcon icon="call" size={20} />
              <span>專人緊急協助</span>
              <MaterialIcon icon="expand_less" size={20} className="ml-auto group-hover:rotate-180 transition-transform" />
            </button>
            {/* 展開選單 (往上展開，避免超出畫面) */}
            <div className="absolute left-0 bottom-full mb-2 w-full bg-white border border-outline/20 rounded-2xl shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all flex flex-col overflow-hidden z-50 origin-bottom">
              <a href="tel:113" className="px-4 py-3 hover:bg-surface-container flex items-center gap-3 text-on-surface">
                <MaterialIcon icon="local_police" size={20} className="text-secondary" />
                <div className="flex flex-col text-left">
                  <span className="font-bold">113 保護專線</span>
                  <span className="text-[10px] text-on-surface/60">24小時保護專線</span>
                </div>
              </a>
              <a href="tel:110" className="px-4 py-3 hover:bg-surface-container flex items-center gap-3 text-on-surface border-t border-outline/10">
                <MaterialIcon icon="local_police" size={20} className="text-secondary" />
                <div className="flex flex-col text-left">
                  <span className="font-bold">110 報案專線</span>
                  <span className="text-[10px] text-on-surface/60">緊急報案</span>
                </div>
              </a>
              <a href="tel:02-2391-7133" className="px-4 py-3 hover:bg-surface-container flex items-center gap-3 text-on-surface border-t border-outline/10">
                <MaterialIcon icon="support_agent" size={20} className="text-secondary" />
                <div className="flex flex-col text-left">
                  <span className="font-bold">現代婦女基金會</span>
                  <span className="text-[10px] text-on-surface/60">性別友善諮詢</span>
                </div>
              </a>
            </div>
          </div>

          {/* 工具連結列 */}
          <div className="grid grid-cols-2 gap-4 border-t border-outline/10 pt-6">
            <a
              href="tel:113"
              className="flex flex-col items-center gap-1 text-on-surface/70 hover:text-primary transition-colors cursor-pointer"
            >
              <MaterialIcon icon="psychology" size={20} />
              <span className="text-xs font-medium">
                {t.counseling}
              </span>
            </a>
            <button
              onClick={onOpenSettings}
              className="flex flex-col items-center gap-1 text-on-surface/70 hover:text-primary transition-colors cursor-pointer"
            >
              <MaterialIcon icon="settings" size={20} />
              <span className="text-xs font-medium">
                {t.settings}
              </span>
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
