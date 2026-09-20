'use client';

/**
 * The Profile's record editor.
 *
 * One small dialog, driven by a per-kind field list, rather than nine bespoke
 * forms or one giant page-wide form. The shapes below are the canonical
 * RECORD_SPECS from the backend narrowed to what a student would reasonably
 * type by hand — this is a FORM over the canonical model, not a second model.
 *
 * Every save goes to `workspaceApi.editStudentProfile`, which is the same
 * validated candidate -> reconciler path PAI's extraction uses, so a hand
 * correction keeps its provenance and lands in the revision trail. Values are
 * merged server-side, so leaving a field blank leaves the stored value alone
 * rather than erasing it.
 */

import * as React from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/responsive-dialog';
import { workspaceApi } from '@/lib/api';
import { useT, type MessageKey } from '@/lib/i18n';
import type { EditableRecordKind } from '@/lib/student-profile';

type FieldType = 'text' | 'number' | 'textarea' | 'select' | 'list';

interface FieldSpec {
  /** Dot path into the record value — `result.gpa`, `details.description`. */
  path: string;
  type: FieldType;
  required?: boolean;
  /** Enum choices, rendered through `studentProfile.option.*`. */
  options?: string[];
  /** Share a row with the next field. */
  half?: boolean;
  placeholder?: string;
}

const DATE_HINT = 'YYYY or YYYY-MM';

/**
 * Dates are stored at whatever precision the student actually knows — the
 * backend accepts YYYY, YYYY-MM and YYYY-MM-DD and a date picker would force
 * a day nobody stated.
 */
const date = (path: string): FieldSpec => ({ path, type: 'text', half: true, placeholder: DATE_HINT });

export const RECORD_FORMS: Record<EditableRecordKind, FieldSpec[]> = {
  education: [
    { path: 'qualification_name', type: 'text', required: true, placeholder: 'BS Computer Science' },
    { path: 'institution_name', type: 'text', placeholder: 'COMSATS' },
    { path: 'field_of_study', type: 'text', half: true },
    {
      path: 'academic_status', type: 'select', half: true,
      options: ['current', 'completed', 'incomplete', 'planned'],
    },
    date('start_date'),
    date('end_date'),
    { path: 'result.gpa', type: 'number', half: true, placeholder: '3.42' },
    { path: 'result.gpa_scale', type: 'number', half: true, placeholder: '4' },
    { path: 'result.percentage', type: 'number', half: true, placeholder: '87' },
    { path: 'result.grade', type: 'text', half: true },
  ],
  test_attempt: [
    { path: 'test_type', type: 'text', required: true, placeholder: 'IELTS' },
    { path: 'overall_score', type: 'text', half: true, placeholder: '7.5' },
    { path: 'attempt_number', type: 'number', half: true },
    date('test_date'),
    date('expiry_date'),
  ],
  language_proficiency: [
    { path: 'language', type: 'text', required: true },
    { path: 'proficiency', type: 'text', placeholder: 'Native, C1, Fluent' },
  ],
  work_experience: [
    { path: 'organization', type: 'text', required: true },
    { path: 'role', type: 'text', required: true },
    { path: 'experience_type', type: 'text', half: true, placeholder: 'Internship' },
    { path: 'details.country', type: 'text', half: true },
    date('start_date'),
    date('end_date'),
    { path: 'details.responsibilities', type: 'list' },
    { path: 'details.skills', type: 'list' },
  ],
  project: [
    { path: 'name', type: 'text', required: true },
    { path: 'role', type: 'text', half: true },
    { path: 'details.url', type: 'text', half: true },
    date('start_date'),
    date('end_date'),
    { path: 'details.description', type: 'textarea' },
    { path: 'details.technologies', type: 'list' },
  ],
  skill: [
    { path: 'name', type: 'text', required: true },
    { path: 'proficiency', type: 'text', placeholder: 'Advanced' },
  ],
  certification: [
    { path: 'name', type: 'text', required: true },
    { path: 'issuer', type: 'text' },
    date('issued_on'),
    date('expires_on'),
    { path: 'details.credential_url', type: 'text' },
  ],
  research: [
    { path: 'title', type: 'text', required: true },
    { path: 'organization', type: 'text', half: true },
    { path: 'role', type: 'text', half: true },
    date('start_date'),
    date('end_date'),
    { path: 'details.abstract', type: 'textarea' },
  ],
  achievement: [
    { path: 'title', type: 'text', required: true },
    { path: 'achievement_type', type: 'text', half: true },
    { path: 'issuer', type: 'text', half: true },
    date('achieved_on'),
    { path: 'details.description', type: 'textarea' },
  ],
  goal: [
    { path: 'title', type: 'text', required: true, placeholder: 'MSc AI in Germany' },
    { path: 'goal_type', type: 'text', required: true, half: true, placeholder: 'education' },
    {
      path: 'commitment', type: 'select', half: true,
      options: ['exploratory', 'considering', 'committed'],
    },
    date('target_date'),
    { path: 'details.motivation', type: 'textarea' },
    { path: 'details.target_countries', type: 'list' },
  ],
};

/**
 * Any canonical record, identified only by the field every one of them has.
 * The editor reads the rest by path, so it does not need a union of all
 * sixteen record types — and cannot drift out of sync with one.
 */
export interface EditableRecord {
  id: string;
}

