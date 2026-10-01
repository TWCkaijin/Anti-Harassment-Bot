import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import { MAX_SUMMARY_LENGTH, toSummaryText, type SummaryCaseContext, type CaseContext } from "../services/caseFacts";
import MaterialIcon from "./MaterialIcon";

interface Props {
  context: CaseContext;
  onSave: (context: CaseContext, answerAgain?: boolean) => void;
  onStartEdit?: () => void;
  onClose: () => void;
  canRegenerate?: boolean;
}

/** One current conversation summary, not a snapshot of the avatar's reply. */
export default function CaseSummary({ context, onSave, onStartEdit, onClose, canRegenerate = false }: Props) {
  const { t } = useI18n();
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState<SummaryCaseContext | null>(null);
  const editing = draft !== null;
  const summary: SummaryCaseContext = context.schema_version === 3 ? context : {
    schema_version: 3, revision: context.revision, facts: {}, summary: toSummaryText(context), summary_origin: "migration",
  };
  const oversized = (draft ?? summary).summary.length > MAX_SUMMARY_LENGTH;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => {
      const target = previous?.isConnected ? previous : document.querySelector<HTMLButtonElement>("[data-summary-trigger]");
      target?.focus();
    };
  }, []);
  useEffect(() => { if (editing) editor.current?.focus(); }, [editing]);

  const save = (again: boolean) => {
    if (!draft || draft.summary.length > MAX_SUMMARY_LENGTH) return;
    onSave({ ...draft, summary: draft.summary.trim(), summary_origin: "user" }, again);
    onClose();
  };
  return createPortal(<div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/35 p-4" onPointerDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
      className="flex max-h-[85dvh] w-full max-w-xl flex-col overflow-y-auto rounded-3xl border border-outline/15 bg-white p-5 shadow-xl sm:p-7"
      onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); onClose(); }
        if (event.key !== "Tab") return;
        const elements = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea, [tabindex="0"]') ?? []);
        const first = elements[0], last = elements.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first?.focus(); }
      }}>
      <div className="flex items-center justify-between gap-3">
        <h2 id={titleId} className="text-base font-bold text-on-surface">{t.caseSummary}</h2>
        <button type="button" aria-label={t.close} onClick={onClose} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-on-surface/70 hover:bg-surface-container"><MaterialIcon icon="close" size={20} /></button>
      </div>
      <p className="mb-4 text-xs leading-relaxed text-on-surface/65">{t.caseSummaryNote}</p>
      {oversized && <p role="alert" className="mb-4 text-sm text-error">{t.caseSummaryOverflow}</p>}
      {draft ? <>
        <label htmlFor={`${titleId}-text`} className="mb-2 text-sm font-medium">{t.caseSummaryText}</label>
        <textarea ref={editor} id={`${titleId}-text`} rows={9} maxLength={MAX_SUMMARY_LENGTH} value={draft.summary} onChange={event => setDraft({ ...draft, summary: event.target.value })}
          className="min-h-32 w-full resize-y rounded-xl border border-outline/30 p-3 text-sm leading-relaxed text-on-surface outline-none focus:border-primary focus:ring-2 focus:ring-primary/15" />
        <p className="my-2 text-right text-xs text-on-surface/60">{draft.summary.length} / {MAX_SUMMARY_LENGTH}</p>
        <p className="mb-4 text-xs leading-relaxed text-on-surface/70">{t.caseSummaryEditNote}</p>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => setDraft(null)} className="min-h-11 rounded-xl px-4 text-sm text-on-surface">{t.cancel}</button>
          <button type="button" disabled={oversized} onClick={() => save(false)} className="min-h-11 rounded-xl border border-primary/30 px-4 text-sm text-primary disabled:opacity-40">{t.caseSummarySave}</button>
          <button type="button" disabled={!canRegenerate || oversized} onClick={() => save(true)} className="min-h-11 rounded-xl bg-primary px-4 text-sm text-white disabled:opacity-40">{t.caseSummarySaveAnswer}</button>
        </div>
      </> : <>
        <div className="min-h-24 overflow-y-auto whitespace-pre-wrap break-words rounded-xl bg-surface-container-low p-4 text-sm leading-relaxed text-on-surface">{summary.summary || t.caseSummaryEmpty}</div>
        <button type="button" onClick={() => { onStartEdit?.(); setDraft(summary); }} className="mt-4 min-h-11 self-end rounded-xl border border-outline/20 px-4 text-sm text-primary">{t.caseSummaryEdit}</button>
      </>}
    </div>
  </div>, document.body);
}
