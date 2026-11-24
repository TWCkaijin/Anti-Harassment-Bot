import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { approvePrivacyDraft, createPrivacyDraft, type PrivacyDraft } from "../services/clientPrivacy";
import type { ChatRequest } from "../services/api";
import { errorMessage } from "@rag/shared/browser";

interface PrivacyReviewDialogProps {
  initialDraft: PrivacyDraft;
  onConfirm: (draft: PrivacyDraft) => void;
  onCancel: () => void;
}

function editField(request: ChatRequest, path: string[], value: string): ChatRequest {
  const next = structuredClone(request);
  let parent: unknown = next;
  for (const key of path.slice(0, -1)) {
    if (Array.isArray(parent)) parent = parent[Number(key)];
    else if (parent && typeof parent === "object") parent = (parent as Record<string, unknown>)[key];
    else throw new Error("無法編輯這個欄位，請重新檢查送出內容。");
  }
  const key = path.at(-1);
  if (!key || !parent || typeof parent !== "object") throw new Error("無法編輯這個欄位，請重新檢查送出內容。");
  if (Array.isArray(parent)) parent[Number(key)] = value;
  else (parent as Record<string, unknown>)[key] = value;
  return next;
}

export default function PrivacyReviewDialog({ initialDraft, onConfirm, onCancel }: PrivacyReviewDialogProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [terms, setTerms] = useState("");
  const [appliedTerms, setAppliedTerms] = useState("");
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    firstFieldRef.current?.focus();
    return () => previousFocus?.focus();
  }, []);

  const hiddenTerms = (value: string) => value.split(/\r?\n/).map(term => term.trim()).filter(Boolean);
  const updateField = (path: string[], value: string) => {
    try {
      setDraft(previous => createPrivacyDraft(editField(previous.request, path, value), hiddenTerms(appliedTerms)));
      setError(null);
    } catch (failure) {
      setError(errorMessage(failure, "無法編輯欄位。"));
    }
  };
  const applyTerms = () => {
    try {
      setDraft(previous => createPrivacyDraft(previous.request, hiddenTerms(terms)));
      setAppliedTerms(terms);
      setError(null);
    } catch (failure) {
      setError(errorMessage(failure, "無法套用隱藏詞。"));
    }
  };
  const confirm = () => {
    if (terms !== appliedTerms) return;
    if (draft.request.image_base64) { setError("圖片上傳目前已停用，請改用文字描述。"); return; }
    try {
      // Validate while the person can still correct the complete outbound preview.
      approvePrivacyDraft(draft);
      onConfirm(draft);
    } catch (failure) {
      setError(errorMessage(failure, "請修正送出內容。"));
    }
  };
  const keepFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") { event.preventDefault(); onCancel(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("textarea:not([disabled]), button:not([disabled])") ?? []);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable.at(-1)!;
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3 sm:p-6" onKeyDown={keepFocus}>
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="privacy-review-title" aria-describedby="privacy-review-description" className="flex max-h-[94dvh] w-full max-w-3xl flex-col rounded-2xl bg-white text-on-surface shadow-2xl">
      <div className="shrink-0 border-b border-outline/15 px-5 py-4">
        <h2 id="privacy-review-title" className="text-lg font-bold">送出前檢查隱私內容</h2>
        <p id="privacy-review-description" className="mt-1 text-sm text-on-surface/70">以下是即將送給服務的完整文字。請逐項檢查、直接修改或加入要隱藏的詞，再按確認送出。</p>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
        {draft.findings.length > 0 && <p className="rounded-lg bg-primary/10 px-3 py-2 text-sm" role="status">已依規則遮蔽 {draft.findings.reduce((total, finding) => total + finding.count, 0)} 處內容；請確認仍可辨識出需要隱藏的資訊。</p>}
        <div>
          <label htmlFor="privacy-hidden-terms" className="block text-sm font-semibold">額外隱藏詞（每行一個）</label>
          <textarea id="privacy-hidden-terms" value={terms} onChange={event => setTerms(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-outline/30 p-2 text-sm" placeholder="例如姓名、公司或地址" />
          <button type="button" onClick={applyTerms} className="mt-2 rounded-lg border border-primary px-3 py-2 text-sm text-primary">套用至全部欄位</button>
          {terms !== appliedTerms && <p className="mt-1 text-xs text-on-surface/70">請先套用隱藏詞，再檢查更新後的內容。</p>}
        </div>
        {draft.fields.map((field, index) => <div key={field.path.join("/")}>
          <label htmlFor={`privacy-field-${index}`} className="block text-sm font-semibold">{field.label}</label>
          <textarea
            ref={index === 0 ? firstFieldRef : undefined}
            id={`privacy-field-${index}`}
            value={field.text}
            onChange={event => updateField(field.path, event.target.value)}
            readOnly={field.readOnly}
            rows={Math.min(8, Math.max(2, field.text.split("\n").length))}
            className="mt-1 w-full rounded-lg border border-outline/30 p-2 text-sm read-only:bg-surface-container-low"
          />
        </div>)}
        {draft.warnings.map(warning => <p key={warning} className="text-xs text-on-surface/70">{warning}</p>)}
        {error && <p role="alert" className="text-sm text-error">{error}</p>}
      </div>
      <div className="flex shrink-0 justify-end gap-3 border-t border-outline/15 px-5 py-4">
        <button type="button" onClick={onCancel} className="rounded-lg border border-outline/30 px-4 py-2 text-sm">取消</button>
        <button type="button" onClick={confirm} disabled={terms !== appliedTerms || Boolean(draft.request.image_base64)} className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">確認並送出</button>
      </div>
    </div>
  </div>;
}
