import { DEFAULT_LOCALE, type Locale } from './locales';
import { FALLBACK_CATALOGUE } from './messages';
import { translate, type MessageKey, type TranslateParams } from './translate';

/**
 * Translator for Server Components and `generateMetadata`, using the same
 * catalogue and fallback chain as the client hook. English only, for now —
 * see `./locales`.
 */
export async function getServerTranslations(): Promise<{
  locale: Locale;
  t: (key: MessageKey, params?: TranslateParams) => string;
}> {
  return {
    locale: DEFAULT_LOCALE,
    t: (key, params) => translate(FALLBACK_CATALOGUE, FALLBACK_CATALOGUE, DEFAULT_LOCALE, key, params),
  };
}
