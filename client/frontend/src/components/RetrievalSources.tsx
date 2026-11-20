import type { RagInfo } from "../services/api";
import { useI18n } from "../i18n";
import MaterialIcon from "./MaterialIcon";
import { officialCitationUrl } from "./citationLinks";

export default function RetrievalSources({ retrieval }: { retrieval?: RagInfo["retrieval"] }) {
  const { t } = useI18n();
  if (!retrieval || !["ok", "empty", "failed"].includes(retrieval.status) || !retrieval.data_types.length) return null;
  const labels = { law: t.retrievalLaw, judgment: t.retrievalJudgment, remedy: t.retrievalRemedy };
  if (retrieval.status === "failed") return <p className="text-xs text-on-surface/65">{t.retrievalFailed}：{retrieval.data_types.map(type => labels[type]).join("、")}</p>;
  return <section aria-label={t.retrievalSources} className="space-y-2">
    <div className="flex flex-wrap items-start gap-2">
      {retrieval.data_types.map(type => {
        const sources = retrieval.sources.filter(source => typeof source === "string" ? type === "law" : source.type === type);
        return <details key={type} className="group min-w-0 max-w-full overflow-hidden rounded-2xl border border-outline/25 bg-white text-xs open:w-full">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 font-semibold text-primary focus-visible:outline-2 focus-visible:-outline-offset-2 [&::-webkit-details-marker]:hidden">
            <MaterialIcon icon={type === "law" ? "menu_book" : type === "judgment" ? "gavel" : "support_agent"} size={14} />
            <span>{t.retrievalCompleted}{labels[type]}</span>
            <span className="tabular-nums opacity-70">{sources.length}</span>
            <span className="ml-auto transition-transform duration-200 group-open:rotate-180">
              <MaterialIcon icon="expand_more" size={14} />
            </span>
          </summary>
          {sources.length ? <ul className="max-h-72 space-y-3 overflow-y-auto overscroll-contain border-t border-outline/15 bg-surface-container-low px-4 py-4 sm:px-5">
            {sources.map((source, index) => <li key={index} className="min-w-0 break-words rounded-xl border border-outline/20 bg-white px-4 py-3 leading-relaxed">
              <span className="block text-sm font-semibold text-on-surface">{typeof source === "string" ? source : source.label}</span>
              {typeof source !== "string" && <>
                {source.article && <p className="mt-1 whitespace-pre-wrap text-on-surface/80">{source.article}</p>}
                {source.version && <p className="mt-1 text-[0.6875rem] text-on-surface/60">{source.version}</p>}
                {typeof source.promulgated_date === "string" && <p className="mt-1 text-[0.6875rem] text-on-surface/60">{t.sourcePromulgatedDate}：{source.promulgated_date}</p>}
                {typeof source.effective_date === "string" && <p className="mt-1 text-[0.6875rem] text-on-surface/60">{t.sourceEffectiveDate}：{source.effective_date}</p>}
                {typeof source.checked_at === "string" && <p className="mt-1 text-[0.6875rem] text-on-surface/60">{t.sourceCheckedAt}：{source.checked_at}</p>}
                {typeof source.effective_note === "string" && <p className="mt-1 text-[0.6875rem] text-on-surface/60">{source.effective_note}</p>}
                {officialCitationUrl(source.source_url) && <a href={officialCitationUrl(source.source_url) ?? undefined} target="_blank" rel="noreferrer" className="mt-1 inline-block text-primary underline">{t.answerBasis}</a>}
              </>}
            </li>)}
          </ul> : <p className="border-t border-primary/10 p-3 text-on-surface/65">{t.retrievalEmpty}</p>}
        </details>;
      })}
    </div>
    <p className="text-[0.6875rem] text-on-surface/55">{t.retrievalCaption}</p>
  </section>;
}
