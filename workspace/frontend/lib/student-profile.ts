/**
 * The Profile's contract with the backend.
 *
 * These types mirror `GET /v1/student-profile` — a PROJECTION over the one
 * canonical source of truth (Vault facts + typed student records), never a
 * second store. Nothing here is persisted client-side: every edit goes back
 * through `workspaceApi.editStudentProfile`, which writes along the same
 * validated path PAI's own extraction uses.
 *
 * Record shapes are the canonical ones from the backend's RECORD_SPECS, so a
 * field added there is a field added here — not a second model to keep in
 * sync with a different name.
 */

import type { MessageKey, TranslateFn } from './i18n';

/** A grade in whatever system the student actually stated. Never normalized. */
export interface RecordResult {
  gpa?: number;
  gpa_scale?: number;
  percentage?: number;
  marks_obtained?: number;
  marks_total?: number;
  grade?: string;
  grading_system?: string;
  backlogs?: number;
  class_or_division?: string;
}

/** Fields every typed record carries once it is canonical. */
interface RecordBase {
  id: string;
  /** self_reported | extracted | document_supported | verified | expired */
  verification_status?: string;
}

export interface EducationRecord extends RecordBase {
  qualification_name: string;
  institution_name?: string;
  canonical_level?: string;
  field_of_study?: string;
  start_date?: string;
  end_date?: string;
  graduation_year?: number;
  academic_status?: 'current' | 'completed' | 'incomplete' | 'planned';
  result?: RecordResult;
  details?: {
    institution_country?: string;
    institution_city?: string;
    framework?: string;
    major?: string;
    minor?: string;
    specialization?: string;
    study_mode?: string;
    thesis_title?: string;
    prerequisites?: string[];
    distinctions?: string[];
  };
}

export interface CourseRecord extends RecordBase {
  education_id: string;
  name: string;
  normalized_name?: string;
  grade?: string;
  score?: RecordResult;
  credits?: number;
  details?: { semester?: string; academic_year?: string; credit_system?: string };
}

export interface TestAttemptRecord extends RecordBase {
  test_type: string;
  original_name?: string;
  attempt_number?: number;
  test_date?: string;
  expiry_date?: string;
  overall_score?: string;
  section_scores?: Record<string, string | number>;
  details?: { status?: string; test_variant?: string };
}

export interface LanguageProficiencyRecord extends RecordBase {
  language: string;
  proficiency?: string;
  evidence_type?: string;
  details?: { native?: boolean; notes?: string };
}

export interface WorkExperienceRecord extends RecordBase {
  organization: string;
  role: string;
  experience_type?: string;
  start_date?: string;
  end_date?: string;
  details?: {
    responsibilities?: string[];
    achievements?: string[];
    skills?: string[];
    country?: string;
    current?: boolean;
  };
}

export interface ProjectRecord extends RecordBase {
  name: string;
  role?: string;
  start_date?: string;
  end_date?: string;
  details?: {
    description?: string;
    skills?: string[];
    technologies?: string[];
    outcomes?: string[];
    url?: string;
  };
}

export interface SkillRecord extends RecordBase {
  name: string;
  proficiency?: string;
  details?: { demonstrated_by?: string[]; learning_goal?: string };
}

export interface CertificationRecord extends RecordBase {
  name: string;
  issuer?: string;
  issued_on?: string;
  expires_on?: string;
  details?: { skills?: string[]; credential_url?: string };
}

export interface ResearchRecord extends RecordBase {
  title: string;
  organization?: string;
  role?: string;
  start_date?: string;
  end_date?: string;
  details?: {
    abstract?: string;
    methods?: string[];
    outcomes?: string[];
    publication_title?: string;
    publication_url?: string;
  };
}

export interface AchievementRecord extends RecordBase {
  title: string;
  achievement_type?: string;
  issuer?: string;
  achieved_on?: string;
  details?: {
    description?: string;
    level?: string;
    leadership_role?: string;
    activity_type?: string;
  };
}

export interface GoalRecord extends RecordBase {
  goal_type: string;
  title: string;
  commitment?: 'exploratory' | 'considering' | 'committed';
  target_date?: string;
  details?: {
    motivation?: string;
    success_criteria?: string;
    degree_level?: string;
    field_of_study?: string;
    target_countries?: string[];
    target_intake?: string;
    career_direction?: string;
    constraints?: string[];
  };
}

export interface ApplicationRecord extends RecordBase {
  institution_name: string;
  program_name?: string;
  intake?: string;
  application_status?: string;
  deadline?: string;
  details?: { missing_documents?: string[]; next_action?: string };
}

export interface VisaRecord extends RecordBase {
  country: string;
  visa_type?: string;
  application_status?: string;
  expiry_date?: string;
  details?: {
    history_type?: string;
    decision_date?: string;
    refusal_reason?: string;
    next_action?: string;
  };
}

