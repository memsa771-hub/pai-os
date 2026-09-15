'use client';

import { createContext, useContext, useEffect, useMemo } from 'react';
import { DEFAULT_LOCALE, type Locale } from './locales';
import { translate, type MessageKey, type TranslateParams } from './translate';
import { FALLBACK_CATALOGUE } from './messages';
import {
  formatDate,
  formatDateTime,
  formatFileSize,
  formatNumber,
  formatRelativeTime,
  formatRelativeTimeShort,
  formatTime,
  getWeekdayLabels,
} from './format';

export type TranslateFn = (key: MessageKey, params?: TranslateParams) => string;

interface I18nContextValue {
  /** The active locale. English only, for now — kept as a value (rather than
   * inlining 'en-US' everywhere) so a second locale is additive later. */
  locale: Locale;
  /** Translates a dot-path key. */
  t: TranslateFn;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  // Keep `<html lang>` in step so screen readers and `:lang()` rules agree
  // with what's on screen.
  useEffect(() => {
    document.documentElement.lang = DEFAULT_LOCALE;
  }, []);

  const value = useMemo<I18nContextValue>(() => ({
    locale: DEFAULT_LOCALE,
    t: (key, params) => translate(FALLBACK_CATALOGUE, FALLBACK_CATALOGUE, DEFAULT_LOCALE, key, params),
  }), []);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within an <I18nProvider>');
  return ctx;
}

/** Shorthand for components that only need the translate function. */
export function useT(): TranslateFn {
  return useI18n().t;
}

/**
 * `Intl` formatters pre-bound to the active locale, so components don't have to
 * thread `locale` through every call.
 */
export function useFormatters() {
  const { locale, t } = useI18n();

  return useMemo(() => {
    const justNow = t('common.justNow');
    return {
      locale,
      /** "5 minutes ago" */
      timeAgo: (value: Date | string | number | null | undefined) =>
        formatRelativeTime(value, locale, justNow),
      /** Compact form for dense lists: "5m", "2d" */
      timeAgoShort: (value: Date | string | number | null | undefined) =>
        formatRelativeTimeShort(value, locale, justNow),
      formatDate: (value: Date | string | number | null | undefined, options?: Intl.DateTimeFormatOptions) =>
        formatDate(value, locale, options),
      formatDateTime: (value: Date | string | number | null | undefined, options?: Intl.DateTimeFormatOptions) =>
        formatDateTime(value, locale, options),
      formatTime: (value: Date | string | number | null | undefined, options?: Intl.DateTimeFormatOptions) =>
        formatTime(value, locale, options),
      formatNumber: (value: number, options?: Intl.NumberFormatOptions) =>
        formatNumber(value, locale, options),
      formatFileSize: (bytes: number | null | undefined) => formatFileSize(bytes, locale),
      /** Monday-first weekday names for schedule pickers. */
      weekdayLabels: (weekday?: Intl.DateTimeFormatOptions['weekday']) =>
        getWeekdayLabels(locale, weekday),
    };
  }, [locale, t]);
}
