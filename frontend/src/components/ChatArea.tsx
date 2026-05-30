/**
 * ChatArea — 主要對話區域（Stitch 新版對話內頁 v0.1）
 * 包含頂部導航列、訊息列表（或 WelcomeHero）、打字動畫、底部輸入框。
 */
import { useRef, useEffect, useState} from "react";
import MaterialIcon from "./MaterialIcon";
import { useI18n } from "../i18n";
import type { ConversationMessage } from "../hooks/useConversation";
import MessageItem from "./MessageItem";
import WelcomeHero from "./WelcomeHero";
import ChatInput from "./ChatInput";
import TypingIndicator from "./TypingIndicator";
import { checkHealth } from "../services/api";

interface ChatAreaProps {
  messages: ConversationMessage[];
  isLoading: boolean;
  onSend: (message: string, imageBase64?: string, imageUrl?: string) => void;
  onOpenSidebar: () => void;
}

export default function ChatArea({
  messages,
  isLoading,
  onSend,
  onOpenSidebar,
}: ChatAreaProps) {
  const { t } = useI18n();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [isBackendConnected, setIsBackendConnected] = useState<boolean | null>(null);

  // 自動滾動到最下方
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // 測試後端連線
  useEffect(() => {
    let mounted = true;
    checkHealth()
      .then(() => {
        if (mounted) setIsBackendConnected(true);
      })
      .catch(() => {
        if (mounted) setIsBackendConnected(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const hasMessages = messages.length > 0;

  return (
    <main className="flex-1 flex flex-col h-screen relative bg-white lg:bg-transparent min-w-0">
      {/* 頂部導航列 */}
      <header className="flex items-center justify-between px-6 lg:px-10 py-6 border-b border-outline/10 bg-white shrink-0">
        <div className="flex items-center gap-4 min-w-0">
          {/* 漢堡按鈕（行動端） */}
          <button
            onClick={onOpenSidebar}
            className="lg:hidden p-2 -ml-2 rounded-xl hover:bg-surface-container-high transition-colors cursor-pointer shrink-0"
            aria-label={t.openSidebar}
          >
            <MaterialIcon icon="menu" size={24} className="text-on-surface/70" />
          </button>
          
          <div className="space-y-1 min-w-0">
            <h2 className="text-base lg:text-lg font-bold text-on-surface truncate">
              {t.appTitle}
            </h2>
            <div className="flex items-center gap-1.5 lg:gap-2 text-[10px] lg:text-xs text-on-surface/50 truncate">
              <span>溫暖守護</span>
              <span>•</span>
              <span>匿名安全</span>
              <span>•</span>
              <span className="truncate">法律知識庫</span>
            </div>
          </div>
        </div>

        {/* 連線狀態指示 */}
        <div className="flex items-center gap-3 shrink-0 pl-2">
          <div
            className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1 rounded-full border transition-colors ${
              isLoading
                ? "text-orange-600 bg-orange-50 border-orange-100"
                : isBackendConnected === false
                ? "text-red-600 bg-red-50 border-red-100"
                : "text-green-600 bg-green-50 border-green-100"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isLoading
                  ? "bg-orange-500 animate-pulse"
                  : isBackendConnected === false
                  ? "bg-red-500"
                  : isBackendConnected === null
                  ? "bg-gray-400 animate-pulse"
                  : "bg-green-500"
              }`}
            ></span>
            <span className="hidden sm:inline">
              {isLoading
                ? t.statusProcessing
                : isBackendConnected === false
                ? "連線失敗"
                : isBackendConnected === null
                ? "連線中..."
                : t.statusConnected}
            </span>
          </div>
        </div>
      </header>

      {/* 訊息區域 */}
      <section className="flex-1 overflow-y-auto chat-scrollbar hero-mesh-gradient flex flex-col">
        {hasMessages ? (
          <div className="w-full px-6 lg:px-10 py-8 space-y-10">
            {messages.map((msg) => (
              <MessageItem key={msg.id} message={msg} />
            ))}
            {isLoading && <TypingIndicator />}
            <div ref={messagesEndRef} className="h-4" />
          </div>
        ) : (
          <WelcomeHero onSuggest={onSend} />
        )}
      </section>

      {/* 底部輸入框（僅在有訊息時顯示，WelcomeHero 有自己的輸入框） */}
      {hasMessages && <ChatInput onSend={onSend} isLoading={isLoading} />}
    </main>
  );
}
