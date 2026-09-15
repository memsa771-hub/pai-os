// Thin, dependency-free Supabase Auth client — plain fetch against
// ${SUPABASE_URL}/auth/v1/*, deliberately not @supabase/supabase-js.
//
// Kept structurally identical to packages/launcher/src/main/auth/supabase.ts
// (same function names/shapes) so a future shared `packages/auth` extraction
// is a cut-and-paste, not a rewrite — see the auth architecture plan.
//
// TODO(deploy): move these public values to environment-backed configuration.
// They are intentionally hardcoded only for the current development phase.
// values — never put a service-role key or JWT secret here.
export const SUPABASE_URL = 'https://qhrzlmfzdeulhdzpadtn.supabase.co';
export const SUPABASE_ANON_KEY = 'sb_publishable_W_ITKg52Rr3G0eeoi4wPLQ_AdSTiKPD';

export interface AuthSession {
  accessToken: string;
  refreshToken: string;
  expiresAt: number; // unix seconds
  user: { id: string; email: string; username: string | null };
}

export interface SignUpResult {
  session: AuthSession | null;
  needsEmailConfirmation: boolean;
}

function authUrl(path: string): string {
  return `${SUPABASE_URL}/auth/v1${path}`;
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return { apikey: SUPABASE_ANON_KEY, 'Content-Type': 'application/json', ...extra };
}

function sessionFromSupabase(body: any): AuthSession {
  const user = body.user || {};
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

async function parseAuthError(res: Response): Promise<Error> {
  const body = await res.json().catch(() => null);
  return new Error(body?.msg || body?.error_description || body?.error || `HTTP ${res.status}`);
}

export async function signInWithPassword(email: string, password: string): Promise<AuthSession> {
  const res = await fetch(authUrl('/token?grant_type=password'), {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await parseAuthError(res);
  return sessionFromSupabase(await res.json());
}

export async function signUpWithPassword(
  email: string,
  password: string,
  username: string,
): Promise<SignUpResult> {
  const res = await fetch(authUrl('/signup'), {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ email, password, data: { username } }),
  });
  if (!res.ok) throw await parseAuthError(res);
  const body = await res.json();
  // Supabase returns a user with no access_token when email confirmation is
  // required (identities present but session absent).
  if (!body.access_token) return { session: null, needsEmailConfirmation: true };
  return { session: sessionFromSupabase(body), needsEmailConfirmation: false };
}

export async function refreshSession(refreshToken: string): Promise<AuthSession> {
  const res = await fetch(authUrl('/token?grant_type=refresh_token'), {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) throw await parseAuthError(res);
  return sessionFromSupabase(await res.json());
}

export async function signOut(accessToken: string): Promise<void> {
  await fetch(authUrl('/logout'), {
    method: 'POST',
    headers: authHeaders({ Authorization: `Bearer ${accessToken}` }),
  }).catch(() => {
    /* best-effort — the local session is cleared regardless */
  });
}

function base64UrlEncode(bytes: Uint8Array): string {
  let str = '';
  Array.from(bytes).forEach((b) => { str += String.fromCharCode(b); });
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function sha256(input: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
  return new Uint8Array(digest);
}

/** Build a Supabase OAuth (PKCE) authorize URL. Returns the verifier to store
 * client-side and present again in exchangeOAuthCode. */
export async function buildOAuthAuthorizeUrl(
  provider: 'google' | 'github',
  redirectTo: string,
): Promise<{ url: string; codeVerifier: string }> {
  const verifierBytes = crypto.getRandomValues(new Uint8Array(32));
  const codeVerifier = base64UrlEncode(verifierBytes);
  const codeChallenge = base64UrlEncode(await sha256(codeVerifier));
  const params = new URLSearchParams({
    provider,
    redirect_to: redirectTo,
    code_challenge: codeChallenge,
    code_challenge_method: 's256',
  });
  return { url: authUrl(`/authorize?${params.toString()}`), codeVerifier };
}

/** Exchange the `code` Supabase redirected back with (OAuth or email
 * confirmation) for a session, using the verifier stashed before redirecting. */
export async function exchangeOAuthCode(code: string, codeVerifier: string): Promise<AuthSession> {
  const res = await fetch(authUrl('/token?grant_type=pkce'), {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ auth_code: code, code_verifier: codeVerifier }),
  });
  if (!res.ok) throw await parseAuthError(res);
  return sessionFromSupabase(await res.json());
}

// The PKCE verifier only needs to survive the redirect to Supabase and back,
// within the same tab — sessionStorage, not localStorage.
const PKCE_VERIFIER_KEY = 'oa_pkce_verifier';

export function storePkceVerifier(verifier: string): void {
  try {
    sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier);
  } catch {
    /* ignore */
  }
}

/** Reads and clears the stashed verifier — single use, matching the code. */
export function consumePkceVerifier(): string | null {
  try {
    const v = sessionStorage.getItem(PKCE_VERIFIER_KEY);
    sessionStorage.removeItem(PKCE_VERIFIER_KEY);
    return v;
  } catch {
    return null;
  }
}

/** Build the authorize URL, stash the verifier, and navigate there — the
 * one-call form both /sign-in and /sign-up use for the OAuth buttons. */
export async function startOAuthRedirect(
  provider: 'google' | 'github',
  redirectTo: string = `${window.location.origin}/auth/callback`,
): Promise<void> {
  const { url, codeVerifier } = await buildOAuthAuthorizeUrl(provider, redirectTo);
  storePkceVerifier(codeVerifier);
  window.location.href = url;
}
