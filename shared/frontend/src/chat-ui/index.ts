/** Chat UI shared by the client app and the admin diagnostic chat. */
export { default as CaseSummary } from "./CaseSummary";
export { default as ChatInput } from "./ChatInput";
export { default as MaterialIcon } from "./MaterialIcon";
export { default as MessageItem } from "./MessageItem";
export { default as TypingIndicator } from "./TypingIndicator";
export { I18nProvider, useI18n, type Locale, type Translations } from "./i18n";
export { setChatUiAnalytics, type ChatUiEvent, type ChatUiTracker } from "./analytics";
export * from "./processingSteps";
export * from "./sendOutcome";
export { useAutosizeTextarea } from "./useAutosizeTextarea";
export { useImageAttachment } from "./useImageAttachment";
export type * from "./types";
