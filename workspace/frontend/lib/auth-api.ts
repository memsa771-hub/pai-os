// The two auth endpoints workspace/backend provides beyond plain Supabase
// calls: attaching a unique username, and signing in by username without the
// client ever learning the resolved email. See workspace/backend/app/routers/auth.py.
import { SUPABASE_ANON_KEY, SUPABASE_URL, type AuthSession } from './supabase-auth';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://workspace-endpoint.openagents.org';

async function parseError(res: Response): Promise<Error> {
  const body = await res.json().catch(() => null);
  return new Error(body?.message || `API error (${res.status})`);
}

export async function isUsernameAvailable(username: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/v1/auth/username-available`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  });
  if (!res.ok) throw await parseError(res);
  const json = await res.json();
  return json.data?.available === true;
}

/** Attach a username to the caller's account. Idempotent for a username the
 * caller already owns. Throws with the backend's message on conflict/failure. */
export async function claimUsername(accessToken: string, username: string): Promise<void> {
  const res = await fetch(`${API_URL}/v1/auth/claim-username`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ username }),
  });
  if (!res.ok) throw await parseError(res);
}

/** Sign in with username+password — resolved to an email server-side only;
 * the client never sees it. */
export async function signInWithUsername(username: string, password: string): Promise<AuthSession> {
  const res = await fetch(`${API_URL}/v1/auth/sign-in-username`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw await parseError(res);
  const json = await res.json();
  const body = json.data;
  const userRes = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${body.access_token}` },
  });
  if (!userRes.ok) throw new Error('Could not load authenticated user');
  const user = await userRes.json();
  return {
    accessToken: body.access_token,
    refreshToken: body.refresh_token,
    expiresAt: Math.floor(Date.now() / 1000) + (body.expires_in ?? 3600),
    user: {
      id: user.id,
      email: user.email,
      username: user.user_metadata?.username ?? null,
    },
  };
}
