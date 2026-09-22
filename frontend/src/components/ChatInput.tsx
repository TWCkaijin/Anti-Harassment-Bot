/**
 * ChatInput — 底部輸入列 (Stitch 新版對話內頁 v0.1)
 * 白底藥丸形狀 + 淡橘色邊框 + 掃描圖示 + 動態發送按鈕。
 */
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import MaterialIcon from "./MaterialIcon";
import FollowUpPanel from "./FollowUpPanel";
import type { ConversationMessage, ReplyContext } from "../hooks/useConversation";
import { getFollowUpData } from "./actionButtonValidation";
import { useI18n } from "../i18n";
import {
  getUserMessageValidationError,
  MAX_USER_MESSAGE_CHARACTERS,
} from "../hooks/conversationHistory";

interface ChatInputProps {
  onSend: (message: string, imageBase64?: string, imageUrl?: string, replyContext?: ReplyContext) => void;
  isLoading?: boolean;
  suggestedReplies?: string[];
  replyPrompt?: ConversationMessage;
  onStop?: () => void;
}

export default function ChatInput({ onSend, isLoading, suggestedReplies = [], replyPrompt, onStop }: ChatInputProps) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isFocused, setIsFocused] = useState(false);

  const promptKey = replyPrompt?.id ?? JSON.stringify(suggestedReplies);
  const panelReplies = replyPrompt?.suggestedReplies ?? suggestedReplies;
  const { hasContent: hasMenu } = getFollowUpData({
    actions: replyPrompt?.actionButtons,
    suggestedReplies: panelReplies,
    clarifyingQuestions: replyPrompt?.clarifyingQuestions,
  });
  const [menuState, setMenuState] = useState({ key: promptKey, hidden: false, sent: false });
  // Reset only the menu when the active question changes; keep the composer draft.
  if (menuState.key !== promptKey) {
    setMenuState({ key: promptKey, hidden: false, sent: false });
  }
  const menuVisible = hasMenu && !menuState.hidden && !menuState.sent;

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<"composer" | "menu" | null>(null);

  useEffect(() => {
    if (pendingFocus.current === "composer" && !menuVisible) textareaRef.current?.focus();
    if (pendingFocus.current === "menu" && menuVisible) panelRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    pendingFocus.current = null;
  }, [menuVisible]);

  const hideMenu = () => {
    pendingFocus.current = "composer";
    setMenuState({ key: promptKey, hidden: true, sent: false });
  };

  const showMenu = () => {
    pendingFocus.current = "menu";
    setMenuState({ key: promptKey, hidden: false, sent: false });
  };

  const selectImage = (file: File) => {
    if (!file.type.startsWith("image/")) return;
    if (file.size > 5 * 1024 * 1024) {
      alert("圖片大小不能超過 5MB");
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setSelectedFile(file);
    setPreviewUrl(URL.createObjectURL(file));
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) selectImage(file);
  };

  const removeFile = () => {
    setSelectedFile(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const fileToBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.readAsDataURL(file);
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = (error) => reject(error);
    });
  };

  const handleSend = async () => {
    if (isLoading || menuVisible) return;
    const trimmed = value.trim();
    if (!trimmed && !selectedFile) return;
    if (getUserMessageValidationError(value)) return;

    let base64: string | undefined;
    if (selectedFile) {
      base64 = await fileToBase64(selectedFile);
    }

    onSend(trimmed, base64, previewUrl || undefined);

    setValue("");
    removeFile();
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const image = Array.from(event.clipboardData.files).find((file) =>
      file.type.startsWith("image/")
    );
    if (image) selectImage(image);
  };

  const handleInput = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 160) + "px";
    }
  };

  const messageLength = value.trim().length;
  const messageValidationError = getUserMessageValidationError(value);
  const hasContent = messageLength > 0 || selectedFile !== null;
  const canSend = hasContent && !messageValidationError;

  return (
    <footer className="shrink-0 px-6 lg:px-10 pb-2 lg:pb-4 bg-transparent">
      <div className="w-full relative">
        <div ref={panelRef} hidden={!menuVisible} inert={!menuVisible}>
          <FollowUpPanel
            key={promptKey}
            suggestedReplies={panelReplies}
            actions={replyPrompt?.actionButtons}
            interactionMode={replyPrompt?.interactionMode}
            clarifyingQuestions={replyPrompt?.clarifyingQuestions}
            isLoading={isLoading}
            onSend={(message, replyContext) => onSend(message, undefined, undefined, replyContext)}
            onHide={hideMenu}
            onSent={() => {
              pendingFocus.current = "composer";
              setMenuState({ key: promptKey, hidden: true, sent: true });
            }}
          />
        </div>

        {hasMenu && !menuVisible && !menuState.sent && (
          <div className="mb-2 flex justify-end">
            <button
              type="button"
              onClick={showMenu}
              className="rounded-lg px-3 py-2 text-xs font-medium text-primary transition-colors hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-primary"
            >
              顯示選單
            </button>
          </div>
        )}

        {menuVisible && isLoading && (
          <div className="mb-2 flex justify-end">
            <button
              type="button"
              onClick={onStop}
              disabled={!onStop}
              className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              <span aria-hidden="true"><MaterialIcon icon="stop" size={18} /></span>
              停止回覆
            </button>
          </div>
        )}

        <div hidden={menuVisible} inert={menuVisible}>

          {previewUrl && (
            <div className="mb-3 relative inline-block">
              <img src={previewUrl} alt="Preview" className="h-20 w-auto rounded-lg object-cover border border-primary/20 shadow-sm" />
              <button
                onClick={removeFile}
                className="absolute -top-2 -right-2 bg-white text-on-surface hover:text-error rounded-full shadow-md p-1 border border-primary/10 transition-colors"
              >
                <MaterialIcon icon="close" size={16} />
              </button>
            </div>
          )}

          <div
            onClick={() => textareaRef.current?.focus()}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            className={`relative bg-white border rounded-[32px] shadow-lg flex items-end px-4 lg:px-6 py-2 transition-all cursor-text ${isFocused ? "border-primary ring-2 ring-primary/20" : "border-primary/30"}`}
          >
            <button
              type="button"
              aria-label="上傳圖片"
              className="p-2 text-on-surface/40 hover:text-primary transition-colors cursor-pointer mb-0.5 shrink-0"
              onClick={() => fileInputRef.current?.click()}
            >
              <MaterialIcon icon="image" size={24} />
            </button>
            <input
              type="file"
              accept="image/*"
              aria-label="選擇圖片"
              className="hidden"
              ref={fileInputRef}
              onChange={handleFileChange}
            />

            <textarea
              ref={textareaRef}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              onInput={handleInput}
              aria-invalid={Boolean(messageValidationError)}
              aria-describedby="chat-message-length"
              placeholder={t.inputPlaceholder}
              rows={1}
              className="flex-1 bg-transparent border-none focus:border-none focus:ring-0 outline-none focus:outline-none px-2 lg:px-4 py-2.5 text-on-surface placeholder:text-on-surface/30 font-medium resize-none leading-relaxed"
              style={{ maxHeight: "160px" }}
            />

            <button
              onClick={isLoading ? onStop : handleSend}
              disabled={isLoading ? !onStop : !canSend}
              className={`
                w-10 h-10 lg:w-12 lg:h-12 rounded-full flex items-center justify-center text-white transition-all group shrink-0 mb-0.5 cursor-pointer
                ${
                  (canSend && !isLoading) || isLoading
                    ? "bg-primary hover:bg-primary/90 shadow-md"
                    : "bg-primary/40 cursor-not-allowed"
                }
              `}
              aria-label={isLoading ? "停止回覆" : t.sendMessage}
            >
              <MaterialIcon
                icon={isLoading ? "stop" : "arrow_forward"}
                size={24}
                className={canSend && !isLoading ? "group-hover:translate-x-0.5 transition-transform" : ""}
              />
            </button>
          </div>

          <p
            id="chat-message-length"
            className={`mt-1 px-4 text-right text-[11px] ${messageValidationError ? "text-error" : "text-on-surface/45"}`}
            role={messageValidationError ? "alert" : undefined}
          >
            {messageValidationError ?? `${messageLength.toLocaleString("en-US")} / ${MAX_USER_MESSAGE_CHARACTERS.toLocaleString("en-US")}`}
        </p>
        </div>

        {/* 底部免責聲明 */}
        <div className="mt-2 text-center">
          <p className="text-[10px] leading-4 text-on-surface/40">
            {t.aiDisclaimer}{" "}
            <span className="text-primary font-bold cursor-pointer hover:underline">
              {t.hotline113}
            </span>
          </p>
        </div>
      </div>
    </footer>
  );
}
