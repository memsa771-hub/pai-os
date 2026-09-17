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
  lastActivityAt: string | null;
  /**
   * A short-lived, READ-ONLY credential for the two places a browser cannot
   * send an Authorization header: the SSE stream and file URLs handed to
   * <img src>/<a href>. Everything else authenticates with the bearer.
   *
   * This replaces the workspace machine token, which this endpoint used to
   * return and the app used to keep in a 30-day cookie. That token is what
   * PAI's agents WRITE with; it no longer reaches the browser at all.
   */
  streamTicket: string | null;
  /** Seconds the ticket is good for, so the client can re-mint before it dies. */
  streamTicketTtl: number;
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

/** A fresh read-only ticket. Called on a timer, and after a 401 on a
 * ticketed URL, since tickets expire in minutes by design. */
export function refreshStreamTicket(
  idToken: string,
): Promise<{ streamTicket: string | null; streamTicketTtl: number }> {
  return bearerFetch('/v1/account/stream-ticket', idToken, { method: 'POST' });
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
