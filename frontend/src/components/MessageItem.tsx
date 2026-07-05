/**
 * MessageItem — 訊息氣泡元件（Stitch 新版對話內頁 v0.1）
 * AI 訊息使用白底、帶框線卡片 (`bg-white border p-8 rounded-3xl`)。
 * 使用者訊息使用 primary 配色，特殊圓角 (`rounded-[2rem] rounded-tr-none`)。
 * 支援 RAG 來源標示（hover 展開條文）、匿名化通知、錯誤狀態、Markdown 渲染。
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import MaterialIcon from "./MaterialIcon";
import { useI18n } from "../i18n";
import React from "react";
import type { ConversationMessage } from "../hooks/useConversation";

interface MessageItemProps {
  message: ConversationMessage;
}

const getEmotionColorClasses = (color?: string) => {
  switch (color?.toLowerCase()) {
    case "red":
      return "text-error bg-error-container/30 border-error/20";
    case "yellow":
      return "text-yellow-700 bg-yellow-100 border-yellow-200";
    case "green":
      return "text-green-700 bg-green-100 border-green-200";
    case "blue":
      return "text-blue-700 bg-blue-100 border-blue-200";
    case "gray":
      return "text-gray-600 bg-gray-100 border-gray-200";
    default:
      return "text-secondary bg-secondary-container/30 border-secondary/20";
  }
};

export default function MessageItem({ message }: MessageItemProps) {
  const { t } = useI18n();
  const [showTooltip, setShowTooltip] = React.useState(false);
  const isUser = message.role === "user";
  const isError = message.isError;

  return (
    <div
      className={`animate-fade-in-up flex gap-4 max-w-4xl ${
        isUser ? "ml-auto flex-row-reverse items-start" : ""
      }`}
    >
      {/* Avatar 與 Emotion 標籤容器 */}
      <div className="flex flex-col items-center gap-1 shrink-0">
        <div
          className={`
            w-8 h-8 rounded-full flex items-center justify-center shadow-sm
            ${
              isUser
                ? "bg-secondary text-white"
                : isError
                  ? "bg-error-container text-error"
                  : "bg-primary text-white"
            }
          `}
        >
          {isUser ? (
            <MaterialIcon icon="person" size={16} />
          ) : isError ? (
            <MaterialIcon icon="error" size={16} />
          ) : (
            <MaterialIcon icon="shield" size={16} filled />
          )}
        </div>
        {/* 情緒標籤 (僅針對使用者訊息顯示) */}
        {isUser && message.emotion && (
          <span
            className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${getEmotionColorClasses(message.emotionColor)}`}
          >
            {message.emotion}
          </span>
        )}
      </div>

      {/* 訊息氣泡 */}
      <div
        className={`text-sm flex flex-col gap-2
          ${
              isUser
                ? "bg-primary text-white py-3 px-5 lg:px-6 rounded-[2rem] rounded-tr-none shadow-md"
                : isError
                  ? "bg-error-container/30 text-error border border-error/15 p-5 lg:p-6 rounded-3xl shadow-sm"
                  : "space-y-2 bg-white p-5 lg:p-6 rounded-3xl border border-outline/10 shadow-sm leading-relaxed text-on-surface"
          }
        `}
      >
        {/* 如果有上傳圖片，顯示圖片預覽 */}
        {isUser && message.imageUrl && (
          <img
            src={message.imageUrl}
            alt="Uploaded attachment"
            className="max-w-[200px] lg:max-w-xs rounded-xl object-contain border border-white/20 mb-1 shadow-sm"
          />
        )}

        {/* 訊息內容 */}
        <div className={`${isUser ? "font-medium whitespace-pre-wrap" : "markdown-message"} break-words`}>
          {isUser || isError ? (
            message.content
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ children, ...props }) => (
                  <a {...props} target="_blank" rel="noreferrer">
                    {children}
                  </a>
                ),
              }}
            >
              {message.content}
            </ReactMarkdown>
          )}
        </div>

        {/* 底部標示列 */}
        {!isUser && !isError && (message.ragUsed?.status || message.anonymized) && (
          <div className="flex flex-wrap gap-2 mt-4 pt-4 border-t border-outline/10">
            {message.ragUsed?.status && (
              <div className="group relative flex items-center">
                <button
                  onClick={() => setShowTooltip(!showTooltip)}
                  onBlur={() => setShowTooltip(false)}
                  className="inline-flex items-center gap-1.5 text-[11px] font-medium text-tertiary bg-primary-container px-2.5 py-1 rounded-full cursor-pointer transition-colors hover:bg-outline/20"
                >
                  <MaterialIcon icon="menu_book" size={14} />
                  {t.ragLabel}
                </button>

                {/* 條文來源 Hover / Click Tooltip */}
                {message.ragUsed.sources.length > 0 && (
                  <div
                    className={`absolute bottom-full left-0 mb-2 w-max max-w-[280px] bg-inverse-surface text-inverse-on-surface text-xs rounded-xl shadow-float z-10 animate-fade-in-up transition-opacity ${showTooltip ? "block" : "hidden group-hover:block"}`}
                  >
                    <div className="p-3 border-b border-inverse-on-surface/10 font-bold bg-white/5 rounded-t-xl">
                      {t.ragTooltipTitle}
                    </div>
                    <ul className="p-3 max-h-40 overflow-y-auto list-disc pl-6 space-y-1">
                      {message.ragUsed.sources.map((src, i) => (
                        <li key={i}>{src}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {message.anonymized && (
              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-secondary bg-secondary-fixed/50 px-2.5 py-1 rounded-full">
                <MaterialIcon icon="shield_with_heart" size={14} />
                {t.anonymizedLabel}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
