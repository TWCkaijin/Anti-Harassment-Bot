import { useState } from "react";
import { useI18n } from "../i18n";
import { FACT_KEYS, MAX_FACT_VALUE_LENGTH, parseFact, type CaseContext, type FactUpdate, type FactKey } from "../services/caseFacts";

interface Props { context: CaseContext; pending: FactUpdate[]; onSave: (context: CaseContext, answerAgain?: boolean) => void; onStartEdit?: () => void; canRegenerate?: boolean }
export default function CaseSummary({ context, pending, onSave, onStartEdit, canRegenerate = false }: Props) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(context);
  const setFact = (key: FactKey, status: string, value?: string) => setDraft(previous => {
    const facts = { ...previous.facts };
    if (status === "missing") delete facts[key];
    else facts[key] = status === "provided" ? { status, value: value ?? "" } : { status: status as "unknown" | "declined" };
    return { ...previous, facts };
  });
  const invalid = Object.values(draft.facts).some(fact => !parseFact(fact));
  const statusLabel = (status: string) => status === "unknown" ? t.caseFactUnknown : t.caseFactDeclined;
  const save = (again: boolean) => { if (!invalid) { onSave(draft, again); setEditing(false); } };
  return <details className="border-b border-outline/15 bg-white px-6 py-3 text-sm lg:px-10">
    <summary className="cursor-pointer font-semibold text-on-surface">{t.caseSummary}{pending.length > 0 ? ` (${pending.length})` : ""}</summary>
    <p className="my-2 text-xs leading-relaxed text-on-surface/65">{t.caseSummaryNote}</p>
    {pending.length > 0 && <div className="my-3 space-y-2">
      <p className="font-medium">{t.caseFactProposed}</p>
      {pending.map((proposal, index) => <div key={`${proposal.fact_key}-${index}`} className="flex flex-wrap items-center gap-2 text-xs">
        <span>{t.caseFactLabels[proposal.fact_key]}: {proposal.value ?? statusLabel(proposal.status)}</span>
        <button type="button" className="rounded border border-outline/20 px-2 py-1 text-primary" onClick={() => {
          onStartEdit?.(); setDraft({ ...context, facts: { ...context.facts, [proposal.fact_key]: parseFact(proposal)! } }); setEditing(true);
        }}>{t.caseFactUseProposal}</button>
      </div>)}
    </div>}
    {editing ? <div className="space-y-3">
      <div className="max-h-72 space-y-3 overflow-y-auto">
        {FACT_KEYS.map(key => <div key={key} className="flex flex-wrap items-center gap-2">
          <label htmlFor={`fact-${key}`} className="w-36 text-xs">{t.caseFactLabels[key]}</label>
          <select id={`fact-${key}`} className="rounded border border-outline/20 bg-white p-2 text-xs" value={draft.facts[key]?.status ?? "missing"} onChange={event => setFact(key, event.target.value, draft.facts[key]?.value)}>
            <option value="missing">{t.caseFactNotAsked}</option><option value="provided">{t.caseFactProvided}</option><option value="unknown">{t.caseFactUnknown}</option><option value="declined">{t.caseFactDeclined}</option>
          </select>
          {draft.facts[key]?.status === "provided" && <input aria-label={`${t.caseFactLabels[key]} ${t.caseFactValue}`} maxLength={MAX_FACT_VALUE_LENGTH} value={draft.facts[key]?.value ?? ""} onChange={event => setFact(key, "provided", event.target.value)} className="min-w-0 flex-1 rounded border border-outline/20 p-2 text-xs" />}
        </div>)}
      </div>
      {invalid && <p role="alert" className="text-xs text-error">{t.caseFactInvalid}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={invalid} onClick={() => save(false)} className="rounded border border-primary/30 px-3 py-2 text-xs text-primary disabled:opacity-40">{t.caseSummarySave}</button>
        <button type="button" disabled={invalid || !canRegenerate} onClick={() => save(true)} className="rounded bg-primary px-3 py-2 text-xs text-white disabled:opacity-40">{t.caseSummarySaveAnswer}</button>
        <button type="button" onClick={() => setEditing(false)} className="px-3 py-2 text-xs">{t.cancel}</button>
      </div>
    </div> : <>
      <dl className="my-3 grid gap-2 text-xs sm:grid-cols-2">
        {FACT_KEYS.filter(key => context.facts[key]).map(key => <div key={key}><dt className="text-on-surface/55">{t.caseFactLabels[key]}</dt><dd>{context.facts[key]?.value ?? statusLabel(context.facts[key]!.status)}</dd></div>)}
      </dl>
      {Object.keys(context.facts).length === 0 && <p className="my-2 text-xs text-on-surface/60">{t.caseSummaryEmpty}</p>}
      <button type="button" onClick={() => { onStartEdit?.(); setDraft(context); setEditing(true); }} className="rounded border border-outline/20 px-3 py-2 text-xs text-primary">{t.caseSummaryEdit}</button>
    </>}
  </details>;
}
