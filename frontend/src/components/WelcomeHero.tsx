/**
 * WelcomeHero — 首頁標題、問題輸入與建議入口。
 */
import { useRef, useState, type KeyboardEvent, type ClipboardEvent } from "react";
import MaterialIcon from "./MaterialIcon";
import { settleSendOutcome, type SendOutcome } from "../services/sendOutcome";
import { IMAGE_ACCEPT, IMAGE_ONLY_MESSAGE } from "../services/imageUpload";
import { useI18n } from "../i18n";
import { useImageAttachment } from "../hooks/useImageAttachment";
import { useAutosizeTextarea } from "../hooks/useAutosizeTextarea";
import {
  getUserMessageValidationError,
  MAX_USER_MESSAGE_CHARACTERS,
} from "../hooks/conversationHistory";

interface WelcomeHeroProps {
  onSuggest: (message: string, imageBase64?: string, imageUrl?: string) => SendOutcome;
  disabled?: boolean;
  allowImageUpload?: boolean;
}

export default function WelcomeHero({ onSuggest, disabled = false, allowImageUpload = true }: WelcomeHeroProps) {
  const { t } = useI18n();
  const [inputValue, setInputValue] = useState("");
  const { attachment, isReading, error, selectFile, removeImage, setError } = useImageAttachment();
  const pendingSend = useRef(false);
  const [sendPending, setSendPending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  useAutosizeTextarea(textareaRef, inputValue);

  const effectiveAttachment = allowImageUpload ? attachment : null;
  const attachmentBlocked = Boolean(attachment && !allowImageUpload);
  const imageError = attachmentBlocked ? "圖片上傳目前已停用，請移除圖片後再傳送。" : error;

  const handleSend = () => {
    if (disabled || pendingSend.current || isReading || attachmentBlocked) return;
    const trimmed = inputValue.trim();
    if ((!trimmed && !effectiveAttachment) || getUserMessageValidationError(inputValue)) return;
    pendingSend.current = true;
    setSendPending(true);
    const finish = () => { pendingSend.current = false; setSendPending(false); };
    try { settleSendOutcome(onSuggest(trimmed || IMAGE_ONLY_MESSAGE, effectiveAttachment?.dataUrl, effectiveAttachment?.dataUrl), () => {
      finish();
      setInputValue(previous => previous.trim() === trimmed ? "" : previous);
      if (effectiveAttachment) removeImage(effectiveAttachment);
    }, finish); } catch { finish(); }
  };
  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const image = Array.from(event.clipboardData.files).find(file => file.type.startsWith("image/"));
    if (!image) return;
    event.preventDefault();
    if (disabled || pendingSend.current) return;
    if (!allowImageUpload) {
      setError("圖片上傳目前已停用，請改用文字描述。");
      return;
    }
    selectFile(image);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const suggestions = [t.suggestLaw, t.suggestReport, t.suggestSelfCare];
  const messageLength = inputValue.trim().length;
  const messageValidationError = getUserMessageValidationError(inputValue);
  const hasContent = messageLength > 0 || effectiveAttachment !== null;
  const canSend = !disabled && !sendPending && !isReading && !attachmentBlocked && hasContent && !messageValidationError;

  return (
    <div className="flex-1 flex flex-col hero-mesh-gradient overflow-y-auto">
      <section className="w-full px-5 lg:px-10 py-12 lg:py-24 flex flex-col items-center text-center flex-1 justify-center">
        {/* 大字標題 */}
        <h2 className="text-3xl lg:text-4xl font-bold tracking-tight text-on-surface max-w-4xl mb-6 leading-tight">
          {t.heroTitle}
          <br />
          <span className="text-primary">{t.heroTitleHighlight}</span>
        </h2>

        <p className="text-sm lg:text-base text-on-surface-variant mb-10 max-w-2xl leading-relaxed">
          {t.heroDesc}
        </p>

        {/* Stitch v0.1 輸入框 */}
        <div className="w-full relative mt-4">
          {attachment && <div className="relative mb-3 inline-block">
            <img src={attachment.dataUrl} alt={t.imagePreview} className="h-20 max-w-full rounded-xl border border-primary/20 object-contain" />
            <button type="button" aria-label={t.removeImage} onClick={() => removeImage()} disabled={disabled || sendPending} className="absolute -right-2 -top-2 flex h-8 w-8 items-center justify-center rounded-full border border-primary/20 bg-white text-on-surface shadow-sm disabled:opacity-40">
              <MaterialIcon icon="close" size={16} />
            </button>
          </div>}
          {imageError && <p role="alert" className="mb-3 text-xs leading-relaxed text-error">{imageError}</p>}
          <div 
            onClick={() => textareaRef.current?.focus()}
            className="relative bg-white border border-primary/30 rounded-3xl shadow-lg flex items-end px-4 lg:px-6 py-2 transition-shadow focus-within:shadow-float focus-within:border-primary/50 text-left cursor-text"
          >
            {allowImageUpload && <>
              <button type="button" aria-label={t.addImage} disabled={disabled || sendPending || isReading} onClick={event => { event.stopPropagation(); fileInputRef.current?.click(); }} className="mb-0.5 shrink-0 p-2 text-on-surface/50 transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-40">
                <MaterialIcon icon="image" size={24} />
              </button>
              <input ref={fileInputRef} type="file" accept={IMAGE_ACCEPT} aria-label={t.chooseImage} className="hidden" disabled={disabled || sendPending || isReading} onChange={event => {
                const file = event.currentTarget.files?.[0];
                event.currentTarget.value = "";
                if (file && !disabled && !pendingSend.current && allowImageUpload) selectFile(file);
              }} />
            </>}
            <textarea
              ref={textareaRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              aria-label={t.heroInputLabel}
              aria-invalid={Boolean(messageValidationError)}
              aria-describedby="welcome-message-length"
              placeholder={t.heroInputPlaceholder}
              rows={1}
              className="min-w-0 flex-1 bg-transparent border-none focus:border-none focus:ring-0 outline-none focus:outline-none focus-visible:outline-none focus:shadow-none px-2 lg:px-4 py-2.5 text-on-surface placeholder:text-on-surface/30 font-medium resize-none leading-relaxed"
              style={{ border: 0, outline: "none", boxShadow: "none" }}
            />
            <button
              onClick={handleSend}
              disabled={!canSend}
              aria-label={t.sendMessage}
              className={`
                w-10 h-10 lg:w-12 lg:h-12 rounded-full flex items-center justify-center text-white transition-all group shrink-0 mb-0.5 cursor-pointer
                ${
                  canSend
                    ? "bg-primary hover:bg-primary/90 shadow-md"
                    : "bg-primary/40 cursor-not-allowed"
                }
              `}
            >
              <MaterialIcon 
                icon="arrow_forward" 
                size={24} 
                className={canSend ? "group-hover:translate-x-0.5 transition-transform" : ""}
              />
            </button>
          </div>

          <p
            id="welcome-message-length"
            className={`mt-1 px-4 text-right text-[11px] ${messageValidationError ? "text-error" : "text-on-surface/45"}`}
            role={messageValidationError ? "alert" : undefined}
          >
            {messageValidationError ?? `${messageLength.toLocaleString("en-US")} / ${MAX_USER_MESSAGE_CHARACTERS.toLocaleString("en-US")}`}
          </p>

          {/* 建議 Chips */}
          <div role="group" aria-label={t.heroSuggestionsLabel} className="mt-4 flex flex-wrap justify-center gap-2">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion}
                disabled={disabled || sendPending || isReading || attachmentBlocked}
                onClick={() => { if (!disabled && !pendingSend.current && !isReading && !attachmentBlocked) onSuggest(suggestion); }}
                className="px-3 py-1.5 bg-surface-container rounded-full text-xs font-semibold text-on-surface-variant cursor-pointer hover:bg-surface-container-high transition-colors tracking-wide"
              >
                {suggestion}
              </button>
            ))}
          </div>
          <p className="mt-4 mx-auto max-w-xl text-[11px] leading-relaxed text-on-surface/50">
            {t.heroPrivacyNote}
          </p>
        </div>
      </section>
    </div>
  );
}