export interface FinancialSponsorRecord extends RecordBase {
  sponsor_type: string;
  name?: string;
  commitment_status?: string;
  details?: {
    relationship?: string;
    amount?: number;
    currency?: string;
    evidence_available?: boolean;
  };
}

export interface ScholarshipApplicationRecord extends RecordBase {
  scholarship_name: string;
  provider?: string;
  application_status?: string;
  deadline?: string;
  details?: {
    award_amount?: number;
    currency?: string;
    missing_requirements?: string[];
    next_action?: string;
  };
}

export interface DocumentRecord extends RecordBase {
  file_id: string;
  document_type: string;
  title?: string;
  details?: { related_record_type?: string; related_record_id?: string };
}

/** Every canonical record kind, keyed exactly as the backend names it. */
export interface RecordsByKind {
  education: EducationRecord[];
  course: CourseRecord[];
  test_attempt: TestAttemptRecord[];
  language_proficiency: LanguageProficiencyRecord[];
  work_experience: WorkExperienceRecord[];
  project: ProjectRecord[];
  skill: SkillRecord[];
  certification: CertificationRecord[];
  research: ResearchRecord[];
  achievement: AchievementRecord[];
  goal: GoalRecord[];
  application: ApplicationRecord[];
  visa: VisaRecord[];
  financial_sponsor: FinancialSponsorRecord[];
  scholarship_application: ScholarshipApplicationRecord[];
  document: DocumentRecord[];
}

export type RecordKind = keyof RecordsByKind;

/** The record kinds the Profile offers a first-class editor for. */
export const EDITABLE_RECORD_KINDS = [
  'education', 'test_attempt', 'language_proficiency', 'work_experience',
  'project', 'skill', 'certification', 'research', 'achievement', 'goal',
] as const;

export type EditableRecordKind = (typeof EDITABLE_RECORD_KINDS)[number];

/**
 * Account identity plus whatever canonical data can stand in for the rest.
 * Every field is optional: the page renders what exists and hides what does
 * not, rather than printing a column of placeholders.
 */
export interface ProfileHeader {
  displayName?: string;
  avatarUrl?: string;
  email?: string;
  preferredName?: string;
  status?: string;
  location?: string;
  headline?: string;
}

export interface ProfileReadiness {
  stage: string;
  status: 'ready' | 'partially_ready' | 'insufficient_information' | 'blocked';
  filled: string[];
  missing: string[];
  conflicts: string[];
  expired_evidence: string[];
  next_useful_gap: string | null;
}

/** An open item needing the student's attention. Values are pre-sanitized. */
export interface ProfileIssue {
  id: string;
  type: string;
  severity: string;
  summary: string;
  clarificationQuestion: string | null;
  recordType: string | null;
  recordId: string | null;
  /** null when the field is one the Profile is not allowed to name. */
  fieldKey: string | null;
  values: { current?: unknown; proposed?: unknown } | null;
  createdAt: string | null;
}

export type FactGroup = 'about' | 'preferences' | 'finance';

export interface StudentProfile {
  header: ProfileHeader;
  /** Safe Vault facts only — restricted identifiers never appear here. */
  facts: Record<string, unknown>;
  factGroups: Record<FactGroup, string[]>;
  sections: {
    education: Pick<RecordsByKind, 'education' | 'course'>;
    tests: Pick<RecordsByKind, 'test_attempt' | 'language_proficiency'>;
    experience: Pick<RecordsByKind, 'work_experience'>;
    projects: Pick<RecordsByKind, 'project'>;
    skills: Pick<RecordsByKind, 'skill'>;
    certifications: Pick<RecordsByKind, 'certification'>;
    research: Pick<RecordsByKind, 'research' | 'achievement'>;
    goals: Pick<RecordsByKind, 'goal'>;
    finance: Pick<RecordsByKind, 'financial_sponsor' | 'scholarship_application'>;
    applications: Pick<RecordsByKind, 'application' | 'visa'>;
    documents: Pick<RecordsByKind, 'document'>;
  };
  readiness: { primary: ProfileReadiness; stages: Record<string, ProfileReadiness> };
  issues: ProfileIssue[];
  meta: {
    recordCount: number;
    factCount: number;
    isEmpty: boolean;
    openIssueCount: number;
    generatedAt: string;
  };
}

/** What the Profile sends back when the student corrects something. */
export interface ProfileEdit {
  /** Exactly one of these two. A record edit without an id creates a record. */
  recordType?: RecordKind;
  recordId?: string;
  fieldKey?: string;
  value: unknown;
  /** Kept on the revision, so provenance says why the value changed. */
  reason: string;
}

// ---------------------------------------------------------------------------
// Display helpers
//
// Formatting only. Nothing here infers a value the student did not state —
// a missing grading scale stays missing rather than becoming "/4".
// ---------------------------------------------------------------------------

/** "2022 – 2026", "2022 – present", or just one end when only one was stated. */
export function formatDateRange(
  start?: string, end?: string, present = 'Present',
): string | null {
  const from = formatDate(start);
  const to = end ? formatDate(end) : (start ? present : null);
  if (from && to) return `${from} – ${to}`;
  return from || to || null;
}

