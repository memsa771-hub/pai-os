/**
 * Locale definition.
 *
 * Placement AI Workspace ships English only. This file exists so the rest of
 * the i18n plumbing (translate/format/context) stays written in terms of a
 * `Locale` type rather than a hardcoded string — adding a second locale back
 * later means widening this union and nothing else.
 */

export const LOCALES = ['en-US'] as const;

export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = 'en-US';

export function isLocale(value: unknown): value is Locale {
  return value === DEFAULT_LOCALE;
}
