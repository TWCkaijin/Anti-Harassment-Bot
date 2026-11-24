import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { downloadBlob, errorMessage } from "./browser";

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it("prefers API detail, then Error message, then the fallback", () => {
  expect(errorMessage(new ApiError(409, "Conflict", "版本已變更"), "x")).toBe("版本已變更");
  expect(errorMessage(new ApiError(500, "Server error"), "x")).toBe("Server error");
  expect(errorMessage(new Error("壞掉了"), "x")).toBe("壞掉了");
  expect(errorMessage("plain", "預設訊息")).toBe("預設訊息");
});

it("downloads through a temporary link and revokes the URL afterwards", async () => {
  vi.useFakeTimers();
  const create = vi.fn((blob: Blob) => { void blob; return "blob:test"; });
  const revoke = vi.fn();
  Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.download).toBe("report.json");
    expect(this.isConnected).toBe(true);
  });
  downloadBlob("{}", "report.json", "application/json");
  expect(click).toHaveBeenCalledOnce();
  expect(create.mock.calls[0][0].type).toBe("application/json");
  expect(document.querySelector("a")).toBeNull();
  expect(revoke).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1000);
  expect(revoke).toHaveBeenCalledWith("blob:test");
});