/** Read a dot path out of a record, for prefilling the form. */
function readPath(record: unknown, path: string): string {
  if (!record) return '';
  const value = path.split('.').reduce<unknown>(
    (node, key) => (node && typeof node === 'object'
      ? (node as Record<string, unknown>)[key] : undefined),
    record,
  );
  if (value === null || value === undefined) return '';
  return Array.isArray(value) ? value.join(', ') : String(value);
}

/** Build a nested value object from the flat draft, dropping blank fields. */
function buildValue(fields: FieldSpec[], draft: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of fields) {
    const raw = (draft[field.path] || '').trim();
    if (!raw) continue;

    let value: unknown = raw;
    if (field.type === 'number') {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) continue;
      value = parsed;
    } else if (field.type === 'list') {
      const items = raw.split(',').map((item) => item.trim()).filter(Boolean);
      if (!items.length) continue;
      value = items;
    }

    const keys = field.path.split('.');
    let node = out;
    for (const key of keys.slice(0, -1)) {
      if (typeof node[key] !== 'object' || node[key] === null) node[key] = {};
      node = node[key] as Record<string, unknown>;
    }
    node[keys[keys.length - 1]] = value;
  }
  return out;
}

export function RecordEditDialog({
  kind,
  sectionLabel,
  record,
  open,
  onOpenChange,
  onSaved,
}: {
  kind: EditableRecordKind;
  /** The human name of the thing being edited, for the dialog title. */
  sectionLabel: string;
  /** null = adding a new record. */
  record: EditableRecord | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const t = useT();
  const fields = RECORD_FORMS[kind];
  const [draft, setDraft] = React.useState<Record<string, string>>({});
  const [saving, setSaving] = React.useState(false);

  // Reset to the record being edited each time the dialog opens, so a
  // cancelled edit never leaks into the next one.
  React.useEffect(() => {
    if (!open) return;
    setDraft(Object.fromEntries(fields.map((f) => [f.path, readPath(record, f.path)])));
  }, [open, record, fields]);

  // `t` echoes an unknown key back, so a field added to RECORD_FORMS before
  // its label exists reads as "Field of study", not `studentProfile.label.…`.
  const label = (path: string) => {
    const leaf = path.split('.').pop() || path;
    const messageKey = `studentProfile.label.${leaf}`;
    const translated = t(messageKey as MessageKey);
    return translated === messageKey
      ? leaf.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
      : translated;
  };

  const missingRequired = fields.some((f) => f.required && !(draft[f.path] || '').trim());

  const save = async () => {
    const value = buildValue(fields, draft);
    if (!Object.keys(value).length) return;
    setSaving(true);
    try {
      await workspaceApi.editStudentProfile({
        recordType: kind,
        ...(record ? { recordId: record.id } : {}),
        value,
        reason: t('studentProfile.editReason'),
      });
      toast.success(t('studentProfile.saved'));
      onOpenChange(false);
      onSaved();
    } catch (e) {
      // The backend rejects a change that contradicts stronger evidence and
      // files it for review instead. Showing its reason is the honest thing:
      // "saved" would be a lie about canonical state.
      toast.error(extractMessage(e) || t('studentProfile.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {record
              ? t('studentProfile.editTitle', { section: sectionLabel })
              : t('studentProfile.addTitle', { section: sectionLabel })}
          </DialogTitle>
          <DialogDescription>{t('studentProfile.editHint')}</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="grid grid-cols-2 gap-3">
            {fields.map((field) => (
              <FieldRow
                key={field.path}
                field={field}
                label={label(field.path)}
                value={draft[field.path] || ''}
                onChange={(v) => setDraft((d) => ({ ...d, [field.path]: v }))}
              />
            ))}
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            {t('studentProfile.cancel')}
          </Button>
          <Button onClick={save} disabled={saving || missingRequired}>
            {saving ? t('studentProfile.saving') : t('studentProfile.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * One labelled control.
 *
 * A component rather than inline JSX so each row can own a `useId` — the label
 * is explicitly tied to its input, so tapping it focuses the field and a
 * screen reader reads the two together instead of announcing a bare box.
 */
function FieldRow({ field, label, value, onChange }: {
  field: FieldSpec;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const t = useT();
  const id = React.useId();

  return (
    <div className={field.half ? 'col-span-1 space-y-1.5' : 'col-span-2 space-y-1.5'}>
      <Label htmlFor={id} className="text-xs">
        {label}
        {field.required && <span className="ms-1 text-destructive">*</span>}
      </Label>

      {field.type === 'select' ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(field.options || []).map((option) => (
              <SelectItem key={option} value={option}>
                {t(`studentProfile.option.${option}` as MessageKey)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : field.type === 'textarea' ? (
        <Textarea
          id={id}
          rows={3}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <Input
          id={id}
          inputMode={field.type === 'number' ? 'decimal' : undefined}
          placeholder={field.placeholder
            || (field.type === 'list' ? 'Comma separated' : undefined)}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}

/** Pull the human message out of an API error like `API 400: {json}`. */
export function extractMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  const brace = raw.indexOf('{');
  if (brace >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(brace));
      if (parsed && typeof parsed.message === 'string') return parsed.message;
    } catch { /* not JSON — fall through to the raw text */ }
  }
  return raw.startsWith('API ') ? '' : raw;
}
