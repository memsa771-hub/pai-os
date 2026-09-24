'use client';

/**
 * The first thing a new student sees.
 *
 * One page, eight required identity fields. The point is not to collect a
 * profile — PAI builds that over time — it is to make the FIRST conversation
 * useful: knowing someone's name, whether they study or work, and where they
 * are turns a cold "tell me about yourself" into real advice straight away.
 *
 * Everything here is rendered from the field list the backend sends, so
 * adding a question is a backend change alone. The layout hints in
 * FIELD_LAYOUT are the only thing this file owns, and a field missing from
 * them still renders as a text box rather than disappearing.
 *
 * Visually it is the workspace's own language — same tokens, same Input /
 * Select / Button components, same muted surfaces — so it reads as PAI
 * opening a conversation, not as a signup wall bolted on in front of it.
 */

import * as React from 'react';
import Image from 'next/image';
import { ArrowRight, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { workspaceApi } from '@/lib/api';
import { useT, type MessageKey } from '@/lib/i18n';
import {
  FIELD_LAYOUT,
  type OnboardingAnswers, type OnboardingFieldSpec, type OnboardingState,
} from '@/lib/onboarding';

/** Pull the human message out of an API error like `API 400: {json}`. */
function errorMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  const brace = raw.indexOf('{');
  if (brace >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(brace));
      if (parsed && typeof parsed.message === 'string') return parsed.message;
    } catch { /* not JSON */ }
  }
  return raw.startsWith('API ') ? '' : raw;
}

export function OnboardingView({ state, onDone }: {
  state: OnboardingState;
  onDone: () => void;
}) {
  const t = useT();
  const [answers, setAnswers] = React.useState<OnboardingAnswers>(state.prefill || {});
  const [busy, setBusy] = React.useState(false);

  const set = (name: string, value: string) =>
    setAnswers((prev) => ({ ...prev, [name]: value }));

  const label = (name: string) => {
    const key = `onboarding.field.${name}`;
    const text = t(key as MessageKey);
    return text === key ? name : text;
  };

  const missingRequired = state.fields.some(
    (f) => f.required && !(answers[f.name] || '').trim(),
  );

  const submit = async () => {
    setBusy(true);
    try {
      const result = await workspaceApi.submitOnboarding(answers);
      // The backend reports per-field rejections rather than failing the whole
      // call. Saying "saved" over a rejected field would be a lie about
      // canonical state, so surface it.
      const rejected = Object.keys(result.rejected || {});
      if (rejected.length || !result.completed) {
        toast.warning(t('onboarding.partial', { count: rejected.length }));
        setBusy(false);
      } else {
        toast.success(t('onboarding.saved'));
        onDone();
      }
    } catch (e) {
      toast.error(errorMessage(e) || t('onboarding.failed'));
      setBusy(false);
    }
  };

  return (
    /* `w-full` matters: LayoutProvider wraps children in `flex grow`, so
       without it this sizes to its content and the body background
       shows through beside the form. */
    <div className="h-svh w-full overflow-y-auto bg-background">
      <div className="mx-auto w-full max-w-lg px-5 py-10 sm:py-14">
        <div className="flex flex-col items-center text-center">
          <Image
            src="/pai-emblem.png"
            alt="Placement AI"
            width={40}
            height={40}
            className="size-10 object-contain"
          />
          <h1 className="mt-4 text-xl font-semibold tracking-tight sm:text-2xl">
            {t('onboarding.title')}
          </h1>
          <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
            {t('onboarding.subtitle')}
          </p>
        </div>

        <div className="mt-8 grid grid-cols-2 gap-3">
          {state.fields.map((field) => (
            <Field
              key={field.name}
              field={field}
              label={label(field.name)}
              value={answers[field.name] || ''}
              onChange={(v) => set(field.name, v)}
            />
          ))}
        </div>

        <p className="mt-4 text-xs text-muted-foreground">{t('onboarding.privacy')}</p>

        <div className="mt-6 flex items-center gap-2">
          <Button
            className="flex-1"
            onClick={submit}
            disabled={busy || missingRequired}
          >
            {busy
              ? <Loader2 className="size-4 animate-spin" />
              : <>{t('onboarding.continue')}<ArrowRight className="size-4" /></>}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({ field, label, value, onChange }: {
  field: OnboardingFieldSpec;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const t = useT();
  // A field the backend added but the layout map has not caught up with still
  // renders — full width, plain text box — rather than silently vanishing.
  const layout = FIELD_LAYOUT[field.name] || {};
  // Explicitly tied to the control: tapping the label focuses it, and a screen
  // reader announces the two together instead of reading an unlabelled box.
  const id = React.useId();

  return (
    <div className={layout.half ? 'col-span-1 space-y-1.5' : 'col-span-2 space-y-1.5'}>
      <Label htmlFor={id} className="text-xs">
        {label}
        {field.required && <span className="ms-1 text-destructive">*</span>}
      </Label>

      {field.choices.length > 0 ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-full">
            <SelectValue placeholder={t('onboarding.choose')} />
          </SelectTrigger>
          <SelectContent>
            {field.choices.map((choice) => {
              const key = `onboarding.choice.${choice}`;
              const text = t(key as MessageKey);
              return (
                <SelectItem key={choice} value={choice}>
                  {text === key ? choice : text}
                </SelectItem>
              );
            })}
          </SelectContent>
        </Select>
      ) : field.isDate ? (
        <Input
          id={id}
          type="date"
          value={value}
          max={new Date().toISOString().slice(0, 10)}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <Input
          id={id}
          value={value}
          placeholder={layout.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
