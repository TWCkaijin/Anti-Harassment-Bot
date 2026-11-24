import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MaterialIcon } from "@rag/shared/chat-ui";
import { useI18n } from "@rag/shared/chat-ui";
import type { ConversationSession } from "../hooks/useConversation";
import { formatConversationText } from "../services/conversationExport";
import { hasCaseContext } from "../services/caseFacts";

interface SidebarProps {
  sessions: ConversationSession[];
  currentSessionId: string;
  isOpen: boolean;
  isDesktop: boolean;
  isCollapsed: boolean;
  isPeeking?: boolean;
  onPeekEnter?: (pointerType: string) => void;
  onPeekLeave?: () => void;
  onClose: () => void;
  onCollapse: () => void;
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
  isDesktop,
  isCollapsed,
  isPeeking = false,
  onPeekEnter,
  onPeekLeave,
  onClose,
  onCollapse,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onRenameSession,
  onOpenSettings,
}: SidebarProps) {
  const { t } = useI18n();
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState<{ top: number; right: number } | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isEmergencyMenuOpen, setIsEmergencyMenuOpen] = useState(false);
  const asideRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const emergencyMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (menuOpenId) menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus(); }, [menuOpenId]);
  useEffect(() => {
    if (isDesktop || !isOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    asideRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      const target = previous?.isConnected && previous !== document.body ? previous : document.querySelector<HTMLButtonElement>("[data-sidebar-trigger]");
      target?.focus();
    };
  }, [isOpen, isDesktop]);
  const visible = isDesktop ? !isCollapsed || isPeeking || Boolean(menuOpenId || editingId) : isOpen;
  const mode = isDesktop ? isCollapsed ? "preview" : "pinned" : "drawer";
  const close = onClose;

  useEffect(() => {
    if (!isEmergencyMenuOpen) return;

    const closeOnOutsidePointerDown = (event: PointerEvent) => {
      if (!emergencyMenuRef.current?.contains(event.target as Node)) {
        setIsEmergencyMenuOpen(false);
      }
    };
    window.addEventListener("pointerdown", closeOnOutsidePointerDown);
    return () => window.removeEventListener("pointerdown", closeOnOutsidePointerDown);
  }, [isEmergencyMenuOpen]);

  // 只顯示有訊息的對話，由新到舊排序
  const sortedSessions = [...sessions]
    .filter((s) => s.messages.length > 0 || (s.caseFacts && hasCaseContext(s.caseFacts)))
    .sort((a, b) => b.createdAt - a.createdAt);
  const openMenuSession = sortedSessions.find((session) => session.id === menuOpenId);

  const handleExport = (session: ConversationSession) => {
    const exportData = formatConversationText(session, t);
    const blob = new Blob([exportData], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat-${session.title || session.id.slice(0, 8)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    setMenuOpenId(null);
    setMenuPosition(null);
    menuTriggerRef.current?.focus();
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
      {!isDesktop && isOpen && (
        <div
          aria-hidden="true"
          className="sidebar-scrim"
          onClick={close}
        />
      )}

      <aside
        ref={asideRef}
        id="conversation-sidebar"
        aria-labelledby="conversation-sidebar-title"
        data-sidebar-mode={mode}
        data-sidebar-open={visible}
        aria-modal={!isDesktop && isOpen && !menuOpenId ? true : undefined}
        role={!isDesktop && isOpen ? "dialog" : "complementary"}
        inert={!visible}
        onPointerEnter={event => { if (isDesktop) onPeekEnter?.(event.pointerType); }}
        onPointerLeave={() => { if (isDesktop) onPeekLeave?.(); }}
        onBlur={event => {
          if (isDesktop && !event.currentTarget.contains(event.relatedTarget)) onPeekLeave?.();
        }}
        onKeyDown={event => {
          if (event.key === "Escape") { event.stopPropagation(); setMenuOpenId(null); setEditingId(null); setIsEmergencyMenuOpen(false); if (isDesktop) { onCollapse(); document.querySelector<HTMLButtonElement>("[data-sidebar-trigger]")?.focus(); } else close(); }
          if (isDesktop || event.key !== "Tab") return;
          const controls = Array.from(asideRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, a[href], [tabindex="0"]') ?? []).filter(element => element.getClientRects().length > 0 && getComputedStyle(element).visibility === "visible");
          const first = controls[0], last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }}
        className="conversation-sidebar"
      >
        <div className="sidebar-heading">
          <h2 id="conversation-sidebar-title">{t.conversationSidebar}</h2>
          {!isDesktop && (
            <button type="button" className="sidebar-icon-button" aria-label={t.close} onClick={close}>
              <MaterialIcon icon="close" size={19} />
            </button>
          )}
        </div>

        <button type="button" data-sidebar-new className="sidebar-new" onClick={() => {
          onNewSession();
          close();
        }}>
          <MaterialIcon icon="add" size={18} />
          {t.newChat}
        </button>

        <div className="sidebar-history chat-scrollbar">
          {sortedSessions.length === 0 ? <p className="sidebar-empty">{t.noHistory}</p> : (
            <ul className="space-y-1">
              {sortedSessions.map(session => {
                const title = session.title || getSessionPreview(session, t);
                const isActive = session.id === currentSessionId;
                return (
                  <li key={session.id} className="sidebar-row" data-active={isActive}>
                    {editingId === session.id ? (
                      <input
                        type="text"
                        autoFocus
                        aria-label={t.renameChat}
                        value={editingTitle}
                        onChange={event => setEditingTitle(event.target.value)}
                        onBlur={() => handleRenameSubmit(session.id)}
                        onKeyDown={event => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            event.currentTarget.blur();
                            menuTriggerRef.current?.focus();
                          }
                          if (event.key === "Escape") {
                            event.stopPropagation();
                            setEditingId(null);
                            requestAnimationFrame(() => menuTriggerRef.current?.focus());
                          }
                        }}
                        className="sidebar-rename"
                      />
                    ) : (
                      <button
                        type="button"
                        className="sidebar-conversation"
                        aria-label={title}
                        title={title}
                        aria-current={isActive ? "page" : undefined}
                        onClick={() => {
                          onSelectSession(session.id);
                          setIsEmergencyMenuOpen(false);
                          close();
                        }}
                      >
                        <MaterialIcon icon="chat_bubble" size={17} className="sidebar-conversation-icon" />
                        <div className="min-w-0">
                          <p className="truncate font-medium">{title}</p>
                          <p className="sidebar-row-meta">
                            {formatDate(session.createdAt, t)}
                            {session.messages.length > 0 && ` · ${t.messagesCount(session.messages.length)}`}
                          </p>
                        </div>
                      </button>
                    )}
                    <button
                      type="button"
                      className="sidebar-icon-button sidebar-row-menu"
                      aria-label={`${t.conversationMenu}：${title}`}
                      aria-haspopup="dialog"
                      aria-expanded={menuOpenId === session.id}
                      onClick={event => {
                        if (menuOpenId === session.id) {
                          setMenuOpenId(null);
                          setMenuPosition(null);
                          return;
                        }
                        const rect = event.currentTarget.getBoundingClientRect();
                        menuTriggerRef.current = event.currentTarget;
                        setMenuOpenId(session.id);
                        setMenuPosition({
                          top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 164)),
                          right: window.innerWidth - rect.right,
                        });
                      }}
                    >
                      <MaterialIcon icon="more_vert" size={17} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="sidebar-tools">
          <div ref={emergencyMenuRef} className="relative">
            <button
              type="button"
              aria-expanded={isEmergencyMenuOpen}
              aria-controls="sidebar-emergency"
              onClick={() => setIsEmergencyMenuOpen(open => !open)}
              className="sidebar-tool sidebar-emergency"
            >
              <MaterialIcon icon="call" size={18} />
              <span>{t.emergencyHelp}</span>
              <MaterialIcon icon={isEmergencyMenuOpen ? "expand_less" : "expand_more"} size={16} className="ml-auto" />
            </button>
            <div id="sidebar-emergency" hidden={!isEmergencyMenuOpen} className="sidebar-emergency-menu">
              <a href="tel:113" className="sidebar-tool" onClick={() => setIsEmergencyMenuOpen(false)}>
                <MaterialIcon icon="local_police" size={18} />
                <span><strong className="block">113 保護專線</strong><span className="sidebar-row-meta">24 小時保護專線</span></span>
              </a>
              <a href="tel:110" className="sidebar-tool" onClick={() => setIsEmergencyMenuOpen(false)}>
                <MaterialIcon icon="local_police" size={18} />
                <span><strong className="block">110 報案專線</strong><span className="sidebar-row-meta">緊急報案</span></span>
              </a>
            </div>
          </div>
          <button type="button" className="sidebar-tool" onClick={() => {
            setIsEmergencyMenuOpen(false);
            onOpenSettings();
          }}>
            <MaterialIcon icon="settings" size={18} />
            {t.settings}
          </button>
          <p className="sidebar-privacy">
            <MaterialIcon icon="lock" size={14} />
            <span>{t.privacyNote} · {t.privacyNoteSub}</span>
          </p>
        </div>
      </aside>
      {openMenuSession && menuPosition && createPortal(
        <>
          <button
            type="button"
            className="fixed inset-0 z-[90] cursor-default"
            aria-label="關閉對話選單"
            onClick={() => {
              setMenuOpenId(null);
              setMenuPosition(null);
              menuTriggerRef.current?.focus();
            }}
          />
          <div
            ref={menuRef}
            role="dialog"
            aria-modal="true"
            aria-label={t.conversationMenu}
            onKeyDown={event => {
              if (event.key === "Escape") { event.stopPropagation(); setMenuOpenId(null); setMenuPosition(null); menuTriggerRef.current?.focus(); }
              if (event.key !== "Tab") return;
              const controls = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
              const first = controls[0], last = controls.at(-1);
              if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
              else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            }}
            className="sidebar-context-menu"
            style={{ top: menuPosition.top, right: menuPosition.right }}
          >
            <button
              onClick={() => {
                setEditingTitle(openMenuSession.title || getSessionPreview(openMenuSession, t));
                setEditingId(openMenuSession.id);
                setMenuOpenId(null);
                setMenuPosition(null);
              }}
              className="sidebar-tool"
            >
              <MaterialIcon icon="edit" size={14} />
              {t.renameChat}
            </button>
            <button
              onClick={() => handleExport(openMenuSession)}
              className="sidebar-tool"
            >
              <MaterialIcon icon="download" size={14} />
              匯出紀錄
            </button>
            <div className="my-1 h-px bg-outline/10" />
            <button
              onClick={() => {
                onDeleteSession(openMenuSession.id);
                setMenuOpenId(null);
                setMenuPosition(null);
              }}
              className="sidebar-tool text-error"
            >
              <MaterialIcon icon="delete" size={14} />
              {t.deleteChat}
            </button>
          </div>
        </>,
        document.body
      )}
    </>
  );
}
