import { useLayoutEffect, useState } from "react";

export const TEXT_SIZES = ["small", "medium", "large"] as const;
export type TextSize = (typeof TEXT_SIZES)[number];

const STORAGE_KEY = "harass_bot_text_size";

export function useTextSizePreference() {
  const [textSize, setTextSize] = useState<TextSize>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return TEXT_SIZES.find(size => size === saved) ?? "medium";
    } catch {
      return "medium";
    }
  });
  const [textSizeSaveFailed, setTextSizeSaveFailed] = useState(false);

  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.chatTextSize = textSize;
    return () => { delete root.dataset.chatTextSize; };
  }, [textSize]);

  const updateTextSize = (size: TextSize) => {
    setTextSize(size);
    try {
      localStorage.setItem(STORAGE_KEY, size);
      setTextSizeSaveFailed(false);
    } catch {
      setTextSizeSaveFailed(true);
    }
  };

  return { textSize, updateTextSize, textSizeSaveFailed };
}
