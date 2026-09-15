// Canonical Workspace API — Placement AI v2.0: one student account = one
// permanent personal workspace, served by the workspace backend's
// GET /v1/account/workspace. That endpoint creates the workspace (with PAI
// Counselor + the canonical welcome conversation) the first time it's called
// for a given user, and returns the same one on every call after that —
// there is no picker, no "create another workspace", no membership list.

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://workspace-endpoint.openagents.org';

export interface AccountWorkspace {
  workspaceId: string;
  slug: string;
  name: string;
  token: string | null;
  lastActivityAt: string | null;
}

async function bearerFetch<T>(path: string, idToken: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${idToken}`,
      ...options.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.message || body?.detail || `API error (${res.status})`);
  }
  const json = await res.json();
  return json.data as T;
}

/** The signed-in student's one canonical personal workspace — created on
 * first call, the same one returned on every call after that. */
export function getAccountWorkspace(idToken: string): Promise<AccountWorkspace> {
  return bearerFetch<AccountWorkspace>('/v1/account/workspace', idToken);
}

/** The signed-in user's cross-workspace profile (name + avatar). */
export interface AccountProfile {
  email: string;
  displayName: string | null;
  /** https:// URL or a small data:image/... URL; null = no custom avatar. */
  avatarUrl: string | null;
  /** Whether this account has watched (or skipped) the welcome intro. */
  welcomeSeen?: boolean;
}

export function getAccountProfile(idToken: string): Promise<AccountProfile> {
  return bearerFetch<AccountProfile>('/v1/account/profile', idToken);
}

/** Omitted fields are left untouched; an empty-string avatarUrl clears it. */
export function updateAccountProfile(
  idToken: string,
  updates: { displayName?: string; avatarUrl?: string; welcomeSeen?: boolean },
): Promise<AccountProfile> {
  return bearerFetch<AccountProfile>('/v1/account/profile', idToken, {
    method: 'PATCH',
    body: JSON.stringify({
      ...(updates.displayName !== undefined ? { display_name: updates.displayName } : {}),
      ...(updates.avatarUrl !== undefined ? { avatar_url: updates.avatarUrl } : {}),
      ...(updates.welcomeSeen !== undefined ? { welcome_seen: updates.welcomeSeen } : {}),
    }),
  });
}
