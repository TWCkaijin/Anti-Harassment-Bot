import { useId, useState } from "react";

import { useI18n } from "../i18n";
import type { ProcessingTrace as ProcessingTraceData } from "../services/processingTrace";

interface ProcessingTraceProps {
  trace: ProcessingTraceData;
}

const seconds = (milliseconds: number) => (Math.max(0, milliseconds) / 1000).toFixed(1);

/** Displays observed processing events, never model reasoning or inferred work. */
export default function ProcessingTrace({ trace }: ProcessingTraceProps) {
  const { t } = useI18n();
  const panelId = useId();
  const [disclosure, setDisclosure] = useState({ outcome: trace.outcome, expanded: trace.outcome !== "complete" });
  // Only an outcome transition resets the disclosure. New running events respect
  // the user's choice, while a completed response always starts collapsed.
  const expanded = disclosure.outcome === trace.outcome ? disclosure.expanded : trace.outcome !== "complete";
  const running = trace.outcome === "running";
  const interrupted = trace.outcome === "error" || trace.outcome === "cancelled";

  return (
    <div className="min-w-0 rounded-xl border border-outline/10 bg-surface-container-low/60 px-3 py-2 text-on-surface/70">
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-2 rounded-md py-1 text-left text-xs focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary"
        aria-label={t.processingTraceTitle}
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={event => {
          const button = event.currentTarget;
          setDisclosure({ outcome: trace.outcome, expanded: !expanded });
          // Expanding a tall answer can move the control above a mobile scroll
          // viewport. Keep the user's chosen panel reachable after layout.
          if (!expanded) requestAnimationFrame(() => {
            if (button.isConnected) button.scrollIntoView?.({ block: "nearest", behavior: "instant" });
          });
        }}
      >
        <span aria-hidden="true" className="flex shrink-0">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" focusable="false" className={`transition-transform ${expanded ? "rotate-90" : ""}`}>
            <path d="m6 4 4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="font-semibold">{t.processingTraceTitle}</span>
        <span role="status" className={`ml-auto ${interrupted ? "text-on-surface/75" : "text-on-surface/55"}`}>
          {t.processingOutcomes[trace.outcome]}
        </span>
        <span className="shrink-0 tabular-nums text-on-surface/50">{t.processingSeconds(seconds(trace.duration_ms))}</span>
      </button>
      <div id={panelId} hidden={!expanded}>
        <p className="mt-2 text-[11px] leading-relaxed text-on-surface/55">{t.processingTraceCaption}</p>
        {trace.steps.length > 0 && (
          <ol aria-label={t.processingTraceSteps} className="mt-3 max-h-60 space-y-2 overflow-y-auto pr-1 text-xs">
            {trace.steps.map((step, index) => {
              const active = running && index === trace.steps.length - 1;
              return (
                <li key={index} className="flex min-w-0 items-start gap-2" aria-current={active ? "step" : undefined}>
                  <span aria-hidden="true" className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${active ? "bg-primary motion-safe:animate-pulse" : "bg-outline/40"}`} />
                  <span className="min-w-0 flex-1 break-words">
                    {t.processingPhases[step.phase]}
                    {step.attempt > 0 && <span className="ml-1.5 text-on-surface/50">{t.processingAttempt(step.attempt + 1)}</span>}
                  </span>
                  <span className="shrink-0 tabular-nums text-on-surface/50">{t.processingSeconds(seconds(step.elapsed_ms))}</span>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
