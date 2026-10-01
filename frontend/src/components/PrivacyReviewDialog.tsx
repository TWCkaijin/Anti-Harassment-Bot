import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { addPrivacyHiddenTerms, cancelPrivacyReview, confirmPrivacyReview, editPrivacyReviewField, getPrivacyReview, removePrivacyReviewClarification, subscribePrivacyReview, type PrivacyReviewSession } from "../services/privacyReview";
import MaterialIcon from "./MaterialIcon";

function Review({ session }: { session: PrivacyReviewSession }) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const [hiddenTerms, setHiddenTerms] = useState("");
  const { draft, id } = session;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const scroll = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    return () => {
      document.body.style.overflow = scroll;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return createPortal(<div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/45 p-3 sm:p-6">
    <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={`${titleId}-note`} tabIndex={-1}
      className="flex max-h-[92dvh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-outline/20 bg-white shadow-2xl"
      onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); cancelPrivacyReview(id); }
        if (event.key !== "Tab") return;
        const elements = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled), input:not(:disabled), [tabindex="0"]') ?? []);
        const first = elements[0], last = elements.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first?.focus(); }
      }}>
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-outline/15 px-5 py-4 sm:px-7">
        <h2 id={titleId} className="text-lg font-bold text-on-surface">確認即將送出的內容</h2>
        <button type="button" aria-label="取消送出" onClick={() => cancelPrivacyReview(id)} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-surface-container"><MaterialIcon icon="close" size={20} /></button>
      </header>
      <div className="min-h-0 space-y-5 overflow-y-auto overscroll-contain px-5 py-5 sm:px-7">
        <div id={`${titleId}-note`} className="space-y-2 text-sm leading-relaxed text-on-surface/75">
          <p>以下是本次要送到服務的完整文字，包括新訊息、歷史、情境摘要與追問選項資料。遮蔽在您的瀏覽器內完成，確認前不會送出這份請求。</p>
          <p>本機文字遮蔽可能遺漏姓名、地址或可組合辨識的細節。請逐欄檢查並直接修正；仍有再識別風險。</p>
          <p>本次修改不會批次清除或改寫既有本機紀錄。</p>
        </div>
        <section aria-label="本機處理摘要" className="rounded-xl bg-surface-container-low p-4 text-sm">
          <p className="font-semibold">已處理 {draft.fields.length} 個文字欄位</p>
          <p className="mt-1 text-xs text-on-surface/65">{draft.fields.map(field => field.label).join("、") || "沒有可送出的文字欄位"}</p>
          {draft.findings.length > 0 ? <ul className="mt-2 space-y-1 text-xs">{draft.findings.map((finding, index) => <li key={index}>{finding.type}：{finding.count} 處</li>)}</ul>
            : <p className="mt-2 text-xs text-on-surface/65">未自動偵測到需要遮蔽的項目，仍請確認全部內容。</p>}
        </section>
        {draft.warnings.length > 0 && <ul aria-label="需要注意的內容" className="space-y-2 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">{draft.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        {draft.request.image_base64 && <section aria-label="本次送出的圖片" className="space-y-2 rounded-xl border border-outline/20 p-4">
          <p className="text-sm font-semibold">本次送出的圖片（原圖）</p>
          <img src={draft.request.image_base64} alt="本次即將送出的圖片" className="max-h-64 max-w-full rounded-lg object-contain" />
          <p className="text-xs text-on-surface/65">若要更換或移除圖片，請取消送出後回到輸入框修改。</p>
        </section>}
        <section className="space-y-2 rounded-xl border border-outline/20 p-4">
          <label htmlFor={`${titleId}-hidden`} className="block text-sm font-semibold">另外需要隱藏的文字（每行一項）</label>
          <textarea id={`${titleId}-hidden`} rows={3} value={hiddenTerms} onChange={event => setHiddenTerms(event.target.value)} placeholder="例如姓名、公司名稱或詳細地址" className="w-full rounded-lg border border-outline/25 p-3 text-sm" />
          <p className="text-xs text-on-surface/60">只在本頁處理，不會把這份隱藏清單傳給服務或存入本機紀錄。</p>
          <button type="button" disabled={!hiddenTerms.trim()} onClick={() => { addPrivacyHiddenTerms(id, hiddenTerms.split(/\r?\n/).map(term => term.trim()).filter(Boolean)); setHiddenTerms(""); }} className="min-h-11 rounded-lg border border-primary/30 px-4 text-sm text-primary disabled:opacity-40">套用額外遮蔽</button>
        </section>
        <section aria-label="本次送出的所有文字" className="space-y-4">
          {draft.fields.map((field, index) => <div key={JSON.stringify(field.path)}>
            <label htmlFor={`${titleId}-field-${index}`} className="mb-2 block text-sm font-semibold">{field.label}{field.readOnly ? "（唯讀）" : ""}</label>
            <textarea id={`${titleId}-field-${index}`} value={field.text} readOnly={field.readOnly} rows={field.text.includes("\n") ? 5 : 3}
              onChange={event => editPrivacyReviewField(id, field.path, event.target.value)} className={`w-full resize-y rounded-xl border border-outline/25 p-3 text-sm leading-relaxed focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/15 ${field.readOnly ? "bg-surface-container-low text-on-surface/70" : "bg-white"}`} />
          </div>)}
        </section>
        {draft.request.clarification_answer && <div className="rounded-xl border border-outline/20 p-4 text-sm">
          <p className="mb-3 text-on-surface/70">追問選項資料含簽章，不能直接修改。如果其中仍有識別資訊，請移除選項資料，再修改上方訊息，以一般文字送出。</p>
          <button type="button" onClick={() => removePrivacyReviewClarification(id)} className="min-h-11 rounded-lg border border-primary/30 px-4 text-primary">移除追問選項資料</button>
        </div>}
        {session.error && <p role="alert" className="rounded-xl bg-error-container/30 p-3 text-sm text-error">{session.error}</p>}
      </div>
      <footer className="flex shrink-0 flex-wrap justify-end gap-3 border-t border-outline/15 bg-white px-5 py-4 sm:px-7">
        <button type="button" onClick={() => cancelPrivacyReview(id)} className="min-h-11 rounded-xl border border-outline/25 px-5 text-sm">取消</button>
        <button type="button" disabled={Boolean(hiddenTerms.trim())} title={hiddenTerms.trim() ? "請先套用額外遮蔽" : undefined} onClick={() => confirmPrivacyReview(id)} className="min-h-11 rounded-xl bg-primary px-5 text-sm font-semibold text-white disabled:opacity-40">確認並送出</button>
      </footer>
    </div>
  </div>, document.body);
}

export default function PrivacyReviewDialog() {
  const session = useSyncExternalStore(subscribePrivacyReview, getPrivacyReview, () => null);
  return session ? <Review key={session.id} session={session} /> : null;
}
