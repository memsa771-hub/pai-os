import enUS, { type Messages } from './en-US';

/** The one and only catalogue — English. */
export const FALLBACK_CATALOGUE = enUS;

export function getCatalogue(): Messages {
  return enUS;
}

export type { Messages };
