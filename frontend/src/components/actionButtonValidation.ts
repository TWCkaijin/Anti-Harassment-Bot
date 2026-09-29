import type { OptionsActionButton, TelActionButton, UrlActionButton } from "../services/api";
import { getUserMessageValidationError, MAX_USER_MESSAGE_CHARACTERS } from "../hooks/conversationHistory";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && [...value].length <= maximum;
}

function safeUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 2048 || !/^https?:\/\/[^/]/i.test(value) || /[\s\\]/.test(value)
    || [...value].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) return null;
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function getSafeResourceActions(actions: unknown): Array<TelActionButton | UrlActionButton> {
  if (!Array.isArray(actions)) return [];
  return actions.flatMap((action): Array<TelActionButton | UrlActionButton> => {
    if (!isRecord(action) || !boundedText(action.label, 80)) return [];
    if (action.action === "tel" && typeof action.phone_number === "string" && /^[0-9+()-]{3,24}$/.test(action.phone_number)) {
      return [{ action: "tel", label: action.label, phone_number: action.phone_number }];
    }
    if (action.action === "url") {
      const url = safeUrl(action.url);
      if (url) return [{ action: "url", label: action.label, url }];
    }
    return [];
  });
}

// Stored conversations can predate the current schema; validate before rendering.
export function isValidOptionsAction(action: unknown): action is OptionsActionButton {
  return isRecord(action) && action.action === "options" && boundedText(action.label, 80)
    && typeof action.id === "string" && /^[a-z][a-z0-9_-]{1,63}$/.test(action.id)
    && boundedText(action.title, 160) && Array.isArray(action.options)
    && action.options.length >= 2 && action.options.length <= 8
    && action.options.every((option) => isRecord(option) && boundedText(option.label, 80)
      && boundedText(option.value, 500))
    && new Set(action.options.map((option) => option.value.trim())).size === action.options.length;
}

/** Share the panel's validated content with the composer that it replaces. */
export function getFollowUpData({ actions, suggestedReplies, clarifyingQuestions, interactionMode }: {
  actions?: unknown;
  suggestedReplies?: unknown;
  clarifyingQuestions?: unknown;
  interactionMode?: unknown;
}) {
  const optionActions = Array.isArray(actions) ? actions.filter(isValidOptionsAction) : [];
  const resources = getSafeResourceActions(actions);
  const questions = Array.isArray(clarifyingQuestions)
    ? [...new Set(clarifyingQuestions.filter((question) => boundedText(question, 2000)).map((question) => question.trim()))]
    : [];
  const suggestions = Array.isArray(suggestedReplies)
    ? [...new Set(suggestedReplies.filter((reply): reply is string => boundedText(reply, MAX_USER_MESSAGE_CHARACTERS) && !getUserMessageValidationError(reply)))]
    : [];

  return {
    optionActions,
    resources,
    questions,
    suggestions,
    hasQuestion: interactionMode === "clarify" && questions.length > 0,
    hasContent: optionActions.length + resources.length + questions.length + suggestions.length > 0,
  };
}
