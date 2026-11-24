import { useState } from "react";

/**
 * A preference kept in localStorage. Storage can be unavailable (private mode,
 * blocked site data), so reads fall back to the default and a failed write is
 * reported instead of thrown; the in-memory value still changes.
 */
export function usePersistentPreference<T>(
  key: string,
  parse: (saved: string | null) => T,
  serialize: (value: T) => string = String,
) {
  const [value, setValue] = useState<T>(() => {
    try { return parse(localStorage.getItem(key)); }
    catch { return parse(null); }
  });
  const [saveFailed, setSaveFailed] = useState(false);

  const update = (next: T) => {
    setValue(next);
    try {
      localStorage.setItem(key, serialize(next));
      setSaveFailed(false);
    } catch {
      setSaveFailed(true);
    }
  };

  return [value, update, saveFailed] as const;
}

export const parseFlag = (saved: string | null) => saved === "true";
