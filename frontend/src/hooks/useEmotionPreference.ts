import { useState } from "react";
const KEY = "harass_bot_show_emotions";
export function useEmotionPreference() {
  const [showEmotions, setShowEmotions] = useState(() => {
    try { return localStorage.getItem(KEY) === "true"; } catch { return false; }
  });
  const [preferenceSaveFailed, setPreferenceSaveFailed] = useState(false);
  const updateShowEmotions = (value: boolean) => {
    setShowEmotions(value);
    try { localStorage.setItem(KEY, String(value)); setPreferenceSaveFailed(false); }
    catch { setPreferenceSaveFailed(true); }
  };
  return { showEmotions, updateShowEmotions, preferenceSaveFailed };
}
