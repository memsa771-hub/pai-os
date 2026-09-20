'use client';

/**
 * Editing the top of the Profile.
 *
 * One dialog, two write paths, deliberately kept apart:
 *
 *  • Name and photo are ACCOUNT identity. They belong to the signed-in user
 *    across the whole app, so they go to /v1/account/profile — the same
 *    endpoint the retired Settings → Profile page used. The account system is
 *    untouched; only its UI moved here.
 *
 *  • Status, location, preferred name and interest are canonical STUDENT
 *    facts. They go through the Vault write path, so they carry provenance,
 *    supersede rather than overwrite, and are visible to PAI Counselor in the
 *    next conversation.
 *
 * Both halves save in one click; neither is allowed to write through the
 * other's path.
 */

import * as React from 'react';
import { Camera, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/responsive-dialog';
import { Separator } from '@/components/ui/separator';
import { workspaceApi } from '@/lib/api';
import { updateAccountProfile } from '@/lib/account-api';
import { usePaiAuth } from '@/lib/pai-auth-context';
import { useT } from '@/lib/i18n';
import { factLabel, type ProfileHeader } from '@/lib/student-profile';
import { extractMessage } from './record-edit-dialog';

const AVATAR_SIZE = 256;

/** The canonical Vault facts this dialog is allowed to write. */
const IDENTITY_FIELDS = [
  'identity.preferred_name',
  'identity.current_status',
  'location.current_city',
  'location.current_country',
  'career.primary_interest',
] as const;

/**
 * Downscale and square-crop a picked image to a small JPEG data URL, so the
 * stored avatar stays a few tens of KB (the backend caps the length).
 */
function fileToAvatarDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const side = Math.min(img.width, img.height);
      const canvas = document.createElement('canvas');
      canvas.width = AVATAR_SIZE;
      canvas.height = AVATAR_SIZE;
      const ctx = canvas.getContext('2d');
      if (!ctx) { reject(new Error('canvas unavailable')); return; }
      ctx.drawImage(
        img,
        (img.width - side) / 2, (img.height - side) / 2, side, side,
        0, 0, AVATAR_SIZE, AVATAR_SIZE,
      );
      resolve(canvas.toDataURL('image/jpeg', 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('not an image')); };
    img.src = url;
  });
}

export function ProfileHeaderEditDialog({
  header,
  facts,
  open,
  onOpenChange,
  onSaved,
}: {
  header: ProfileHeader;
  facts: Record<string, unknown>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const t = useT();
  const { idToken } = usePaiAuth();
  const fileInput = React.useRef<HTMLInputElement>(null);

  const [name, setName] = React.useState('');
  // null = untouched; '' = cleared; 'data:…' = newly picked.
  const [avatarDraft, setAvatarDraft] = React.useState<string | null>(null);
  const [identity, setIdentity] = React.useState<Record<string, string>>({});
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    setName(header.displayName || '');
    setAvatarDraft(null);
    setIdentity(Object.fromEntries(IDENTITY_FIELDS.map((key) => {
      const value = facts[key];
      return [key, typeof value === 'string' ? value : ''];
    })));
  }, [open, header, facts]);

  const pickFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      setAvatarDraft(await fileToAvatarDataUrl(file));
    } catch {
      toast.error(t('profile.badImage'));
    }
  };

  const shownAvatar = avatarDraft !== null ? (avatarDraft || null) : (header.avatarUrl || null);
  const initial = (name || header.displayName || header.email || '?')[0]?.toUpperCase();

  const save = async () => {
    setSaving(true);
    try {
      // 1. Account identity — the existing account-profile path, unchanged.
      const accountChanged =
        name.trim() !== (header.displayName || '') || avatarDraft !== null;
      if (accountChanged && idToken && name.trim()) {
        await updateAccountProfile(idToken, {
          ...(name.trim() !== (header.displayName || '') ? { displayName: name.trim() } : {}),
          ...(avatarDraft !== null ? { avatarUrl: avatarDraft } : {}),
        });
      }

      // 2. Canonical student facts — one validated Vault write per changed
      //    field, so each keeps its own provenance and history.
      for (const key of IDENTITY_FIELDS) {
        const next = (identity[key] || '').trim();
        const before = typeof facts[key] === 'string' ? (facts[key] as string) : '';
        if (next === before || !next) continue;
        await workspaceApi.editStudentProfile({
          fieldKey: key, value: next, reason: t('studentProfile.editReason'),
        });
      }

      toast.success(t('studentProfile.saved'));
      onOpenChange(false);
      onSaved();
    } catch (e) {
      toast.error(extractMessage(e) || t('studentProfile.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('studentProfile.editIdentity')}</DialogTitle>
          <DialogDescription>{t('studentProfile.editHint')}</DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-5">
          {/* Account identity */}
          <div className="flex items-start gap-4">
            <div className="relative shrink-0">
              {shownAvatar ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={shownAvatar} alt="" className="size-16 rounded-full object-cover" />
              ) : (
                <div className="flex size-16 items-center justify-center rounded-full bg-primary/10 text-xl font-semibold text-primary">
                  {initial}
                </div>
              )}
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                title={t('profile.changePhoto')}
                className="absolute -end-1 -bottom-1 flex size-6 items-center justify-center rounded-full border bg-background shadow-sm transition-colors hover:bg-muted"
              >
                <Camera className="size-3" />
              </button>
              <input
                ref={fileInput}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = ''; }}
              />
            </div>

            <div className="min-w-0 flex-1 space-y-1.5">
              <Label className="text-xs">{t('profile.displayName')}</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('profile.displayNamePlaceholder')}
                maxLength={120}
              />
              {header.email && (
                <p className="truncate text-xs text-muted-foreground">{header.email}</p>
              )}
              {shownAvatar && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setAvatarDraft('')}
                >
                  <X className="size-3" />
                  {t('profile.removePhoto')}
                </Button>
              )}
            </div>
          </div>

          <Separator />

          {/* Canonical student facts */}
          <div className="grid grid-cols-2 gap-3">
            {IDENTITY_FIELDS.map((key) => (
              <IdentityField
                key={key}
                fieldKey={key}
                label={factLabel(t, key)}
                wide={key === 'identity.current_status' || key === 'career.primary_interest'}
                value={identity[key] || ''}
                onChange={(v) => setIdentity((d) => ({ ...d, [key]: v }))}
              />
            ))}
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            {t('studentProfile.cancel')}
          </Button>
          <Button onClick={save} disabled={saving || !name.trim()}>
            {saving ? t('studentProfile.saving') : t('studentProfile.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** A labelled Vault-fact box; see FieldRow in record-edit-dialog for the why. */
function IdentityField({ fieldKey, label, wide, value, onChange }: {
  fieldKey: string;
  label: string;
  wide: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = React.useId();
  return (
    <div className={wide ? 'col-span-2 space-y-1.5' : 'col-span-1 space-y-1.5'}>
      <Label htmlFor={id} className="text-xs">{label}</Label>
      <Input
        id={id}
        name={fieldKey}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
