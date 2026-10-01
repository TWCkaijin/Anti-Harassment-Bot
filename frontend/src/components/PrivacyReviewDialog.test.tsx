import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PrivacyReviewDialog from "./PrivacyReviewDialog";
import { cancelPrivacyReview, confirmPrivacyReview, getPrivacyReview, requestPrivacyReview } from "../services/privacyReview";
import { reviewedRequestBody } from "../services/clientPrivacy";
import type { ChatRequest } from "../services/api";
import { emptySummaryContext } from "../services/caseFacts";

const request = (): ChatRequest => ({ message: "聯絡 alice@example.com，合成甲公司", history: [{ role: "user", content: "合成甲公司 0912-345-678" }], use_rag: true, contract_version: 4, case_context: { ...emptySummaryContext(), summary: "合成甲公司，合成乙名稱" } });
afterEach(() => { while (getPrivacyReview()) cancelPrivacyReview(getPrivacyReview()!.id); vi.restoreAllMocks(); });

describe("local privacy review dialog", () => {
  it("previews the original image and sends it only after confirming the current draft", async () => {
    const image = "data:image/png;base64,iVBORw0KGgo=";
    render(<PrivacyReviewDialog />);
    let pending!: Promise<ChatRequest | null>;
    act(() => { pending = requestPrivacyReview({ ...request(), image_base64: image }); });
    expect(screen.getByRole("img", { name: "本次即將送出的圖片" })).toHaveAttribute("src", image);
    expect(screen.getByText(/圖片將以原圖傳送/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "確認並送出" }));
    expect(JSON.parse(reviewedRequestBody((await pending)!)).image_base64).toBe(image);
  });

  it("requires explicit confirmation, previews all fields, reapplies hidden terms and returns the exact approved snapshot", async () => {
    const network = vi.spyOn(globalThis, "fetch");
    const write = vi.spyOn(Storage.prototype, "setItem");
    render(<PrivacyReviewDialog />);
    let pending!: Promise<ChatRequest | null>;
    act(() => { pending = requestPrivacyReview(request()); });
    expect(screen.getByRole("dialog", { name: "確認即將送出的內容" })).toBeVisible();
    expect(screen.getByLabelText("本次訊息")).toHaveValue("聯絡 [電子郵件]，合成甲公司");
    expect(screen.getByLabelText("歷史 1・使用者")).toHaveValue("合成甲公司 [手機號碼]");
    expect(screen.getByLabelText("本次共同摘要")).toHaveValue("合成甲公司，合成乙名稱");
    expect(() => reviewedRequestBody(getPrivacyReview()!.draft.request)).toThrow();
    const extra = screen.getByLabelText("另外需要隱藏的文字（每行一項）");
    fireEvent.change(extra, { target: { value: "合成甲公司" } });
    expect(screen.getByRole("button", { name: "確認並送出" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "套用額外遮蔽" }));
    fireEvent.change(extra, { target: { value: "合成乙名稱" } });
    fireEvent.click(screen.getByRole("button", { name: "套用額外遮蔽" }));
    // Editing a field must retain all earlier hidden terms, not only the last one.
    fireEvent.change(screen.getByLabelText("本次訊息"), { target: { value: "合成甲公司 合成乙名稱，請問如何處理？" } });
    expect(screen.getByLabelText("本次訊息")).toHaveValue("[已隱藏資訊1] [已隱藏資訊2]，請問如何處理？");
    fireEvent.click(screen.getByRole("button", { name: "確認並送出" }));
    const approved = await pending;
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(approved).not.toBeNull();
    const body = reviewedRequestBody(approved!);
    expect(body).not.toMatch(/alice@example\.com|0912-345-678|合成甲公司|合成乙名稱/);
    expect(Object.isFrozen(approved)).toBe(true);
    expect(() => reviewedRequestBody(structuredClone(approved!))).toThrow();
    expect(network).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
  });

  it("keeps an invalid edited draft open and permits correction", async () => {
    render(<PrivacyReviewDialog />);
    let pending!: Promise<ChatRequest | null>;
    act(() => { pending = requestPrivacyReview(request()); });
    fireEvent.change(screen.getByLabelText("本次訊息"), { target: { value: " " } });
    fireEvent.click(screen.getByRole("button", { name: "確認並送出" }));
    expect(screen.getByRole("alert")).toHaveTextContent("不為空白");
    expect(screen.getByRole("dialog")).toBeVisible();
    fireEvent.change(screen.getByLabelText("本次訊息"), { target: { value: "修正後的問題" } });
    fireEvent.click(screen.getByRole("button", { name: "確認並送出" }));
    expect((await pending)?.message).toBe("修正後的問題");
  });

  it("traps focus and Escape cancels without approval, restoring the triggering control", async () => {
    render(<><button>原本的輸入</button><PrivacyReviewDialog /></>);
    const origin = screen.getByRole("button", { name: "原本的輸入" });
    origin.focus();
    let pending!: Promise<ChatRequest | null>;
    act(() => { pending = requestPrivacyReview(request()); });
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "確認並送出" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    expect(screen.getByRole("button", { name: "取消送出" })).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(await pending).toBeNull();
    expect(origin).toHaveFocus();
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("aborts only the owned review and ignores stale confirmations while queued reviews advance", async () => {
    render(<PrivacyReviewDialog />);
    const firstController = new AbortController();
    let first!: Promise<ChatRequest | null>, second!: Promise<ChatRequest | null>;
    act(() => { first = requestPrivacyReview(request(), firstController.signal); second = requestPrivacyReview({ ...request(), message: "第二個問題" }); });
    const firstId = getPrivacyReview()!.id;
    act(() => { firstController.abort(); });
    expect(await first).toBeNull();
    expect(screen.getByLabelText("本次訊息")).toHaveValue("第二個問題");
    expect(confirmPrivacyReview(firstId)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(await second).toBeNull();
    expect(getPrivacyReview()).toBeNull();
  });
});
