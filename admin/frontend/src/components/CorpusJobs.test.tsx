import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CorpusJobs from "./CorpusJobs";
import * as corpus from "../services/corpus";

vi.mock("../services/corpus", () => ({ listCorpusJobs: vi.fn(), getCorpusJob: vi.fn(), retryCorpusJob: vi.fn(), cancelCorpusJob: vi.fn() }));
const pending: corpus.CorpusJob = { job_id: "queued-job", kind: "corpus_import", status: "queued", created_at: "2026-10-10T00:00:00Z", import_id: "draft-1", progress: { completed_chunks: 2, total_chunks: 5 } };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(corpus.listCorpusJobs).mockResolvedValue({ items: [pending], next_cursor: null });
  vi.mocked(corpus.getCorpusJob).mockResolvedValue(pending);
  vi.mocked(corpus.retryCorpusJob).mockResolvedValue(pending);
  vi.mocked(corpus.cancelCorpusJob).mockResolvedValue({ ...pending, status: "cancelled" });
});

describe("durable corpus jobs", () => {
  it("recovers the pending server job without browser storage and can cancel it", async () => {
    const opened = vi.fn(), completed = vi.fn();
    render(<CorpusJobs csrfToken="csrf" refreshKey={0} onCompleted={completed} onOpenImport={opened} />);
    expect(await screen.findByRole("button", { name: "取消工作" })).toBeEnabled();
    expect(screen.getByText(/completed_chunks/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "開啟匯入草稿" }));
    expect(opened).toHaveBeenCalledWith("draft-1");
    vi.mocked(corpus.getCorpusJob).mockResolvedValue({ ...pending, status: "cancelled" });
    fireEvent.click(screen.getByRole("button", { name: "取消工作" }));
    await waitFor(() => expect(corpus.cancelCorpusJob).toHaveBeenCalledExactlyOnceWith("queued-job", "csrf"));
    await waitFor(() => expect(completed).toHaveBeenCalledWith(expect.objectContaining({ status: "cancelled" })));
  });
  it("requires duplicate-cost acknowledgment only for an uncertain external call", async () => {
    const review: corpus.CorpusJob = { ...pending, status: "needs_review", error: { detail: "外部請求結果不明" } };
    vi.mocked(corpus.listCorpusJobs).mockResolvedValue({ items: [review], next_cursor: null });
    vi.mocked(corpus.getCorpusJob).mockResolvedValue(review);
    render(<CorpusJobs csrfToken="csrf" refreshKey={0} onCompleted={vi.fn()} onOpenImport={vi.fn()} />);
    const retry = await screen.findByRole("button", { name: "重試工作" });
    expect(retry).toBeDisabled();
    expect(corpus.retryCorpusJob).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /重試可能產生重複費用/ }));
    vi.mocked(corpus.getCorpusJob).mockResolvedValue(pending);
    fireEvent.click(retry);
    await waitFor(() => expect(corpus.retryCorpusJob).toHaveBeenCalledExactlyOnceWith("queued-job", true, "csrf"));
  });
  it("paginates history and restarts authenticated polling after reauthentication", async () => {
    vi.mocked(corpus.listCorpusJobs).mockResolvedValueOnce({ items: [pending], next_cursor: "older-page" }).mockResolvedValue({ items: [{ ...pending, job_id: "older-job", status: "failed" }], next_cursor: null });
    const completed = vi.fn(), opened = vi.fn();
    const view = render(<CorpusJobs csrfToken="old-csrf" refreshKey={0} onCompleted={completed} onOpenImport={opened} />);
    fireEvent.click(await screen.findByRole("button", { name: "下一頁工作" }));
    await waitFor(() => expect(corpus.listCorpusJobs).toHaveBeenCalledWith("older-page", expect.any(AbortSignal)));
    await waitFor(() => expect(corpus.getCorpusJob).toHaveBeenCalledWith("older-job", expect.any(AbortSignal)));
    const before = vi.mocked(corpus.getCorpusJob).mock.calls.length;
    view.rerender(<CorpusJobs csrfToken="new-csrf" refreshKey={0} onCompleted={completed} onOpenImport={opened} />);
    await waitFor(() => expect(corpus.getCorpusJob).toHaveBeenCalledTimes(before + 1));
  });
});