/** Dates arrive as YYYY, YYYY-MM or YYYY-MM-DD and are shown at that precision. */
export function formatDate(value?: string): string | null {
  if (!value) return null;
  const [year, month, day] = value.split('-');
  if (!month) return year;
  const monthName = new Date(Number(year), Number(month) - 1, 1)
    .toLocaleString(undefined, { month: 'short' });
  return day ? `${monthName} ${Number(day)}, ${year}` : `${monthName} ${year}`;
}

/**
 * A grade the way the student gave it: "CGPA 3.42 / 4", "87%", "312 / 400".
 * The scale is printed only when it was stated.
 */
export function formatResult(result?: RecordResult): string | null {
  if (!result) return null;
  const parts: string[] = [];
  if (result.gpa !== undefined) {
    parts.push(result.gpa_scale !== undefined
      ? `CGPA ${result.gpa} / ${result.gpa_scale}`
      : `CGPA ${result.gpa}`);
  }
  if (result.percentage !== undefined) parts.push(`${result.percentage}%`);
  if (result.marks_obtained !== undefined && result.marks_total !== undefined) {
    parts.push(`${result.marks_obtained} / ${result.marks_total}`);
  }
  if (result.grade) parts.push(result.grade);
  if (result.class_or_division) parts.push(result.class_or_division);
  if (result.backlogs) parts.push(`${result.backlogs} backlog${result.backlogs === 1 ? '' : 's'}`);
  return parts.join(' · ') || null;
}

/**
 * A stored enum rendered for reading: `in_progress` -> "In progress".
 *
 * Statuses arrive as the machine values the records actually store. Printing
 * them verbatim is the database showing through the CV, so every status the
 * page displays goes through here. Free text the student wrote is returned
 * unchanged — this only ever reshapes a single snake_case token.
 */
export function humanizeValue(value?: string | null): string | null {
  if (!value) return null;
  const text = value.trim();
  if (!text) return null;
  if (/\s/.test(text) || !/_/.test(text)) {
    // Already a phrase the student or an extractor wrote; leave it alone,
    // beyond capitalizing a lone lowercase token like "preparing".
    return /^[a-z]/.test(text) && !/\s/.test(text)
      ? text.charAt(0).toUpperCase() + text.slice(1)
      : text;
  }
  return text.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/**
 * A Vault key's display label.
 *
 * `t()` returns the key itself when the catalogue has no entry, so this
 * compares against that and humanizes instead. Both the profile page and the
 * edit dialog go through here — rendering `studentProfile.field.…` at a user
 * is the failure mode this exists to make impossible.
 */
export function factLabel(t: TranslateFn, key: string): string {
  const messageKey = `studentProfile.field.${key}`;
  const label = t(messageKey as MessageKey);
  return label === messageKey ? humanizeFactKey(key) : label;
}

/** Turn an unlabelled Vault key into a readable label as a last resort. */
export function humanizeFactKey(key: string): string {
  const leaf = key.split('.').pop() || key;
  return leaf.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/**
 * Render a Vault value for display. Objects are formatted by shape rather
 * than stringified, because a profile page must never show raw JSON.
 */
export function formatFactValue(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) {
    const items = value.map((item) => formatFactValue(item)).filter(Boolean);
    return items.length ? items.join(', ') : null;
  }
  if (typeof value === 'object') {
    const money = value as { amount?: number; currency?: string; period?: string };
    if (typeof money.amount === 'number') {
      const amount = money.amount.toLocaleString();
      const period = money.period === 'per_year' ? ' per year'
        : money.period === 'total' ? ' total' : '';
      return `${money.currency ? `${money.currency} ` : ''}${amount}${period}`;
    }
    // An unrecognized object shape is withheld rather than dumped as JSON.
    return null;
  }
  // Enum-backed facts are stored as their machine value ("female", "student").
  // Printing those verbatim is the database showing through the CV, so a lone
  // lowercase token is capitalized; free text the student wrote is untouched.
  return humanizeValue(String(value));
}

/** Education newest-first, using whatever date evidence each record carries. */
export function sortEducation(records: EducationRecord[]): EducationRecord[] {
  const rank = (row: EducationRecord) =>
    row.end_date || (row.graduation_year ? String(row.graduation_year) : '') || row.start_date || '';
  return [...records].sort((a, b) => {
    // An in-progress degree leads: it is the student's current situation.
    if ((a.academic_status === 'current') !== (b.academic_status === 'current')) {
      return a.academic_status === 'current' ? -1 : 1;
    }
    return rank(b).localeCompare(rank(a));
  });
}

/** Most recent first, by whichever end of the range was stated. */
export function sortByRecency<T extends { start_date?: string; end_date?: string }>(
  records: T[],
): T[] {
  return [...records].sort((a, b) =>
    (b.end_date || b.start_date || '').localeCompare(a.end_date || a.start_date || ''));
}
