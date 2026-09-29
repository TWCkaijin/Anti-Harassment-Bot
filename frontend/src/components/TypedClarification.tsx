import { useState } from "react";
import { useI18n } from "../i18n";
import type { ReplyContext } from "../hooks/useConversation";
import { MAX_FACT_VALUE_LENGTH, type Clarification, type CaseFact } from "../services/caseFacts";
interface Props { clarification: Clarification; revision: number; isLoading: boolean; onSend: (message: string, context: ReplyContext) => void; onHide: () => void }
export default function TypedClarification({ clarification, revision, isLoading, onSend, onHide }: Props) {
  const { t } = useI18n();
  const [choice, setChoice] = useState("");
  const [other, setOther] = useState("");
  const [sent, setSent] = useState(false);
  const options = clarification.options.filter(option => option.value !== "不確定");
  const valid = choice && (choice !== "other" || (other.trim().length > 0 && other.length <= MAX_FACT_VALUE_LENGTH));
  const submit = () => {
    if (!valid || isLoading || sent) return;
    const fact: CaseFact = choice === "unknown" || choice === "declined" ? { status: choice } : { status: "provided", value: choice === "other" ? other.trim() : options[Number(choice)]?.value };
    const answer = fact.value ?? (fact.status === "unknown" ? t.caseFactUnknown : t.caseFactDeclined);
    setSent(true);
    onSend(`${clarification.question}\n${answer}`, { answers: [{ question: clarification.question, answer }], factsRevision: revision, clarificationAnswer: { question_id: clarification.question_id, fact_key: clarification.fact_key, ...fact } });
  };
  return <section className="my-3 rounded-xl border border-primary/20 bg-white p-4" aria-label={t.clarificationTitle}>
    <p className="mb-1 text-xs text-on-surface/55">{t.clarificationTitle}</p>
    <fieldset disabled={isLoading || sent}>
      <legend className="text-sm font-semibold">{clarification.question}</legend>
      <p className="my-2 text-xs text-on-surface/65">{clarification.reason}</p>
      <div className="space-y-2 text-sm">
        {[...options.map((option, index) => ({ label: option.label, value: String(index) })), { label: t.caseFactUnknown, value: "unknown" }, { label: t.caseFactDeclined, value: "declined" }, { label: t.clarificationOther, value: "other" }].map(option => <label key={option.value} className="flex min-h-9 items-center gap-2">
          <input type="radio" name={clarification.question_id} checked={choice === option.value} onChange={() => setChoice(option.value)} />{option.label}
        </label>)}
        {choice === "other" && <input autoFocus aria-label={t.clarificationOther} value={other} maxLength={MAX_FACT_VALUE_LENGTH} onChange={event => setOther(event.target.value)} className="w-full rounded border border-outline/20 p-2" />}
      </div>
    </fieldset>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
      <button type="button" onClick={onHide} className="text-xs text-on-surface/60">{t.clarificationHide}</button>
      <button type="button" onClick={submit} disabled={!valid || isLoading || sent} className="rounded-lg bg-primary px-3 py-2 text-xs text-white disabled:opacity-40">{t.clarificationSubmit}</button>
    </div>
  </section>;
}
