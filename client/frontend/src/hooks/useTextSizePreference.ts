import { useLayoutEffect } from "react";
import { usePersistentPreference } from "./usePersistentPreference";

export const TEXT_SIZES = ["small", "medium", "large"] as const;
export type TextSize = (typeof TEXT_SIZES)[number];

const parseTextSize = (saved: string | null): TextSize => TEXT_SIZES.find(size => size === saved) ?? "medium";

export function useTextSizePreference() {
  const [textSize, updateTextSize, textSizeSaveFailed] = usePersistentPreference("harass_bot_text_size", parseTextSize);

  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.chatTextSize = textSize;
    return () => { delete root.dataset.chatTextSize; };
  }, [textSize]);

  return { textSize, updateTextSize, textSizeSaveFailed };
}
