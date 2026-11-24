import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import { useEmotionPreference } from "./useEmotionPreference";
beforeEach(() => localStorage.clear());
it("hides emotions by default and persists explicit opt-in", () => {
  const { result, unmount } = renderHook(useEmotionPreference);
  expect(result.current.showEmotions).toBe(false);
  act(() => result.current.updateShowEmotions(true));
  unmount();
  expect(renderHook(useEmotionPreference).result.current.showEmotions).toBe(true);
});
