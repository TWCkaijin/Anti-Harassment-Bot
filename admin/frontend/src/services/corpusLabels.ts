import type { CorpusKind } from "./corpus";

/** Display labels for corpus kinds and source/job states. */
export const KIND_LABELS: Record<CorpusKind, string> = {
  law: "法規",
  judgment: "判決",
  remedy: "救濟與資源",
};

export const STATE_LABELS: Record<string, string> = {
  active: "可檢索",
  queued: "排隊中",
  running: "處理中",
  succeeded: "完成",
  completed: "完成",
  failed: "失敗",
  cancelled: "已取消",
  needs_review: "需要檢查",
  deleting: "刪除中",
  purge_pending: "已移出檢索，檔案清理中",
  deleted: "已刪除",
  legacy: "既有資料",
};
