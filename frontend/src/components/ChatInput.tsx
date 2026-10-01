/**
 * ChatInput — 底部輸入列 (Stitch 新版對話內頁 v0.1)
 * 白底藥丸形狀 + 淡橘色邊框 + 掃描圖示 + 動態發送按鈕。
 */
import {
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import MaterialIcon from "./MaterialIcon";
import { settleSendOutcome, type SendOutcome } from "../services/sendOutcome";
import { IMAGE_ACCEPT, IMAGE_ONLY_MESSAGE } from "../services/imageUpload";
import { useImageAttachment } from "../hooks/useImageAttachment";
import { useAutosizeTextarea } from "../hooks/useAutosizeTextarea";
import FollowUpPanel from "./FollowUpPanel";
import TypedClarification from "./TypedClarification";
import NextStepSuggestions from "./NextStepSuggestions";
import type { ConversationMessage, ReplyContext } from "../hooks/useConversation";
import { getFollowUpData } from "./actionButtonValidation";
import { useI18n } from "../i18n";
import { isClarification, type ClarificationDraft } from "../services/caseFacts";
import {
  getUserMessageValidationError,
  MAX_USER_MESSAGE_CHARACTERS,
} from "../hooks/conversationHistory";

interface ChatInputProps {
  onSend: (message: string, imageBase64?: string, imageUrl?: string, replyContext?: ReplyContext) => SendOutcome;
  isLoading?: boolean;
  suggestedReplies?: string[];
  replyPrompt?: ConversationMessage;
  streamingPrompt?: Pick<ConversationMessage, "id" | "streamingGuidance">;
  onStop?: () => void;
  caseRevision?: number;
  activeContractVersion?: 1 | 2 | 3 | 4;
  disabled?: boolean;
  allowImageUpload?: boolean;
  onClarificationDraftChange?: (messageId: string, draft: ClarificationDraft) => void;
}

export default function ChatInput({ onSend, isLoading, suggestedReplies = [], replyPrompt, streamingPrompt, onStop, caseRevision, activeContractVersion, disabled = false, allowImageUpload = true, onClarificationDraftChange }: ChatInputProps) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const { attachment, isReading, error: imageError, selectFile, removeImage, setError: setImageError } = useImageAttachment();
  const pendingSend = useRef(false);
  const [sendPending, setSendPending] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  const preview = isLoading ? streamingPrompt?.streamingGuidance : undefined;
  const promptKey = streamingPrompt?.id ?? replyPrompt?.id ?? JSON.stringify(suggestedReplies);
  const panelReplies = preview ? preview.suggested_replies ?? [] : replyPrompt?.suggestedReplies ?? suggestedReplies;
  const typedContract = replyPrompt?.contractVersion === 2 || replyPrompt?.contractVersion === 3 || replyPrompt?.contractVersion === 4;
  const typedQuestion = !preview && typedContract && replyPrompt.contractVersion === activeContractVersion
    && !replyPrompt.superseded && !replyPrompt.isError && !replyPrompt.isCancelled && !replyPrompt.isStreaming
    && replyPrompt.interactionMode === "clarify" && replyPrompt.factsRevision === caseRevision
    && isClarification(replyPrompt.clarification, replyPrompt.contractVersion)
    && (replyPrompt.contractVersion !== 4 || replyPrompt.clarification.context_revision === caseRevision)
    && (replyPrompt.contractVersion === 2 || Boolean(replyPrompt.clarification.validation_token))
      ? replyPrompt.clarification : undefined;
  const interactionMode = preview ? preview.interaction_mode : typedContract ? "answer" : replyPrompt?.interactionMode;
  const clarifyingQuestions = preview ? preview.clarifying_questions : replyPrompt?.clarifyingQuestions;
  const actions = preview ? undefined : replyPrompt?.actionButtons;
  const { hasQuestion: hasLegacyQuestion, optionActions, suggestions } = getFollowUpData({
    actions,
    suggestedReplies: panelReplies,
    clarifyingQuestions,
    interactionMode,
  });
  const hasQuestion = Boolean(typedQuestion) || hasLegacyQuestion;
  const nextSteps = (typedContract && replyPrompt?.interactionMode === "clarify") || (preview && interactionMode !== "answer") ? [] : optionActions.length > 0
    ? optionActions.flatMap((action) => action.options)
    : suggestions.map((suggestion) => ({ label: suggestion, value: suggestion }));
  const [menuState, setMenuState] = useState({ key: promptKey, hidden: false, sent: false });
  // Reset only the menu when the active question changes; keep the composer draft.
  if (menuState.key !== promptKey) {
    setMenuState({ key: promptKey, hidden: false, sent: false });
  }
  const menuVisible = hasQuestion && !menuState.hidden && !menuState.sent;

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<"composer" | "menu" | null>(null);
  useAutosizeTextarea(textareaRef, value, !menuVisible);

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

  const handleSend = () => {
    if (disabled || isLoading || menuVisible || pendingSend.current || isReading) return;
    if (attachment && !allowImageUpload) { setImageError("圖片上傳目前已停用，請移除圖片後再傳送。"); return; }
    const trimmed = value.trim();
    if ((!trimmed && !attachment) || getUserMessageValidationError(value)) return;
    pendingSend.current = true;
    setSendPending(true);
    const finish = () => { pendingSend.current = false; setSendPending(false); };
    try { settleSendOutcome(onSend(trimmed || IMAGE_ONLY_MESSAGE, attachment?.dataUrl, attachment?.dataUrl), () => {
      finish();
      setValue(previous => previous.trim() === trimmed ? "" : previous);
      if (attachment) removeImage(attachment);
    }, finish); } catch { finish(); }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const image = Array.from(event.clipboardData.files).find(file => file.type.startsWith("image/"));
    if (image) {
      event.preventDefault();
      if (disabled || isLoading || menuVisible || pendingSend.current) return;
      if (!allowImageUpload) { setImageError("圖片上傳目前已停用。"); return; }
      selectFile(image);
    }
  };

  const messageLength = value.trim().length;
  const messageValidationError = getUserMessageValidationError(value);
  const hasContent = messageLength > 0 || attachment !== null;
  const canSend = !disabled && !sendPending && !isReading && hasContent && !messageValidationError && (!attachment || allowImageUpload);
  const imageControlsDisabled = disabled || Boolean(isLoading) || sendPending;

  return (
    <footer className="shrink-0 px-6 lg:px-10 pb-2 lg:pb-4 bg-transparent">
      <div className="w-full relative">
        <div ref={panelRef} hidden={!menuVisible} inert={!menuVisible} className="max-h-[min(50dvh,28rem)] overflow-y-auto overscroll-contain">
          {typedQuestion && <TypedClarification key={`${promptKey}-${caseRevision}`} clarification={typedQuestion} revision={caseRevision!} isLoading={Boolean(isLoading || disabled)} onHide={hideMenu} draft={replyPrompt?.clarificationDraft} onDraftChange={draft => { if (replyPrompt) onClarificationDraftChange?.(replyPrompt.id, draft); }} onSend={(message, context) => {
            const outcome = onSend(message, undefined, undefined, context);
            settleSendOutcome(outcome, () => setMenuState({ key: promptKey, hidden: true, sent: true }));
            return outcome;
          }} />}
          {!typedQuestion && hasLegacyQuestion && <FollowUpPanel
            key={promptKey}
            suggestedReplies={panelReplies}
            actions={actions}
            interactionMode={interactionMode}
            clarifyingQuestions={clarifyingQuestions}
            isLoading={isLoading || disabled}
            isStreaming={Boolean(preview)}
            onSend={(message, replyContext) => onSend(message, undefined, undefined, replyContext)}
            onHide={hideMenu}
            onSent={() => {
              pendingFocus.current = "composer";
              setMenuState({ key: promptKey, hidden: true, sent: true });
            }}
          />}
        </div>

        {hasQuestion && !menuVisible && !menuState.sent && (
          <div className="mb-2 flex justify-end">
            <button
              type="button"
              onClick={showMenu}
              className="rounded-lg px-3 py-2 text-xs font-medium text-primary transition-colors hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-primary"
            >
              {typedQuestion ? t.clarificationShow : "顯示選單"}
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
          {!hasQuestion && (
            <NextStepSuggestions
              key={promptKey}
              suggestions={nextSteps}
              isLoading={isLoading || disabled}
              onSend={(message) => onSend(message)}
            />
          )}

          {attachment && <div className="relative mb-3 inline-block">
            <img src={attachment.dataUrl} alt={t.imagePreview} className="h-24 max-w-full rounded-xl border border-outline/20 object-contain" />
            <button type="button" aria-label={t.removeImage} disabled={imageControlsDisabled} onClick={() => removeImage()}
              className="absolute -right-2 -top-2 flex h-9 w-9 items-center justify-center rounded-full border border-outline/20 bg-white shadow-sm focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-40">
              <MaterialIcon icon="close" size={18} />
            </button>
          </div>}
          {isReading && <p role="status" className="mb-2 text-xs text-on-surface/60">正在讀取圖片…</p>}
          {(imageError || (attachment && !allowImageUpload)) && <p role="alert" className="mb-2 text-xs text-error">{attachment && !allowImageUpload ? "圖片上傳目前已停用，請移除圖片後再傳送。" : imageError}</p>}

          <div
            onClick={() => textareaRef.current?.focus()}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            className={`relative bg-white border rounded-[32px] shadow-lg flex items-end px-4 lg:px-6 py-2 transition-all cursor-text ${isFocused ? "border-primary ring-2 ring-primary/20" : "border-primary/30"}`}
          >
            {allowImageUpload && <>
              <input ref={fileInputRef} type="file" accept={IMAGE_ACCEPT} aria-label={t.chooseImage} disabled={imageControlsDisabled} className="hidden" onChange={event => {
                const file = event.currentTarget.files?.[0];
                event.currentTarget.value = "";
                if (file && !imageControlsDisabled && !menuVisible) selectFile(file);
              }} />
              <button type="button" aria-label={t.addImage} title={t.addImage} disabled={imageControlsDisabled} onClick={event => { event.stopPropagation(); fileInputRef.current?.click(); }}
                className="mb-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-primary transition-colors hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-40 lg:h-12 lg:w-12">
                <MaterialIcon icon="image" size={22} />
              </button>
            </>}
            <textarea
              ref={textareaRef}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              aria-invalid={Boolean(messageValidationError)}
              aria-describedby="chat-message-length"
              placeholder={t.inputPlaceholder}
              rows={1}
              className="min-w-0 flex-1 bg-transparent border-none focus:border-none focus:ring-0 focus-visible:ring-0 outline-none focus:outline-none focus-visible:outline-none px-2 lg:px-4 py-2.5 text-on-surface placeholder:text-on-surface/30 font-medium resize-none leading-relaxed"
              style={{ border: 0, outline: "none", boxShadow: "none" }}
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
