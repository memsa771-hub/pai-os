'use client';

/**
 * The Profile — a living CV, not a settings page and not a database admin
 * screen.
 *
 * Everything on it is a projection of the one canonical student model (Vault
 * facts + typed student records), fetched as a single composed document from
 * `GET /v1/student-profile`. This component never touches memory internals,
 * never caches a second copy of the profile, and never invents a value: a
 * field the student has not stated is left out rather than printed as "N/A".
 *
 * The loop this closes: the student tells PAI Counselor something, extraction
 * proposes it, reconciliation writes it canonically, and it appears here on
 * the next load. A correction made here writes back along that same path, so
 * Counselor sees it in the next conversation.
 */

import * as React from 'react';
import {
  AlertTriangle, CircleUser, Loader2, MapPin, MessageSquare, Pencil,
  Plus, RefreshCw, Upload,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DetailHeader } from '@/components/layout/app-header';
import { useLayout } from '@/components/layout/layout-context';
import { useWorkspace } from '@/lib/workspace-context';
import { workspaceApi } from '@/lib/api';
import { PAI_PRIMARY_CONVERSATION_ID } from '@/lib/primary-conversation';
import { useT, type MessageKey } from '@/lib/i18n';
import {
  factLabel, formatDate, formatDateRange, formatFactValue, formatResult,
  humanizeFactKey, humanizeValue, sortByRecency, sortEducation,
  type EditableRecordKind, type StudentProfile,
} from '@/lib/student-profile';
import {
  Chips, Collapsible, FactRow, Row, Section, StageChip, VerificationBadge,
} from './profile-primitives';
import { RecordEditDialog, extractMessage, type EditableRecord } from './record-edit-dialog';
import { ProfileHeaderEditDialog } from './profile-header-edit-dialog';

/** Which record an edit dialog is currently pointed at. */
interface EditTarget {
  kind: EditableRecordKind;
  sectionLabel: string;
  record: EditableRecord | null;
}

