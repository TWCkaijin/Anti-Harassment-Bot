import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PrivacyReviewDialog from "./PrivacyReviewDialog";
import { approvePrivacyDraft, createPrivacyDraft, reviewedRequestBody, type PrivacyDraft } from "../services/clientPrivacy";

function draft() {
  return createPrivacyDraft({
    message: "請協助 alice@example.test 與甲公司",
    history: [{ role: "user", content: "舊問題 bob@example.test 與甲公司" }],
    use_rag: false,
    contract_version: 4,
    case_context: { schema_version: 3, revision: 0, facts: {}, summary: "甲公司在場", summary_origin: "user" },
  });
}

describe("outbound privacy review dialog", () => {
  it("shows and edits every text field, applies a local hidden term, then approves that exact payload", () => {
    const confirm = vi.fn<(value: PrivacyDraft) => void>();
    render(<PrivacyReviewDialog initialDraft={draft()} onConfirm={confirm} onCancel={vi.fn()} />);
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("textbox", { name: "本次訊息" })).toHaveValue("請協助 [電子郵件] 與甲公司");
    expect(screen.getByRole("textbox", { name: "歷史 1・使用者" })).toHaveValue("舊問題 [電子郵件] 與甲公司");
    expect(screen.getByRole("textbox", { name: "本次共同摘要" })).toHaveValue("甲公司在場");
    fireEvent.change(screen.getByRole("textbox", { name: "本次共同摘要" }), { target: { value: "甲公司與目擊者在場" } });
    fireEvent.change(screen.getByRole("textbox", { name: "額外隱藏詞（每行一個）" }), { target: { value: "甲公司" } });
    expect(screen.getByRole("button", { name: "確認並送出" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "套用至全部欄位" }));
    expect(screen.getByRole("textbox", { name: "本次訊息" })).toHaveValue("請協助 [電子郵件] 與[已隱藏資訊1]");
    expect(screen.getByRole("textbox", { name: "歷史 1・使用者" })).toHaveValue("舊問題 [電子郵件] 與[已隱藏資訊1]");
    expect(screen.getByRole("textbox", { name: "本次共同摘要" })).toHaveValue("[已隱藏資訊1]與目擊者在場");
    fireEvent.click(screen.getByRole("button", { name: "確認並送出" }));
    expect(confirm).toHaveBeenCalledOnce();
    const approved = confirm.mock.calls[0][0];
    const request = approvePrivacyDraft(approved);
    expect(reviewedRequestBody(request)).toBe(JSON.stringify(approved.request));
    expect(reviewedRequestBody(request)).not.toMatch(/alice@|bob@|甲公司/);
  });

  it("keeps keyboard focus inside review and returns it to the previous control on cancel", () => {
    const trigger = document.createElement("button");
    trigger.textContent = "open";
    document.body.append(trigger);
    trigger.focus();
    const cancel = vi.fn();
    const { unmount } = render(<PrivacyReviewDialog initialDraft={draft()} onConfirm={vi.fn()} onCancel={cancel} />);
    expect(screen.getByRole("textbox", { name: "本次訊息" })).toHaveFocus();
    const last = screen.getByRole("button", { name: "確認並送出" });
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(screen.getByRole("textbox", { name: "額外隱藏詞（每行一個）" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(cancel).toHaveBeenCalledOnce();
    unmount();
    expect(trigger).toHaveFocus();
    trigger.remove();
  });
});
