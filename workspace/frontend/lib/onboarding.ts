/**
 * First-run onboarding — the student's opening statement about themselves.
 *
 * This replaces the OpenAgents "connect your first agent" flow, which had no
 * meaning in PAI: there is nothing to connect. PAI Counselor is the only
 * conversational agent and PAI Operator is deliberately invisible. What a new
 * PAI account actually lacks is not an agent, it is any knowledge of the
 * student — so that is what we ask for.
 *
 * Every answer becomes a canonical Vault fact through the same
 * candidate -> reconciler path PAI's own extraction uses. There is no
 * onboarding store and no second copy of a student's identity.
 */

/** Names match `ONBOARDING_FIELDS` in the backend, which is the source. */
export interface OnboardingAnswers {
  fullName?: string;
  preferredName?: string;
  statusCategory?: string;
  nationality?: string;
  gender?: string;
  dateOfBirth?: string;
  currentCountry?: string;
  currentCity?: string;
}

export type OnboardingFieldName = keyof OnboardingAnswers;

/** One question, as the backend declares it. */
export interface OnboardingFieldSpec {
  name: OnboardingFieldName;
  /** The canonical Vault key the answer is stored under. */
  fieldKey: string;
  required: boolean;
  /** Non-empty for the dropdowns. */
  choices: string[];
  isDate: boolean;
}

export interface OnboardingState {
  /** Whether the form is still owed by this account. */
  required: boolean;
  completedAt: string | null;
  fields: OnboardingFieldSpec[];
  /** Anything already known — a returning or pre-existing student. */
  prefill: OnboardingAnswers;
}

export interface OnboardingResult {
  /** Vault keys that actually moved. */
  saved: string[];
  /** Vault key -> why it did not, so nothing is reported as saved falsely. */
  rejected: Record<string, string>;
  completed: boolean;
}

/**
 * The order the form asks in, and how each field is rendered.
 *
 * The backend owns WHICH fields exist; this owns how they look. Anything the
 * backend sends that is missing here still renders, as a plain text box — a
 * new question is never invisible just because the UI has not caught up.
 */
export const FIELD_LAYOUT: Record<string, { half?: boolean; placeholder?: string }> = {
  fullName: { placeholder: 'Ali Ahmed' },
  preferredName: { placeholder: 'Ali' },
  statusCategory: {},
  nationality: { half: true, placeholder: 'Pakistani' },
  gender: { half: true },
  dateOfBirth: { half: true },
  currentCountry: { half: true, placeholder: 'Pakistan' },
  currentCity: { half: true, placeholder: 'Islamabad' },
};
