import { describe, expect, it, vi } from "vitest";
import type { ChatRequest } from "./api";
import { preparePrivateRequest } from "./privateRequest";
import { reviewedRequestBody, type PrivacyDraft } from "./clientPrivacy";

const input = (): ChatRequest => ({ message: "synthetic@example.test", history: [{ role: "assistant", content: "0912345678" }], use_rag: true, contract_version: 4 });

const review = async (draft: PrivacyDraft) => draft;

describe("reviewed local request preparation", () => {
  it("requires review, masks all text and produces a registered immutable request without network", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const reviewer = vi.fn(review);
    try {
      const prepared = await preparePrivateRequest(input(), reviewer);
      expect(reviewer).toHaveBeenCalledOnce();
      expect(Object.isFrozen(prepared)).toBe(true);
      const body = reviewedRequestBody(prepared!);
      expect(body).not.toContain("synthetic@example.test");
      expect(body).not.toContain("0912345678");
      expect(JSON.parse(body)).toMatchObject({ message: "[電子郵件]", history: [{ role: "assistant", content: "[手機號碼]" }] });
      expect(fetch).not.toHaveBeenCalled();
    } finally { fetch.mockRestore(); }
  });
  it("retains independent snapshots when subsequent requests or caller inputs change", async () => {
    const raw = input();
    const first = await preparePrivateRequest(raw, review);
    raw.message = "new@example.test";
    const second = await preparePrivateRequest(raw, review);
    expect(first).not.toBe(second);
    expect(reviewedRequestBody(first!)).not.toContain("new@example.test");
    expect(() => reviewedRequestBody({ ...first! })).toThrow();
  });
  it("blocks invalid images and lengths", async () => {
    await expect(preparePrivateRequest({ ...input(), image_base64: "data:image/png;base64,invalid" }, review)).rejects.toThrow();
    await expect(preparePrivateRequest({ ...input(), message: "字".repeat(2001) }, review)).rejects.toThrow("2,000");
  });
  it("does not register or send a cancelled review", async () => {
    expect(await preparePrivateRequest(input(), async () => null)).toBeNull();
  });
  it.each([true, false])("honors cancellation before completion (already aborted: %s)", async before => {
    const controller = new AbortController();
    if (before) controller.abort();
    const pending = preparePrivateRequest(input(), async draft => { await Promise.resolve(); return draft; }, controller.signal);
    controller.abort();
    await expect(pending).resolves.toBeNull();
  });
});
