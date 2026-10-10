import type { RagInfo } from "../services/api";
import { useI18n } from "../i18n";
import MaterialIcon from "./MaterialIcon";

export default function RetrievalSources({ retrieval }: { retrieval?: RagInfo["retrieval"] }) {
  const { t } = useI18n();
  if (!retrieval || !["ok", "empty", "failed"].includes(retrieval.status) || !retrieval.data_types.length) return null;
  const labels = { law: t.retrievalLaw, judgment: t.retrievalJudgment, remedy: t.retrievalRemedy };
  if (retrieval.status === "failed") return <p className="text-xs text-on-surface/65">{t.retrievalFailed}：{retrieval.data_types.map(type => labels[type]).join("、")}</p>;
  return <section aria-label={t.retrievalSources} className="space-y-2">
    <div className="flex flex-wrap items-start gap-2">
      {retrieval.data_types.map(type => {
        const sources = retrieval.sources.filter(source => typeof source === "string" ? type === "law" : source.type === type);
        return <details key={type} className="min-w-0 max-w-full rounded-xl border border-primary/15 bg-primary/5 text-xs">
          <summary className="flex cursor-pointer items-center gap-1.5 px-3 py-2 font-semibold text-primary">
            <MaterialIcon icon={type === "law" ? "menu_book" : type === "judgment" ? "gavel" : "support_agent"} size={14} />
            <span>{t.retrievalCompleted}{labels[type]}</span>
            <span className="tabular-nums opacity-70">{sources.length}</span>
            <MaterialIcon icon="expand_more" size={14} />
          </summary>
          {sources.length ? <ul className="max-h-52 space-y-2 overflow-y-auto border-t border-primary/10 p-3">
            {sources.map((source, index) => <li key={index} className="break-words leading-relaxed">
              <span>{typeof source === "string" ? source : source.label}</span>
              {typeof source !== "string" && <>
                {source.article && <p className="mt-1 whitespace-pre-wrap">{source.article}</p>}
                {source.source_url && /^https?:\/\//i.test(source.source_url) && <a href={source.source_url} target="_blank" rel="noreferrer" className="ml-2 text-primary underline">{t.answerBasis}</a>}
              </>}
            </li>)}
          </ul> : <p className="border-t border-primary/10 p-3 text-on-surface/65">{t.retrievalEmpty}</p>}
        </details>;
      })}
    </div>
    <p className="text-[11px] text-on-surface/55">{t.retrievalCaption}</p>
  </section>;
}
