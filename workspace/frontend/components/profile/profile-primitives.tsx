'use client';

/**
 * The Profile's building blocks.
 *
 * This page is a CV, not an admin console: a section is a heading and a list
 * of rows, separated by hairlines rather than boxed in cards, and a field that
 * has no value is simply absent. Nothing here renders a placeholder, a stub
 * row, or raw JSON — if the student never said it, the page stays quiet about
 * it.
 */

import * as React from 'react';
import { Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useT } from '@/lib/i18n';

/**
 * A profile section. An empty one renders NOTHING — not a heading, not a
 * placeholder row. A CV with eleven "nothing here yet" stubs reads as a form
 * the student has failed to fill in; the way to add a missing section is the
 * single "Add to your profile" row at the foot of the page.
 */
export function Section({
  title,
  count,
  onAdd,
  addLabel,
  children,
  isEmpty,
  id,
}: {
  title: string;
  count?: number;
  onAdd?: () => void;
  addLabel?: string;
  children?: React.ReactNode;
  isEmpty?: boolean;
  id?: string;
}) {
  const t = useT();
  if (isEmpty) return null;

  return (
    <section id={id} className="scroll-mt-4">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-xs font-semibold tracking-wider uppercase text-muted-foreground">
          {title}
        </h2>
        {count !== undefined && count > 0 && (
          <span className="text-xs tabular-nums text-muted-foreground">{count}</span>
        )}
        <div className="flex-1" />
        {onAdd && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={onAdd}
          >
            <Plus className="size-3.5" />
            {addLabel || t('studentProfile.add')}
          </Button>
        )}
      </div>

      <div className="divide-y divide-border/60 border-t border-border/60">{children}</div>
    </section>
  );
}

/**
 * One record. A title line, an optional right-aligned figure (a grade, a
 * score, a date range), and a subdued line or two beneath.
 */
export function Row({
  title,
  subtitle,
  meta,
  trailing,
  notes,
  onEdit,
  editLabel,
  badges,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** Small, dimmed facts — a date range, a location, an attempt number. */
  meta?: (string | null | undefined)[];
  /** The figure this record is really about: a CGPA, a band score. */
  trailing?: React.ReactNode;
  notes?: React.ReactNode;
  onEdit?: () => void;
  editLabel?: string;
  badges?: React.ReactNode;
}) {
  const shown = (meta || []).filter(Boolean) as string[];

  return (
    <div className="group/row flex items-start gap-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-medium">{title}</span>
          {badges}
        </div>
        {subtitle && (
          <div className="mt-0.5 truncate text-sm text-muted-foreground">{subtitle}</div>
        )}
        {shown.length > 0 && (
          <div className="mt-0.5 text-xs text-muted-foreground">{shown.join(' · ')}</div>
        )}
        {notes && <div className="mt-1 text-sm text-foreground/80">{notes}</div>}
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        {trailing && (
          <span className="text-sm tabular-nums whitespace-nowrap text-foreground/80">
            {trailing}
          </span>
        )}
        {onEdit && (
          <Button
            variant="ghost"
            size="sm"
            aria-label={editLabel}
            className="h-7 px-2 text-xs text-muted-foreground opacity-0 transition-opacity group-hover/row:opacity-100 focus-visible:opacity-100 hover:text-foreground"
            onClick={onEdit}
          >
            {editLabel}
          </Button>
        )}
      </div>
    </div>
  );
}

/** A label/value pair for the scalar-fact blocks. Hidden when unset. */
export function FactRow({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="flex gap-3 py-1.5 text-sm">
      {/* Narrower label column on a phone, where a fixed 10rem gutter left
          the values wrapping two words at a time. */}
      <span className="w-28 shrink-0 text-muted-foreground sm:w-40">{label}</span>
      <span className="min-w-0 flex-1">{value}</span>
    </div>
  );
}

/** Small inline tags — skills, technologies, target countries. */
export function Chips({ values }: { values?: string[] }) {
  if (!values?.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => (
        <Badge key={value} variant="secondary" appearance="light" size="sm">
          {value}
        </Badge>
      ))}
    </div>
  );
}

/**
 * How well-evidenced a record is. Only shown when it says something the
 * student can act on — "expired" and "from a document" do; "self-reported",
 * which is the default for anything they simply told PAI, does not.
 */
export function VerificationBadge({ status }: { status?: string }) {
  const t = useT();
  if (status === 'expired') {
    return (
      <Badge variant="warning" appearance="light" size="sm">
        {t('studentProfile.expired')}
      </Badge>
    );
  }
  if (status === 'document_supported' || status === 'externally_verified' || status === 'verified') {
    return (
      <Badge variant="success" appearance="light" size="sm">
        {t('studentProfile.documentSupported')}
      </Badge>
    );
  }
  return null;
}

/**
 * A list that opens up past a threshold. Long CVs are common and a profile
 * should stay scannable; this keeps the first few rows and hides the tail.
 */
export function Collapsible<T>({
  items,
  limit = 4,
  render,
}: {
  items: T[];
  limit?: number;
  render: (item: T, index: number) => React.ReactNode;
}) {
  const t = useT();
  const [expanded, setExpanded] = React.useState(false);
  const shown = expanded ? items : items.slice(0, limit);

  return (
    <>
      {shown.map(render)}
      {items.length > limit && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="w-full py-2 text-start text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          {expanded
            ? t('studentProfile.showLess')
            : t('studentProfile.showAll', { count: items.length })}
        </button>
      )}
    </>
  );
}

/**
 * Readiness status -> the Badge variant that already carries that meaning in
 * this theme. Deliberately not a hand-picked palette: `appearance="light"`
 * resolves to the app's own success/destructive custom properties and flips
 * with the theme on its own.
 */
const STAGE_VARIANT: Record<string, React.ComponentProps<typeof Badge>['variant']> = {
  ready: 'success',
  partially_ready: 'secondary',
  // Warning, not destructive. ReadinessService marks EVERY stage blocked while
  // any conflict is open, so `destructive` paints the whole strip alarm-red
  // over one unreviewed record — nine alarms for a thing the "Needs your
  // review" section immediately above already itemizes once. Amber ties the
  // strip to that section's own icon instead of shouting past it.
  blocked: 'warning',
  insufficient_information: 'outline',
};

/**
 * The readiness strip: one chip per journey stage.
 *
 * The status is carried in text as well as colour — `title` for a pointer,
 * visually-hidden text for a screen reader — because "this stage is blocked"
 * is not something a colour alone can say.
 */
export function StageChip({
  label, status, statusLabel,
}: { label: string; status: string; statusLabel: string }) {
  return (
    <Badge
      variant={STAGE_VARIANT[status] ?? 'outline'}
      appearance="light"
      size="sm"
      shape="circle"
      title={`${label}: ${statusLabel}`}
    >
      {label}
      <span className="sr-only">: {statusLabel}</span>
    </Badge>
  );
}
