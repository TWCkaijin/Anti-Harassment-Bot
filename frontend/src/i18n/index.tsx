/* eslint-disable react-refresh/only-export-components */
/**
 * i18n — 簡易國際化系統
 * 使用 React Context 提供語言切換能力。
 * 支援 zh-TW 與 en，預設從 localStorage 讀取使用者偏好。
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useMemo,
  type ReactNode,
} from "react";
import zhTW from "./locales/zh-TW";
import en from "./locales/en";

export type Locale = "zh-TW" | "en";
export type Translations = typeof zhTW;

const locales: Record<Locale, Translations> = {
  "zh-TW": zhTW,
  en: en as unknown as Translations,
};

const LOCALE_STORAGE_KEY = "harass_bot_locale";

function getInitialLocale(): Locale {
  try {
    const saved = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (saved === "en" || saved === "zh-TW") return saved;
  } catch {
    // ignore
  }
  return "zh-TW";
}

// ── Context ──

interface I18nContextValue {
  locale: Locale;
  t: Translations;
  setLocale: (locale: Locale) => void;
}

const I18nContext = createContext<I18nContextValue | null>(null);

// ── Provider ──

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(getInitialLocale);

  const setLocale = useCallback((newLocale: Locale) => {
    setLocaleState(newLocale);
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, newLocale);
    } catch {
      // ignore
    }
  }, []);

  const value = useMemo(
    () => ({
      locale,
      t: locales[locale],
      setLocale,
    }),
    [locale, setLocale]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

// ── Hook ──

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return ctx;
}
