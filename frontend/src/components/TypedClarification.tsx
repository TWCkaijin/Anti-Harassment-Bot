import { useState } from "react";
import { useI18n } from "../i18n";
import type { ReplyContext } from "../hooks/useConversation";
import { factText, isClarificationDraft, MAX_FACT_VALUE_LENGTH, type Clarification, type CaseFact, type ClarificationDraft } from "../services/caseFacts";
import { limitDistinctOptions, MAX_TYPED_PREDEFINED_OPTIONS } from "./choiceOptions";
import HideOptionsButton from "./HideOptionsButton";
import { settleSendOutcome, type SendOutcome } from "../services/sendOutcome";
interface Props { clarification: Clarification; revision: number; isLoading: boolean; onSend: (message: string, context: ReplyContext) => SendOutcome; onHide: () => void; draft?: ClarificationDraft; onDraftChange?: (draft: ClarificationDraft) => void }
export default function TypedClarification({ clarification, revision, isLoading, onSend, onHide, draft: savedDraft, onDraftChange }: Props) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<ClarificationDraft>(() => isClarificationDraft(savedDraft) ? savedDraft : { selectedValues: [], other: "", includeOther: false, special: null });
  const [sent, setSent] = useState(false);
  const reservedChoices = new Set(["unknown", "declined", "other", "不確定", "暫不提供", "自行補充", "其他", t.caseFactUnknown, t.caseFactDeclined, t.clarificationOther]);
  const options = limitDistinctOptions(clarification.options.filter(option => !reservedChoices.has(option.value.trim()) && !reservedChoices.has(option.label.trim())), MAX_TYPED_PREDEFINED_OPTIONS);
  const multiple = clarification.selection_mode === "multiple";
  const maximum = multiple ? Math.min(4, Math.max(2, clarification.max_selections ?? 4)) : 1;
  const selectedValues = draft.selectedValues.filter(value => options.some(option => option.value === value));
  const providedValues = [...selectedValues, ...(draft.includeOther && draft.other.trim() ? [draft.other.trim()] : [])];
  const valid = draft.special !== null || (providedValues.length > 0 && providedValues.length <= maximum
    && new Set(providedValues).size === providedValues.length && (!draft.includeOther || (draft.other.trim().length > 0 && draft.other.length <= MAX_FACT_VALUE_LENGTH)));
  const updateDraft = (next: ClarificationDraft) => { if (!isLoading && !sent) { setDraft(next); onDraftChange?.(next); } };
  const choose = (value: string) => {
    if (value === "unknown" || value === "declined") {
      updateDraft({ ...draft, selectedValues: [], includeOther: false, special: multiple && draft.special === value ? null : value });
    } else if (value === "other") {
      updateDraft({ ...draft, selectedValues: multiple ? selectedValues : [], includeOther: !multiple || !draft.includeOther, special: null });
    } else {
      updateDraft({ ...draft, selectedValues: multiple ? selectedValues.includes(value) ? selectedValues.filter(item => item !== value) : [...selectedValues, value] : [value], includeOther: multiple && draft.includeOther, special: null });
    }
  };
  const choices = [...options, { label: t.caseFactUnknown, value: "unknown" }, { label: t.caseFactDeclined, value: "declined" }, { label: t.clarificationOther, value: "other" }];
  const checked = (value: string) => value === "other" ? draft.includeOther : value === "unknown" || value === "declined" ? draft.special === value : selectedValues.includes(value);
  const submit = () => {
    if (!valid || isLoading || sent) return;
    const fact: CaseFact = draft.special ? { status: draft.special } : { status: "provided", value: multiple ? providedValues : providedValues[0] };
    const answer = fact.status === "provided" ? factText(fact.value) : fact.status === "unknown" ? t.caseFactUnknown : t.caseFactDeclined;
    setSent(true);
    try { settleSendOutcome(onSend(`${clarification.question}\n${answer}`, { answers: [{ question: clarification.question, answer }], factsRevision: revision, clarificationAnswer: { question_id: clarification.question_id, ...(clarification.fact_key ? { fact_key: clarification.fact_key } : {}), ...fact,
      ...(clarification.context_revision !== undefined ? { context_revision: clarification.context_revision } : {}),
      ...(clarification.selection_mode ? { selection_mode: clarification.selection_mode, max_selections: maximum } : {}),
      ...(clarification.context_scope ? { context_scope: clarification.context_scope } : {}),
      ...(clarification.validation_token ? { validation_token: clarification.validation_token } : clarification.selection_mode ? { allowed_values: clarification.options.map(option => option.value) } : {}),
    } }), () => {}, () => setSent(false)); }
    catch { setSent(false); }
  };
  return <section className="my-3 overflow-hidden rounded-2xl border border-primary/20 bg-white shadow-sm" aria-label={t.clarificationTitle}>
    <div className="flex items-center justify-between gap-3 border-b border-primary/10 px-4 py-2">
      <p className="min-w-0 text-xs leading-relaxed text-on-surface/65">{t.clarificationTitle}</p>
      <HideOptionsButton onHide={onHide} label={t.clarificationHide} />
    </div>
    <div className="max-h-[min(34dvh,18rem)] overflow-y-auto overscroll-contain px-4 py-3">
    <fieldset disabled={isLoading || sent} className="min-w-0">
      <legend className="text-sm font-semibold">{clarification.question}</legend>
      <p className="my-2 text-xs text-on-surface/65">{clarification.reason}</p>
      {multiple && <p className="mb-2 text-xs text-on-surface/65">{t.clarificationMultiple(maximum)}</p>}
      <div className="grid grid-cols-1 gap-2 text-sm min-[360px]:grid-cols-2">
        {choices.map(option => <label key={option.value} className={`flex min-h-11 min-w-0 items-center gap-2 rounded-xl border px-3 py-2 leading-5 transition-colors ${checked(option.value) ? "border-primary/40 bg-primary/5" : "border-outline/15 hover:bg-surface-container-low"} ${isLoading || sent ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}>
          <input className="h-4 w-4 shrink-0 accent-primary" type={multiple ? "checkbox" : "radio"} name={clarification.question_id} checked={checked(option.value)} disabled={multiple && !checked(option.value) && !["unknown", "declined"].includes(option.value) && selectedValues.length + Number(draft.includeOther) >= maximum} onChange={() => choose(option.value)} /><span className="min-w-0 break-words">{option.label}</span>
        </label>)}
        {draft.includeOther && <input autoFocus aria-label={t.clarificationOther} value={draft.other} maxLength={MAX_FACT_VALUE_LENGTH} onChange={event => updateDraft({ ...draft, other: event.target.value })} className="min-h-11 w-full min-w-0 rounded-lg border border-outline/20 p-2 min-[360px]:col-span-2" />}
      </div>
    </fieldset>
    </div>
    <div className="flex justify-end border-t border-primary/10 px-4 py-2">
      <button type="button" onClick={submit} disabled={!valid || isLoading || sent} className="min-h-11 rounded-lg bg-primary px-4 py-2 text-xs font-medium text-white disabled:opacity-40">{t.clarificationSubmit}</button>
    </div>
  </section>;
}
