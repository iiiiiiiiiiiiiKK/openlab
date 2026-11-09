/**
 * Lightweight i18n for the OPENLAB workbench.
 *
 * Design goals:
 *   · no runtime dependency (the bundle stays small),
 *   · type-safe keys, so a typo is a build error rather than a raw key in the UI,
 *   · Chinese by default, switchable from the top bar or Settings,
 *   · the choice is persisted in localStorage and reflected in `<html lang>`.
 *
 * The file is `.tsx` because it renders the context provider directly instead of
 * importing a heavier runtime.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { coreMessages } from "./messages.core";
import { pageMessages } from "./messages.pages";
import { moduleMessages } from "./messages.modules";

export type Locale = "zh" | "en";

export const DEFAULT_LOCALE: Locale = "zh";
export const LOCALE_STORAGE_KEY = "openlab.locale";

export const MESSAGES = {
  ...coreMessages,
  ...pageMessages,
  ...moduleMessages,
} as const;

export type MessageKey = keyof typeof MESSAGES;

export interface MessageEntry {
  en: string;
  zh: string;
}

const registry = MESSAGES as unknown as Record<string, MessageEntry>;

export function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = params[name];
    return value === undefined || value === null ? match : String(value);
  });
}

/** Translate `key` for `locale`, interpolating `{placeholder}` values. */
export function translate(
  locale: Locale,
  key: MessageKey | string,
  params?: Record<string, string | number>,
): string {
  const entry = registry[key as string];
  if (!entry) return String(key);
  const template = entry[locale] ?? entry.en ?? String(key);
  return interpolate(template, params);
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  t: (key: MessageKey | string, params?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

function readInitialLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    if (stored === "zh" || stored === "en") return stored;
  } catch {
    /* storage may be unavailable */
  }
  return DEFAULT_LOCALE;
}

function applyDocumentLocale(locale: Locale): void {
  try {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  } catch {
    /* restricted environment */
  }
}

function persistLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    /* ignore */
  }
  applyDocumentLocale(locale);
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readInitialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    persistLocale(next);
  }, []);

  const toggleLocale = useCallback(() => {
    setLocaleState((current) => {
      const next: Locale = current === "zh" ? "en" : "zh";
      persistLocale(next);
      return next;
    });
  }, []);

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      setLocale,
      toggleLocale,
      t: (key, params) => translate(locale, key, params),
    }),
    [locale, setLocale, toggleLocale],
  );

  applyDocumentLocale(locale);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used inside I18nProvider");
  }
  return context;
}

/** Shorthand for components that only need the translator. */
export function useT(): (key: MessageKey | string, params?: Record<string, string | number>) => string {
  return useI18n().t;
}

/** Translate outside of React using the persisted preference. */
export function tStatic(key: MessageKey | string, params?: Record<string, string | number>): string {
  return translate(readInitialLocale(), key, params);
}
