import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useTextSizePreference } from "./useTextSizePreference";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it("uses medium for invalid preferences and restores a chosen size on remount", () => {
  localStorage.setItem("harass_bot_text_size", "invalid");
  const { result, unmount } = renderHook(useTextSizePreference);
  expect(result.current.textSize).toBe("medium");

  act(() => result.current.updateTextSize("large"));
  expect(document.documentElement).toHaveAttribute("data-chat-text-size", "large");
  unmount();

  expect(renderHook(useTextSizePreference).result.current.textSize).toBe("large");
});

it("applies the size for the current visit when storage rejects writes", () => {
  const { result } = renderHook(useTextSizePreference);
  vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new Error("Storage unavailable"); });

  act(() => result.current.updateTextSize("small"));

  expect(document.documentElement).toHaveAttribute("data-chat-text-size", "small");
  expect(result.current.textSizeSaveFailed).toBe(true);
});
