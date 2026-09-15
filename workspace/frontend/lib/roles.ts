import type { TranslateFn } from './i18n';
import type { WorkspaceRole } from './types';

/**
 * Human-readable role names.
 *
 * The API speaks `owner`/`admin`/`member`/`viewer`; this maps each onto its
 * display label via the message catalogue rather than printing the raw value
 * verbatim in a picker, a badge and a sentence.
 *
 * An unknown value comes through untranslated rather than blank: a role the UI
 * has not heard of is still information, and hiding it would leave a member row
 * looking as if it had no role at all.
 */
const ROLE_KEYS = {
  owner: 'admin.roleOwner',
  admin: 'admin.roleAdmin',
  member: 'admin.roleMember',
  viewer: 'admin.roleViewer',
} as const;

export function roleLabel(t: TranslateFn, role?: string | null): string {
  if (!role) return '';
  const key = ROLE_KEYS[role as WorkspaceRole];
  return key ? t(key) : role;
}
