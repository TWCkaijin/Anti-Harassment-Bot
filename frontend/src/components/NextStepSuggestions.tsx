import { useRef, useState } from "react";

import type { ActionOption } from "../services/api";

interface NextStepSuggestionsProps {
  suggestions: ActionOption[];
  isLoading?: boolean;
  onSend: (message: string) => void;
}

/** Suggested next steps send a plain message; they do not answer an AI question. */
export default function NextStepSuggestions({ suggestions, isLoading, onSend }: NextStepSuggestionsProps) {
  const submitted = useRef(false);
  const [sent, setSent] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const chooseSuggestion = (value: string) => {
    if (isLoading || submitted.current) return;
    submitted.current = true;
    setSendError(null);
    try {
      onSend(value);
    } catch {
      submitted.current = false;
      setSendError("建議未能送出，請再試一次。");
      return;
    }
    setSent(true);
  };

  if (suggestions.length === 0) return null;

  return (
    <section aria-label="下一步建議" className="mb-2 min-w-0">
      <div className="flex flex-nowrap gap-2 overflow-x-auto overscroll-x-contain py-1">
        {suggestions.map((suggestion, index) => (
          <button
            key={`${index}-${suggestion.value}`}
            type="button"
            onClick={() => chooseSuggestion(suggestion.value)}
            disabled={isLoading || sent}
            className="shrink-0 whitespace-nowrap rounded-full border border-primary/20 bg-white px-3 py-2 text-xs font-medium text-primary transition-colors hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            {suggestion.label}
          </button>
        ))}
      </div>
      {sendError && <p role="alert" className="mt-1 text-xs text-error">{sendError}</p>}
    </section>
  );
}
