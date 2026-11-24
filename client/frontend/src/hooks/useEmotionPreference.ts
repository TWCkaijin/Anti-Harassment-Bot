import { parseFlag, usePersistentPreference } from "./usePersistentPreference";

export function useEmotionPreference() {
  const [showEmotions, updateShowEmotions, preferenceSaveFailed] = usePersistentPreference("harass_bot_show_emotions", parseFlag);
  return { showEmotions, updateShowEmotions, preferenceSaveFailed };
}