export function ProfileView() {
  const t = useT();
  const { openView, setSelectedAgentName } = useLayout();
  const { setCurrentSessionId } = useWorkspace();

  const [profile, setProfile] = React.useState<StudentProfile | null>(null);
  const [error, setError] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [editTarget, setEditTarget] = React.useState<EditTarget | null>(null);
  const [editingHeader, setEditingHeader] = React.useState(false);

  const load = React.useCallback(async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    try {
      setProfile(await workspaceApi.getStudentProfile());
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const talkToPai = () => {
    setCurrentSessionId(PAI_PRIMARY_CONVERSATION_ID);
    setSelectedAgentName(null);
    openView('threads');
  };

  const openEditor = (kind: EditableRecordKind, sectionLabel: string,
                      record: EditableRecord | null = null) =>
    setEditTarget({ kind, sectionLabel, record });

  const header = (
    <DetailHeader
      title={
        <span className="flex items-center gap-2 text-sm font-semibold">
          <CircleUser className="size-4 text-muted-foreground" />
          {t('studentProfile.title')}
        </span>
      }
      titleInHeader
    >
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
        onClick={() => load(false)}
        aria-label={t('studentProfile.refresh')}
      >
        <RefreshCw className="size-3.5" />
      </Button>
    </DetailHeader>
  );

  if (loading) {
    return (
      <div className="flex h-full flex-col">
        {header}
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  if (error || !profile) {
    return (
      <div className="flex h-full flex-col">
        {header}
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
          <p className="text-sm text-muted-foreground">{t('studentProfile.loadFailed')}</p>
          <Button variant="outline" size="sm" onClick={() => load()}>
            {t('studentProfile.retry')}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {header}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6 sm:py-6">
          <ProfileIdentity
            profile={profile}
            onEdit={() => setEditingHeader(true)}
          />

          {profile.meta.isEmpty ? (
            <EmptyProfile
              onTalkToPai={talkToPai}
              onAddEducation={() => openEditor('education', t('studentProfile.education'))}
              onUploadCv={() => openView('files')}
            />
          ) : (
            <div className="mt-7 space-y-7">
              <IssuesBlock profile={profile} onResolved={() => load(false)} />
              <JourneyBlock profile={profile} />
              <AboutBlock profile={profile} />
              <EducationBlock profile={profile} onEdit={openEditor} />
              <TestsBlock profile={profile} onEdit={openEditor} />
              <ExperienceBlock profile={profile} onEdit={openEditor} />
              <ProjectsBlock profile={profile} onEdit={openEditor} />
              <SkillsBlock profile={profile} onEdit={openEditor} />
              <CertificationsBlock profile={profile} onEdit={openEditor} />
              <ResearchBlock profile={profile} onEdit={openEditor} />
              <GoalsBlock profile={profile} onEdit={openEditor} />
              <PreferencesBlock profile={profile} />
              <FinanceBlock profile={profile} />
              <ApplicationsBlock profile={profile} />
              <DocumentsBlock profile={profile} />
              <AddMoreBlock profile={profile} onEdit={openEditor} />
            </div>
          )}
        </div>
      </div>

      <ProfileHeaderEditDialog
        header={profile.header}
        facts={profile.facts}
        open={editingHeader}
        onOpenChange={setEditingHeader}
        onSaved={() => load(false)}
      />

      {editTarget && (
        <RecordEditDialog
          kind={editTarget.kind}
          sectionLabel={editTarget.sectionLabel}
          record={editTarget.record}
          open
          onOpenChange={(open) => { if (!open) setEditTarget(null); }}
          onSaved={() => load(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function ProfileIdentity({
  profile, onEdit,
}: { profile: StudentProfile; onEdit: () => void }) {
  const t = useT();
  const { header } = profile;
  const name = header.displayName || t('studentProfile.noName');
  const initial = name[0]?.toUpperCase() || '?';

  return (
    <div className="flex items-start gap-4">
      {header.avatarUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={header.avatarUrl}
          alt=""
          className="size-16 shrink-0 rounded-full object-cover sm:size-20"
        />
      ) : (
        <div className="flex size-16 shrink-0 items-center justify-center rounded-full bg-primary/10 text-2xl font-semibold text-primary sm:size-20">
          {initial}
        </div>
      )}

      <div className="min-w-0 flex-1">
        <h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">{name}</h1>
        {header.status && (
          <p className="mt-0.5 text-sm text-foreground/80">{header.status}</p>
        )}
        {header.location && (
          <p className="mt-0.5 flex items-center gap-1 text-sm text-muted-foreground">
            <MapPin className="size-3.5 shrink-0" />
            {header.location}
          </p>
        )}
        {header.headline && (
          <p className="mt-1.5 text-sm text-muted-foreground">{header.headline}</p>
        )}
        {header.email && (
          <p className="mt-1.5 truncate text-xs text-muted-foreground">{header.email}</p>
        )}
      </div>

      <Button variant="outline" size="sm" className="h-8 shrink-0 px-2.5" onClick={onEdit}>
        <Pencil className="size-3.5" />
        <span className="hidden sm:inline">{t('studentProfile.edit')}</span>
      </Button>
    </div>
  );
}

/**
 * The first-run profile.
 *
 * Built from the app's own empty-state pattern (a dimmed icon, a line, a
 * quieter line — see workflows-view and chat/empty-state), with the three
 * actions that actually start a profile underneath. No decorative frame: PAI
 * does not box its empty states, and a dashed card here would read as a
 * drop-target rather than an invitation.
 */
function EmptyProfile({
  onTalkToPai, onAddEducation, onUploadCv,
}: { onTalkToPai: () => void; onAddEducation: () => void; onUploadCv: () => void }) {
  const t = useT();
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <CircleUser className="size-8 text-muted-foreground opacity-30" />
      <p className="max-w-md text-sm text-muted-foreground">
        {t('studentProfile.emptyTitle')}
      </p>
      <p className="max-w-md text-xs text-muted-foreground/60">
        {t('studentProfile.emptyBody')}
      </p>
      <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
        <Button size="sm" onClick={onTalkToPai}>
          <MessageSquare className="size-3.5" />
          {t('studentProfile.talkToPai')}
        </Button>
        <Button variant="outline" size="sm" onClick={onAddEducation}>
          <Plus className="size-3.5" />
          {t('studentProfile.addEducation')}
        </Button>
        <Button variant="outline" size="sm" onClick={onUploadCv}>
          <Upload className="size-3.5" />
          {t('studentProfile.uploadCv')}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Issues and readiness
// ---------------------------------------------------------------------------

function IssuesBlock({
  profile, onResolved,
}: { profile: StudentProfile; onResolved: () => void }) {
  const t = useT();
  const [busy, setBusy] = React.useState<string | null>(null);
  if (!profile.issues.length) return null;

  const resolve = async (id: string) => {
    setBusy(id);
    try {
      await workspaceApi.resolveProfileIssue(id, t('studentProfile.resolveNote'));
      toast.success(t('studentProfile.resolved'));
      onResolved();
    } catch (e) {
      toast.error(extractMessage(e) || t('studentProfile.saveFailed'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <AlertTriangle className="size-3.5 text-amber-500" />
        <h2 className="text-xs font-semibold tracking-wider uppercase text-muted-foreground">
          {t('studentProfile.issues')}
        </h2>
        <span className="text-xs tabular-nums text-muted-foreground">
          {profile.issues.length}
        </span>
      </div>
      {/* The same neutral chrome every other section uses. The signal lives in
          the heading icon and the per-row severity badge, not in a coloured
          panel — PAI's surfaces are muted, and a full-width amber block here
          would read as a different product's alert. */}
      <div className="divide-y divide-border/60 border-t border-border/60">
        {profile.issues.map((issue) => (
          <div key={issue.id} className="flex items-start gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-sm font-medium">{issue.summary}</span>
                {issue.severity === 'blocking' && (
                  <Badge variant="warning" appearance="light" size="sm">
                    {t('studentProfile.severityBlocking')}
                  </Badge>
                )}
              </div>
              {issue.clarificationQuestion && (
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {issue.clarificationQuestion}
                </p>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 shrink-0 px-2 text-xs"
              disabled={busy === issue.id}
              onClick={() => resolve(issue.id)}
            >
              {t('studentProfile.resolve')}
            </Button>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Readiness statuses, named. Anything unrecognized falls back to its own id. */
function stageStatusLabel(t: ReturnType<typeof useT>, status: string): string {
  switch (status) {
    case 'ready': return t('studentProfile.stageReady');
    case 'partially_ready': return t('studentProfile.stagePartial');
    case 'blocked': return t('studentProfile.stageBlocked');
    case 'insufficient_information': return t('studentProfile.stageInsufficient');
    default: return humanizeFactKey(status);
  }
}

function JourneyBlock({ profile }: { profile: StudentProfile }) {
  const t = useT();
  const stages = Object.entries(profile.readiness.stages);
  if (!stages.length) return null;

  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold tracking-wider uppercase text-muted-foreground">
        {t('studentProfile.journey')}
      </h2>
      <div className="flex flex-wrap gap-1.5">
        {stages.map(([stage, readiness]) => (
          <StageChip
            key={stage}
            label={humanizeFactKey(stage)}
            status={readiness.status}
            statusLabel={stageStatusLabel(t, readiness.status)}
          />
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Scalar-fact blocks
// ---------------------------------------------------------------------------

/** Render a group of Vault facts, skipping anything unset or unrenderable. */
function FactGroupBlock({
  title, keys, facts,
}: { title: string; keys: string[]; facts: Record<string, unknown> }) {
  const t = useT();
  const rows = keys
    .map((key) => ({ key, value: formatFactValue(facts[key]) }))
    .filter((row) => row.value !== null);
  if (!rows.length) return null;

  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold tracking-wider uppercase text-muted-foreground">
        {title}
      </h2>
      <div className="divide-y divide-border/60 border-t border-border/60 py-0.5">
        {rows.map(({ key, value }) => (
          <FactRow key={key} label={factLabel(t, key)} value={value} />
        ))}
      </div>
    </section>
  );
}

function AboutBlock({ profile }: { profile: StudentProfile }) {
  const t = useT();
  return (
    <FactGroupBlock
      title={t('studentProfile.about')}
      keys={profile.factGroups.about || []}
      facts={profile.facts}
    />
  );
}

function PreferencesBlock({ profile }: { profile: StudentProfile }) {
  const t = useT();
  return (
    <FactGroupBlock
      title={t('studentProfile.preferences')}
      keys={profile.factGroups.preferences || []}
      facts={profile.facts}
    />
  );
}

// ---------------------------------------------------------------------------
// Record blocks
// ---------------------------------------------------------------------------

type OpenEditor = (
  kind: EditableRecordKind, sectionLabel: string,
  record?: EditableRecord | null,
) => void;

/** The per-row edit affordance, shared by every editable section. */
function useEditProps(onEdit: OpenEditor, kind: EditableRecordKind, label: string) {
  const t = useT();
  return React.useCallback(
    (record: EditableRecord) => ({
      onEdit: () => onEdit(kind, label, record),
      editLabel: t('studentProfile.edit'),
    }),
    [onEdit, kind, label, t],
  );
}

function EducationBlock({
  profile, onEdit,
}: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const label = t('studentProfile.education');
  const editProps = useEditProps(onEdit, 'education', label);
  const records = sortEducation(profile.sections.education.education || []);
  const courses = profile.sections.education.course || [];

  return (
    <>
      <Section
        title={label}
        count={records.length}
        isEmpty={!records.length}
        onAdd={() => onEdit('education', label)}
      >
        {records.map((record) => (
          <Row
            key={record.id}
            title={record.qualification_name}
            subtitle={record.institution_name}
            meta={[
              formatDateRange(record.start_date,
                record.end_date || (record.graduation_year ? String(record.graduation_year) : undefined),
                t('studentProfile.present')),
              record.field_of_study,
              record.details?.institution_city,
            ]}
            trailing={formatResult(record.result)}
            badges={<VerificationBadge status={record.verification_status} />}
            {...editProps(record)}
          />
        ))}
      </Section>

      {courses.length > 0 && (
        <Section title={t('studentProfile.courses')} count={courses.length}>
          <Collapsible
            items={courses}
            render={(course) => (
              <Row
                key={course.id}
                title={course.name}
                meta={[course.details?.semester, course.details?.academic_year]}
                trailing={course.grade || formatResult(course.score)}
              />
            )}
          />
        </Section>
      )}
    </>
  );
}

function TestsBlock({ profile, onEdit }: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const testLabel = t('studentProfile.tests');
  const langLabel = t('studentProfile.languages');
  const testProps = useEditProps(onEdit, 'test_attempt', testLabel);
  const langProps = useEditProps(onEdit, 'language_proficiency', langLabel);
  const tests = profile.sections.tests.test_attempt || [];
  const languages = profile.sections.tests.language_proficiency || [];

  return (
    <>
      <Section
        title={testLabel}
        count={tests.length}
        isEmpty={!tests.length}
        onAdd={() => onEdit('test_attempt', testLabel)}
      >
        {tests.map((test) => (
          <Row
            key={test.id}
            title={test.test_type}
            meta={[
              test.attempt_number
                ? t('studentProfile.attempt', { number: test.attempt_number })
                : null,
              formatDate(test.test_date),
            ]}
            notes={
              test.section_scores && Object.keys(test.section_scores).length > 0 ? (
                <span className="text-xs text-muted-foreground">
                  {Object.entries(test.section_scores)
                    .map(([section, score]) => `${humanizeFactKey(section)} ${score}`)
                    .join(' · ')}
                </span>
              ) : undefined
            }
            trailing={test.overall_score}
            badges={<VerificationBadge status={test.verification_status} />}
            {...testProps(test)}
          />
        ))}
      </Section>

      <Section
        title={langLabel}
        count={languages.length}
        isEmpty={!languages.length}
        onAdd={() => onEdit('language_proficiency', langLabel)}
      >
        {languages.map((language) => (
          <Row
            key={language.id}
            title={language.language}
            meta={[humanizeValue(language.evidence_type)]}
            trailing={language.proficiency}
            {...langProps(language)}
          />
        ))}
      </Section>
    </>
  );
}

function ExperienceBlock({ profile, onEdit }: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const label = t('studentProfile.experience');
  const editProps = useEditProps(onEdit, 'work_experience', label);
  const records = sortByRecency(profile.sections.experience.work_experience || []);

  return (
    <Section
      title={label}
      count={records.length}
      isEmpty={!records.length}
      onAdd={() => onEdit('work_experience', label)}
    >
      {records.map((record) => (
        <Row
          key={record.id}
          title={record.role}
          subtitle={record.organization}
          meta={[
            formatDateRange(record.start_date, record.end_date, t('studentProfile.present')),
            humanizeValue(record.experience_type),
            record.details?.country,
          ]}
          notes={
            record.details?.responsibilities?.length ? (
              <ul className="list-disc space-y-0.5 ps-4 text-sm text-muted-foreground">
                {record.details.responsibilities.slice(0, 3).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : undefined
          }
          {...editProps(record)}
        />
      ))}
    </Section>
  );
}

function ProjectsBlock({ profile, onEdit }: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const label = t('studentProfile.projects');
  const editProps = useEditProps(onEdit, 'project', label);
  const records = sortByRecency(profile.sections.projects.project || []);

  return (
    <Section
      title={label}
      count={records.length}
      isEmpty={!records.length}
      onAdd={() => onEdit('project', label)}
    >
      {records.map((record) => (
        <Row
          key={record.id}
          title={record.name}
          subtitle={record.role}
          meta={[formatDateRange(record.start_date, record.end_date, t('studentProfile.present'))]}
          notes={
            <>
              {record.details?.description && (
                <p className="text-sm text-muted-foreground">{record.details.description}</p>
              )}
              {record.details?.technologies?.length ? (
                <div className="mt-1.5">
                  <Chips values={record.details.technologies} />
                </div>
              ) : null}
            </>
          }
          {...editProps(record)}
        />
      ))}
    </Section>
  );
}

function SkillsBlock({ profile, onEdit }: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const label = t('studentProfile.skills');
  const editProps = useEditProps(onEdit, 'skill', label);
  const records = profile.sections.skills.skill || [];

  return (
    <Section
      title={label}
      count={records.length}
      isEmpty={!records.length}
      onAdd={() => onEdit('skill', label)}
    >
      {records.map((record) => (
        <Row
          key={record.id}
          title={record.name}
          trailing={record.proficiency}
          {...editProps(record)}
        />
      ))}
    </Section>
  );
}

function CertificationsBlock({ profile, onEdit }: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const label = t('studentProfile.certifications');
  const editProps = useEditProps(onEdit, 'certification', label);
  const records = profile.sections.certifications.certification || [];

  return (
    <Section
      title={label}
      count={records.length}
      isEmpty={!records.length}
      onAdd={() => onEdit('certification', label)}
    >
      {records.map((record) => (
        <Row
          key={record.id}
          title={record.name}
          subtitle={record.issuer}
          meta={[formatDateRange(record.issued_on, record.expires_on)]}
          badges={<VerificationBadge status={record.verification_status} />}
          {...editProps(record)}
        />
      ))}
    </Section>
  );
}

function ResearchBlock({ profile, onEdit }: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const researchLabel = t('studentProfile.research');
  const achievementLabel = t('studentProfile.achievements');
  const researchProps = useEditProps(onEdit, 'research', researchLabel);
  const achievementProps = useEditProps(onEdit, 'achievement', achievementLabel);
  const research = sortByRecency(profile.sections.research.research || []);
  const achievements = profile.sections.research.achievement || [];

  return (
    <>
      <Section
        title={researchLabel}
        count={research.length}
        isEmpty={!research.length}
        onAdd={() => onEdit('research', researchLabel)}
      >
        {research.map((record) => (
          <Row
            key={record.id}
            title={record.title}
            subtitle={record.organization}
            meta={[
              formatDateRange(record.start_date, record.end_date, t('studentProfile.present')),
              record.role,
            ]}
            notes={record.details?.publication_title}
            {...researchProps(record)}
          />
        ))}
      </Section>

      <Section
        title={achievementLabel}
        count={achievements.length}
        isEmpty={!achievements.length}
        onAdd={() => onEdit('achievement', achievementLabel)}
      >
        {achievements.map((record) => (
          <Row
            key={record.id}
            title={record.title}
            subtitle={record.issuer}
            meta={[formatDate(record.achieved_on), humanizeValue(record.achievement_type),
                   humanizeValue(record.details?.level)]}
            {...achievementProps(record)}
          />
        ))}
      </Section>
    </>
  );
}

function GoalsBlock({ profile, onEdit }: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();
  const label = t('studentProfile.goals');
  const editProps = useEditProps(onEdit, 'goal', label);
  const records = profile.sections.goals.goal || [];

  return (
    <Section
      title={label}
      count={records.length}
      isEmpty={!records.length}
      onAdd={() => onEdit('goal', label)}
    >
      {records.map((record) => (
        <Row
          key={record.id}
          title={record.title}
          meta={[
            record.commitment
              ? t(`studentProfile.option.${record.commitment}` as MessageKey)
              : null,
            formatDate(record.target_date),
            record.details?.target_intake,
          ]}
          notes={
            <>
              {record.details?.motivation && (
                <p className="text-sm text-muted-foreground">{record.details.motivation}</p>
              )}
              {record.details?.target_countries?.length ? (
                <div className="mt-1.5">
                  <Chips values={record.details.target_countries} />
                </div>
              ) : null}
            </>
          }
          {...editProps(record)}
        />
      ))}
    </Section>
  );
}

function FinanceBlock({ profile }: { profile: StudentProfile }) {
  const t = useT();
  const sponsors = profile.sections.finance.financial_sponsor || [];
  const scholarships = profile.sections.finance.scholarship_application || [];
  const factKeys = profile.factGroups.finance || [];

  return (
    <>
      <FactGroupBlock
        title={t('studentProfile.finance')}
        keys={factKeys}
        facts={profile.facts}
      />

      <Section title={t('studentProfile.sponsors')} count={sponsors.length} isEmpty={!sponsors.length}>
        {sponsors.map((record) => (
          <Row
            key={record.id}
            title={record.name || humanizeValue(record.sponsor_type)}
            meta={[record.details?.relationship, humanizeValue(record.commitment_status)]}
            trailing={formatFactValue({
              amount: record.details?.amount, currency: record.details?.currency,
            })}
          />
        ))}
      </Section>

      <Section
        title={t('studentProfile.scholarships')}
        count={scholarships.length}
        isEmpty={!scholarships.length}
      >
        {scholarships.map((record) => (
          <Row
            key={record.id}
            title={record.scholarship_name}
            subtitle={record.provider}
            meta={[humanizeValue(record.application_status), formatDate(record.deadline)]}
            trailing={formatFactValue({
              amount: record.details?.award_amount, currency: record.details?.currency,
            })}
          />
        ))}
      </Section>
    </>
  );
}

function ApplicationsBlock({ profile }: { profile: StudentProfile }) {
  const t = useT();
  const applications = profile.sections.applications.application || [];
  const visas = profile.sections.applications.visa || [];

  return (
    <>
      <Section
        title={t('studentProfile.applications')}
        count={applications.length}
        isEmpty={!applications.length}
      >
        {applications.map((record) => (
          <Row
            key={record.id}
            title={record.program_name || record.institution_name}
            subtitle={record.program_name ? record.institution_name : undefined}
            meta={[record.intake, formatDate(record.deadline), record.details?.next_action]}
            trailing={humanizeValue(record.application_status)}
          />
        ))}
      </Section>

      <Section title={t('studentProfile.visas')} count={visas.length} isEmpty={!visas.length}>
        {visas.map((record) => (
          <Row
            key={record.id}
            title={record.country}
            subtitle={record.visa_type}
            meta={[record.details?.next_action]}
            trailing={humanizeValue(record.application_status)}
            badges={<VerificationBadge status={record.verification_status} />}
          />
        ))}
      </Section>
    </>
  );
}

function DocumentsBlock({ profile }: { profile: StudentProfile }) {
  const t = useT();
  const documents = profile.sections.documents.document || [];

  return (
    <Section
      title={t('studentProfile.documents')}
      count={documents.length}
      isEmpty={!documents.length}
    >
      {documents.map((record) => (
        <Row
          key={record.id}
          title={record.title || humanizeValue(record.document_type)}
          meta={[humanizeValue(record.document_type)]}
          badges={<VerificationBadge status={record.verification_status} />}
        />
      ))}
    </Section>
  );
}

/**
 * The one place a section the student has nothing in can be started from.
 *
 * Empty sections render nothing of their own (see Section), so this collects
 * them into a single row of small actions instead of eleven headings with
 * "nothing here yet" underneath each — which is what would make the page feel
 * like an unfinished form rather than a CV.
 */
function AddMoreBlock({
  profile, onEdit,
}: { profile: StudentProfile; onEdit: OpenEditor }) {
  const t = useT();

  // Section id -> the editable kind it adds, and the label to show.
  const candidates: { kind: EditableRecordKind; records: unknown[]; label: string }[] = [
    { kind: 'education', records: profile.sections.education.education, label: t('studentProfile.education') },
    { kind: 'test_attempt', records: profile.sections.tests.test_attempt, label: t('studentProfile.tests') },
    { kind: 'language_proficiency', records: profile.sections.tests.language_proficiency, label: t('studentProfile.languages') },
    { kind: 'work_experience', records: profile.sections.experience.work_experience, label: t('studentProfile.experience') },
    { kind: 'project', records: profile.sections.projects.project, label: t('studentProfile.projects') },
    { kind: 'skill', records: profile.sections.skills.skill, label: t('studentProfile.skills') },
    { kind: 'certification', records: profile.sections.certifications.certification, label: t('studentProfile.certifications') },
    { kind: 'research', records: profile.sections.research.research, label: t('studentProfile.research') },
    { kind: 'achievement', records: profile.sections.research.achievement, label: t('studentProfile.achievements') },
    { kind: 'goal', records: profile.sections.goals.goal, label: t('studentProfile.goals') },
  ];

  const missing = candidates.filter((entry) => !(entry.records || []).length);
  if (!missing.length) return null;

  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold tracking-wider uppercase text-muted-foreground">
        {t('studentProfile.addMore')}
      </h2>
      <div className="flex flex-wrap gap-1.5">
        {missing.map(({ kind, label }) => (
          <Button
            key={kind}
            variant="outline"
            size="sm"
            className="h-7 px-2.5 text-xs font-normal text-muted-foreground hover:text-foreground"
            onClick={() => onEdit(kind, label)}
          >
            <Plus className="size-3" />
            {label}
          </Button>
        ))}
      </div>
    </section>
  );
}
