import { useId, useRef, useState, type KeyboardEvent } from "react";

import { getUserMessageValidationError } from "../hooks/conversationHistory";
import type { ReplyContext } from "../hooks/useConversation";
import type { ActionButton, ActionOption } from "../services/api";
import ActionButtons from "./ActionButtons";
import { getFollowUpData } from "./actionButtonValidation";
import MaterialIcon from "./MaterialIcon";

interface FollowUpPanelProps {
  suggestedReplies?: string[];
  actions?: ActionButton[];
  clarifyingQuestions?: string[];
  interactionMode?: "answer" | "clarify";
  isLoading?: boolean;
  onSend: (message: string, replyContext?: ReplyContext) => void;
  onHide?: () => void;
  onSent?: () => void;
}

interface QuestionGroup {
  key: string;
  title: string;
  context?: string;
  options: ActionOption[];
}

interface Answer {
  selected?: number | "other";
  other: string;
}

const navigationClassName = "inline-flex min-h-9 items-center gap-1 rounded-lg px-2 text-xs font-medium text-on-surface/65 transition-colors hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-35";

/** One message owns one panel; the caller keys it by assistant message ID. */
export default function FollowUpPanel({
  suggestedReplies = [],
  actions = [],
  clarifyingQuestions = [],
  interactionMode = "answer",
  isLoading = false,
  onSend,
  onHide,
  onSent,
}: FollowUpPanelProps) {
  const panelId = useId();
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [sent, setSent] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submitted = useRef(false);

  const { optionActions, resources, questions, suggestions, hasQuestion } = getFollowUpData({ actions, suggestedReplies, clarifyingQuestions, interactionMode });
  const supplementalQuestions = questions.filter((question) => !optionActions.some((action) => action.title.trim() === question));
  const groups: QuestionGroup[] = optionActions.length > 0
    ? optionActions.map((action, index) => ({ key: `${index}-${action.id}`, title: action.title, context: [action.title, ...supplementalQuestions].join("\n"), options: action.options }))
    : suggestions.length > 0 || questions.length > 0
      ? [{ key: "suggestions", title: questions.length === 1 ? questions[0] : "您想如何回覆？", context: questions.join("\n") || undefined, options: suggestions.map((reply) => ({ label: reply, value: reply })) }]
      : [];
  const groupIndex = Math.min(currentIndex, Math.max(0, groups.length - 1));
  const currentGroup = groups[groupIndex];
  const currentAnswer = currentGroup ? answers[currentGroup.key] : undefined;
  const getAnswer = (group: QuestionGroup) => {
    const answer = answers[group.key];
    return answer?.selected === "other" ? answer.other.trim()
      : typeof answer?.selected === "number" ? group.options[answer.selected]?.value ?? "" : "";
  };
  const allAnswered = groups.length > 0 && groups.every((group) => getAnswer(group).trim().length > 0);
  // History omits panel metadata; only configured option values stand on their own.
  const reply = groups.length === 1
    ? (optionActions.length === 0 || answers[groups[0].key]?.selected === "other") && groups[0].context
      ? `${groups[0].context}\n${getAnswer(groups[0])}` : getAnswer(groups[0])
    : [...(supplementalQuestions.length > 0 ? [supplementalQuestions.join("\n")] : []), ...groups.map((group) => `${group.title}\n${getAnswer(group)}`)].join("\n\n");
  const validationError = getUserMessageValidationError(reply);
  const statusError = validationError ?? submitError;
  const canSubmit = hasQuestion && allAnswered && !validationError && !isLoading;
  const heading = "AI 需要更多您的資訊";
  const extraQuestions = questions.filter((question) => !groups.some((group) => group.title.trim() === question));

  const updateAnswer = (patch: Partial<Answer>) => {
    if (!currentGroup || isLoading || submitted.current) return;
    setSubmitError(null);
    setAnswers((previous) => ({
      ...previous,
      [currentGroup.key]: { ...(previous[currentGroup.key] ?? { other: "" }), ...patch },
    }));
  };

  const handleSubmit = () => {
    if (!canSubmit || submitted.current) return;
    submitted.current = true;
    setSubmitError(null);
    try {
      onSend(reply, {
        answers: groups.map((group) => ({ question: group.context ?? group.title, answer: getAnswer(group) })),
      });
    } catch {
      submitted.current = false;
      setSubmitError("回覆未能送出，請再試一次。");
      return;
    }
    setSent(true);
    onSent?.();
  };

  const handlePanelKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape" && onHide) {
      event.preventDefault();
      event.stopPropagation();
      onHide();
    }
  };

  const handleOtherKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSubmit();
    }
  };

  if (sent || !hasQuestion) return null;

  return (
    <section aria-labelledby={`${panelId}-heading`} onKeyDown={handlePanelKeyDown} className="my-3 overflow-hidden rounded-2xl border border-primary/15 bg-white/95 shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-primary/10 px-4 py-2">
        <h2 id={`${panelId}-heading`} className="text-sm font-semibold text-on-surface">{heading}</h2>
        <div className="flex shrink-0 items-center gap-2">
          {groups.length > 1 && <span className="text-xs tabular-nums text-on-surface/50" aria-live="polite">{groupIndex + 1} / {groups.length} 題</span>}
          {onHide && <button type="button" onClick={onHide} className={navigationClassName}>隱藏</button>}
        </div>
      </div>
      <div className="max-h-[min(34dvh,18rem)] overflow-y-auto overscroll-contain px-4 py-2">
        {extraQuestions.length > 0 && (
          <div className="mb-3 space-y-1 text-sm leading-relaxed text-on-surface/65">
            {extraQuestions.map((question) => <p key={question}>{question}</p>)}
          </div>
        )}
        {currentGroup && (
          <fieldset disabled={isLoading} className="min-w-0">
            <legend className={currentGroup.title === heading ? "sr-only" : "mb-3 text-sm font-medium text-on-surface"}>{currentGroup.title}</legend>
            <div className="space-y-1.5">
              {currentGroup.options.map((option, index) => (
                <label
                  key={`${currentGroup.key}-${index}`}
                  className={`flex min-h-10 cursor-pointer items-start gap-3 rounded-xl border px-3 py-2 text-sm leading-5 transition-colors sm:min-h-9 ${currentAnswer?.selected === index ? "border-primary/40 bg-primary/5" : "border-outline/10 hover:bg-surface-container-low"} ${isLoading ? "cursor-not-allowed opacity-50" : ""}`}
                >
                  <input
                    type="radio"
                    name={`${panelId}-${currentGroup.key}`}
                    checked={currentAnswer?.selected === index}
                    onChange={() => updateAnswer({ selected: index })}
                    className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
                  />
                  <span className="min-w-0 break-words">{option.label}</span>
                </label>
              ))}
              <div className={`rounded-xl border px-3 py-2 transition-colors ${currentAnswer?.selected === "other" ? "border-primary/40 bg-primary/5" : "border-outline/10"} ${isLoading ? "opacity-50" : ""}`}>
                <label className="flex cursor-pointer items-center gap-3 text-sm font-medium">
                  <input
                    type="radio"
                    name={`${panelId}-${currentGroup.key}`}
                    checked={currentAnswer?.selected === "other"}
                    onChange={() => updateAnswer({ selected: "other" })}
                    className="h-4 w-4 shrink-0 accent-primary"
                  />
                  其他
                </label>
                <textarea
                  key={currentGroup.key}
                  value={currentAnswer?.other ?? ""}
                  onFocus={() => updateAnswer({ selected: "other" })}
                  onChange={(event) => updateAnswer({ selected: "other", other: event.target.value })}
                  onKeyDown={handleOtherKeyDown}
                  tabIndex={currentAnswer?.selected === "other" ? 0 : -1}
                  aria-label="其他補充"
                  aria-invalid={Boolean(validationError)}
                  aria-describedby={`${panelId}-status`}
                  placeholder="也可以用自己的話回答…"
                  rows={1}
                  className="mt-2 max-h-24 w-full resize-y rounded-lg border border-outline/15 bg-white px-3 py-2 text-sm leading-5 text-on-surface placeholder:text-on-surface/35 focus:border-primary/40 focus:outline-2 focus:outline-primary/15 disabled:cursor-not-allowed"
                />
              </div>
            </div>
          </fieldset>
        )}
        {resources.length > 0 && <div className={currentGroup ? "mt-3 border-t border-primary/10 pt-3" : ""}><ActionButtons actions={resources} /></div>}
      </div>
      {currentGroup && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-primary/10 px-4 py-2">
          <div>
            {groups.length > 1 && (
              <nav aria-label="切換問題" className="-ml-2 flex items-center">
                <button type="button" onClick={() => setCurrentIndex(groupIndex - 1)} disabled={isLoading || groupIndex === 0} className={navigationClassName}>
                  <span aria-hidden="true"><MaterialIcon icon="chevron_left" size={16} /></span>上一題
                </button>
                <button type="button" onClick={() => setCurrentIndex(groupIndex + 1)} disabled={isLoading || groupIndex === groups.length - 1} className={navigationClassName}>
                  下一題<span aria-hidden="true"><MaterialIcon icon="chevron_right" size={16} /></span>
                </button>
              </nav>
            )}
            <p id={`${panelId}-status`} role={statusError ? "alert" : undefined} className={`text-[11px] ${statusError ? "text-error" : "text-on-surface/45"}`}>
              {statusError ?? (groups.length > 1 ? `已填 ${groups.filter((group) => getAnswer(group).trim()).length} / ${groups.length} 題` : "選好後送出，也可以自由補充")}
            </p>
          </div>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="inline-flex min-h-10 shrink-0 items-center gap-2 rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-primary/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            送出回覆<span aria-hidden="true"><MaterialIcon icon="arrow_upward" size={16} /></span>
          </button>
        </div>
      )}
    </section>
  );
}
