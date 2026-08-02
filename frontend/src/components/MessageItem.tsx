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
import type { RagSource, RagSourceType } from "../services/api";

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

const SOURCE_ORDER: RagSourceType[] = ["law", "judgment", "remedy", "unknown"];

const normalizeSource = (source: RagSource | string): RagSource => {
  if (typeof source === "string") {
    return {
      label: source,
      type: "law",
    };
  }
  return {
    label: source.label,
    type: source.type ?? "unknown",
    collection: source.collection,
    doc_id: source.doc_id,
  };
};

const getSourceStyle = (type: RagSourceType) => {
  switch (type) {
    case "law":
      return {
        icon: "menu_book",
        className: "text-orange-700 bg-orange-50 border-orange-200 hover:bg-orange-100",
        tooltipClassName: "border-orange-200",
      };
    case "judgment":
      return {
        icon: "gavel",
        className: "text-blue-700 bg-blue-50 border-blue-200 hover:bg-blue-100",
        tooltipClassName: "border-blue-200",
      };
    case "remedy":
      return {
        icon: "support_agent",
        className: "text-emerald-700 bg-emerald-50 border-emerald-200 hover:bg-emerald-100",
        tooltipClassName: "border-emerald-200",
      };
    default:
      return {
        icon: "database",
        className: "text-gray-700 bg-gray-50 border-gray-200 hover:bg-gray-100",
        tooltipClassName: "border-gray-200",
      };
  }
};

export default function MessageItem({ message }: MessageItemProps) {
  const { t } = useI18n();
  const [activeSourceType, setActiveSourceType] = React.useState<RagSourceType | null>(null);
  const isUser = message.role === "user";
  const isError = message.isError;
  const sourceGroups = React.useMemo(() => {
    const sources = message.ragUsed?.sources ?? [];
    const groups = new Map<RagSourceType, RagSource[]>();
    sources.map(normalizeSource).forEach((source) => {
      const type = source.type ?? "unknown";
      const list = groups.get(type) ?? [];
      if (!list.some((item) => item.label === source.label && item.collection === source.collection)) {
        list.push(source);
      }
      groups.set(type, list);
    });
    return SOURCE_ORDER
      .filter((type) => groups.has(type))
      .map((type) => ({ type, sources: groups.get(type) ?? [] }));
  }, [message.ragUsed?.sources]);

  const getSourceTypeLabel = (type: RagSourceType) => {
    switch (type) {
      case "law":
        return t.ragSourceLaw;
      case "judgment":
        return t.ragSourceJudgment;
      case "remedy":
        return t.ragSourceRemedy;
      default:
        return t.ragSourceUnknown;
    }
  };

  const activeSourceGroup = sourceGroups.find(({ type }) => type === activeSourceType);

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
          <div className="mt-4 space-y-3 border-t border-outline/10 pt-4">
            {message.ragUsed?.status && sourceGroups.length > 0 && (
              <div className="min-w-0 space-y-2">
                <p className="text-xs font-semibold text-on-surface/65">已檢索相關資料</p>
                <div className="flex flex-wrap items-center gap-2">
                {sourceGroups.map(({ type, sources }) => {
                  const style = getSourceStyle(type);
                  const isActive = activeSourceType === type;
                  return (
                    <button
                      key={type}
                      type="button"
                      onClick={() => setActiveSourceType(isActive ? null : type)}
                      className={`inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-semibold transition-colors ${style.className}`}
                      aria-expanded={isActive}
                      aria-label={`${t.ragLabel}：${getSourceTypeLabel(type)}`}
                    >
                      <MaterialIcon icon={style.icon} size={14} />
                      <span>{getSourceTypeLabel(type)}</span>
                      <span className="tabular-nums opacity-70">{sources.length}</span>
                    </button>
                  );
                })}
                </div>
                {activeSourceGroup && (
                  <div className="min-w-0 overflow-hidden rounded-lg border border-outline/15 bg-surface-container-low text-xs text-on-surface">
                    <div className="flex items-center gap-2 border-b border-outline/10 px-3 py-2 font-semibold">
                      <MaterialIcon icon={getSourceStyle(activeSourceGroup.type).icon} size={15} />
                      <span>{getSourceTypeLabel(activeSourceGroup.type)}</span>
                    </div>
                    <ul className="max-h-44 space-y-2 overflow-y-auto p-3 pr-2">
                      {activeSourceGroup.sources.map((src, i) => (
                        <li
                          key={`${src.label}-${i}`}
                          className="min-w-0 break-words rounded-md bg-white px-3 py-2 leading-relaxed shadow-sm"
                        >
                          {src.label}
                        </li>
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
