import { useId, useState } from "react";

import { useI18n } from "../i18n";
import type { ProcessingTrace as ProcessingTraceData } from "../services/processingTrace";
import type { AnalysisEntry } from "../services/analysis";

interface ProcessingTraceProps {
  trace: ProcessingTraceData;
  analysis?: AnalysisEntry[];
  reasoning?: { text: string; kind: "text" | "summary"; stage: "understanding" | "answer" }[];
}

/** Provider explanations are labelled separately from observed processing. */
export default function ProcessingTrace({ trace, analysis = [], reasoning = [] }: ProcessingTraceProps) {
  const { t } = useI18n();
  const panelId = useId();
  const [disclosure, setDisclosure] = useState({ outcome: trace.outcome, expanded: trace.outcome !== "complete" });
  // Only an outcome transition resets the disclosure. New running events respect
  // the user's choice, while a completed response always starts collapsed.
  const expanded = disclosure.outcome === trace.outcome ? disclosure.expanded : trace.outcome !== "complete";
  const running = trace.outcome === "running";
  const interrupted = trace.outcome === "error" || trace.outcome === "cancelled";
  const title = analysis.length > 0 ? t.analysisTitle : t.processingTraceTitle;

  return (
    <div className="min-w-0 rounded-xl border border-outline/10 bg-surface-container-low/60 px-3 py-2 text-on-surface/70">
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-2 rounded-md py-1 text-left text-xs focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary"
        aria-label={title}
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
        <span className="font-semibold">{title}</span>
        <span role="status" className={`ml-auto ${interrupted ? "text-on-surface/75" : "text-on-surface/55"}`}>
          {t.processingOutcomes[trace.outcome]}
        </span>
      </button>
      <div id={panelId} hidden={!expanded}>
        {reasoning.length > 0 && <section aria-label={t.reasoningTitle} className="my-3 rounded-lg border border-outline/10 bg-white/70 p-3">
          <p className="text-xs font-semibold text-on-surface/75">{t.reasoningTitle}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-on-surface/65">{t.reasoningCaption}</p>
          <div className="mt-2 max-h-60 overflow-y-auto whitespace-pre-wrap break-words text-xs italic leading-relaxed text-on-surface/75">
            {reasoning.map((entry, index) => <span key={index}>{index > 0 && reasoning[index - 1].stage !== entry.stage ? "\n\n" : ""}{entry.text}</span>)}
          </div>
        </section>}
        <p className="mt-2 text-[11px] leading-relaxed text-on-surface/55">{analysis.length ? t.analysisCaption : t.processingTraceCaption}</p>
        {analysis.length > 0 ? <ol aria-label={t.analysisSteps} className="mt-3 max-h-72 space-y-3 overflow-y-auto pr-1 text-xs">
          {analysis.map((entry, index) => <li key={`${entry.stage}-${index}`} className="space-y-1.5 rounded-lg border border-outline/10 bg-white/70 p-3">
            <p className="font-semibold text-on-surface">{t.analysisStages[entry.stage]}</p>
            <p className="leading-relaxed">{entry.summary}</p>
            {entry.facts.length > 0 && <p className="leading-relaxed"><span className="font-medium">{entry.stage === "answer" ? t.analysisAnswerHighlights : t.analysisFacts}：</span>{entry.facts.join("；")}</p>}
            {entry.source_labels.length > 0 && <p className="leading-relaxed"><span className="font-medium">{t.analysisSources}：</span>{entry.source_labels.join("；")}</p>}
            {entry.limitations.length > 0 && <p className="leading-relaxed text-on-surface/65"><span className="font-medium">{t.analysisLimitations}：</span>{entry.limitations.join("；")}</p>}
          </li>)}
        </ol> : trace.steps.length > 0 && (
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
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
