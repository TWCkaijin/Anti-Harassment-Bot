import { useState } from "react";
import { currentBehaviorReview } from "../services/behaviorRecords";
import {
  REVIEW_LABELS,
  type BehaviorRecord,
  type BehaviorReview,
} from "../services/behaviorTests";

const PRIMARY_BUTTON_CLASS =
  "min-h-11 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white disabled:opacity-40";

export default function ReviewForm({
  record,
  disabled,
  onSave,
}: {
  record: BehaviorRecord;
  disabled: boolean;
  onSave: (review: Omit<BehaviorReview, "evidenceSha256" | "reviewedAt">) => Promise<void>;
}) {
  const saved = currentBehaviorReview(record);
  const [verdict, setVerdict] = useState<BehaviorReview["verdict"] | "">(saved?.verdict ?? "");
  const [critical, setCritical] = useState(saved?.critical ?? false);
  const [reason, setReason] = useState(saved?.reason ?? "");
  const [reviewer, setReviewer] = useState(saved?.reviewer ?? "");

  if (record.status !== "completed") {
    return (
      <p className="text-sm text-on-surface/60">完成此案例的所有輪次後，才能做整體人工判分。</p>
    );
  }

  return (
    <fieldset disabled={disabled} className="space-y-3 border-t border-outline/15 pt-4">
      <legend className="text-sm font-semibold">人工覆核</legend>
      {record.review && !saved && (
        <p className="text-sm text-error">腳本、設定或證據已變更，原覆核失效，請重新判分。</p>
      )}
      {saved && (
        <p className="text-sm text-primary">
          已保存：{REVIEW_LABELS[saved.verdict]} · {saved.reviewer} ·{" "}
          {new Date(saved.reviewedAt).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}
        </p>
      )}
      <label className="block text-sm">
        人工結論
        <select
          aria-label="人工結論"
          value={verdict}
          onChange={(event) => {
            setVerdict(event.target.value as typeof verdict);
            if (event.target.value !== "fail") setCritical(false);
          }}
          className="input mt-1"
        >
          <option value="">請選擇</option>
          <option value="pass">通過</option>
          <option value="fail">失敗</option>
          <option value="indeterminate">無法判定</option>
        </select>
      </label>
      <label className="flex min-h-11 items-center gap-3 text-sm">
        <input
          type="checkbox"
          checked={critical}
          disabled={verdict !== "fail"}
          onChange={(event) => setCritical(event.target.checked)}
          className="h-5 w-5 accent-primary"
        />
        重大失敗（限失敗結論）
      </label>
      <label className="block text-sm">
        覆核理由
        <textarea
          aria-label="覆核理由"
          rows={3}
          maxLength={4000}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="input mt-1"
          placeholder="指出對應輪次與回覆證據"
        />
      </label>
      <label className="block text-sm">
        覆核者
        <input
          aria-label="覆核者"
          maxLength={100}
          value={reviewer}
          onChange={(event) => setReviewer(event.target.value)}
          className="input mt-1"
        />
      </label>
      <button
        className={PRIMARY_BUTTON_CLASS}
        disabled={!verdict || !reason.trim() || !reviewer.trim()}
        onClick={() => {
          if (verdict) {
            void onSave({
              verdict,
              critical,
              reason: reason.trim(),
              reviewer: reviewer.trim(),
            });
          }
        }}
      >
        保存人工覆核
      </button>
    </fieldset>
  );
}
