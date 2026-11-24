import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { parseFlag, usePersistentPreference } from "./usePersistentPreference";
import { usePrivacyReviewPreference } from "./usePrivacyReviewPreference";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it("reads, parses and persists a value", () => {
  localStorage.setItem("pref", "3");
  const { result } = renderHook(() => usePersistentPreference("pref", saved => Number(saved ?? 1)));
  expect(result.current[0]).toBe(3);
  act(() => result.current[1](5));
  expect(result.current[0]).toBe(5);
  expect(localStorage.getItem("pref")).toBe("5");
  expect(result.current[2]).toBe(false);
});

it("falls back to the default when storage cannot be read", () => {
  vi.spyOn(localStorage, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  const { result } = renderHook(() => usePersistentPreference("pref", parseFlag));
  expect(result.current[0]).toBe(false);
});

it("keeps the new value in memory and reports a failed write", () => {
  vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
  const { result } = renderHook(usePrivacyReviewPreference);
  act(() => result.current.updateConfirmPrivacy(true));
  expect(result.current.confirmPrivacyBeforeSending).toBe(true);
  expect(result.current.privacyReviewSaveFailed).toBe(true);
});
