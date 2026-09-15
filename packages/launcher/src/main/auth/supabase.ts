/**
 * Supabase Auth, spoken directly — plain REST against
 * ${SUPABASE_URL}/auth/v1/*, deliberately not the Supabase JS SDK (same
 * reasoning as the old firebase-rest.ts: a browser library wanting a DOM has
 * no place in the main process for a handful of POSTs).
 *
 * Kept structurally identical to workspace/frontend/lib/supabase-auth.ts
 * (same function names/shapes) so a future shared `packages/auth` extraction
 * is a cut-and-paste, not a rewrite — see the auth architecture plan.
 */

import crypto from "crypto"
import { authFetch } from "./http"

// TODO(deploy): move these public values to environment-backed configuration.
// yet per the current plan). Both public/publishable — never a secret here.
// Must match workspace/frontend/lib/supabase-auth.ts and workspace/backend's
// SUPABASE_URL exactly (same project).
const SUPABASE_URL = "https://qhrzlmfzdeulhdzpadtn.supabase.co"
const SUPABASE_ANON_KEY = "sb_publishable_W_ITKg52Rr3G0eeoi4wPLQ_AdSTiKPD"

const TIMEOUT_MS = 15_000

export interface SupabaseSession {
  accessToken: string
  refreshToken: string
  /** Unix seconds. */
  expiresAt: number
  email: string
  username: string | null
}

export interface SignUpResult {
  session: SupabaseSession | null
  needsEmailConfirmation: boolean
}

function authUrl(path: string): string {
  return `${SUPABASE_URL}/auth/v1${path}`
}

async function post(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const res = await authFetch(authUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json", apikey: SUPABASE_ANON_KEY },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    const json = (await res.json().catch(() => null)) as Record<string, unknown> | null
    if (!res.ok || !json) {
      const message =
        (json?.msg as string | undefined) ||
        (json?.error_description as string | undefined) ||
        (json?.error as string | undefined) ||
        `HTTP ${res.status}`
      throw new Error(message)
    }
    return json
  } finally {
    clearTimeout(timer)
  }
}

function sessionFrom(data: Record<string, unknown>): SupabaseSession {
  const user = (data.user as Record<string, unknown>) || {}
  const metadata = (user.user_metadata as Record<string, unknown>) || {}
  return {
    accessToken: String(data.access_token || ""),
    refreshToken: String(data.refresh_token || ""),
    expiresAt: Math.floor(Date.now() / 1000) + (Number(data.expires_in) || 3600),
    email: String(user.email || ""),
    username: (metadata.username as string) || null,
  }
}

export async function sessionFromTokens(data: Record<string, unknown>): Promise<SupabaseSession> {
  const accessToken = String(data.access_token || "")
  if (!accessToken) throw new Error("SIGN_IN_REJECTED")
  const res = await authFetch(authUrl("/user"), {
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${accessToken}` },
  })
  const user = (await res.json().catch(() => null)) as Record<string, unknown> | null
  if (!res.ok || !user) throw new Error("SIGN_IN_REJECTED")
  return sessionFrom({ ...data, user })
}

export async function signInWithPassword(email: string, password: string): Promise<SupabaseSession> {
  const data = await post("/token?grant_type=password", { email, password })
  const session = sessionFrom(data)
  if (!session.accessToken) throw new Error("SIGN_IN_REJECTED")
  return session
}

export async function signUpWithPassword(
  email: string,
  password: string,
  username: string,
): Promise<SignUpResult> {
  const data = await post("/signup", { email, password, data: { username } })
  if (!data.access_token) return { session: null, needsEmailConfirmation: true }
  return { session: sessionFrom(data), needsEmailConfirmation: false }
}

export async function refreshSession(refreshToken: string): Promise<SupabaseSession> {
  const data = await post("/token?grant_type=refresh_token", { refresh_token: refreshToken })
  const session = sessionFrom(data)
  if (!session.accessToken) throw new Error("REFRESH_REJECTED")
  return session
}

export async function signOut(accessToken: string): Promise<void> {
  try {
    await authFetch(authUrl("/logout"), {
      method: "POST",
      headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${accessToken}` },
    })
  } catch {
    /* best-effort — the local session is cleared regardless */
  }
}

function base64UrlEncode(buf: Buffer): string {
  return buf.toString("base64url")
}

/** Build a Supabase OAuth (PKCE) authorize URL. The verifier stays in this
 * process (see account.ts) — the browser tab never sees it. */
export function buildOAuthAuthorizeUrl(
  provider: "google" | "github",
  redirectTo: string,
): { url: string; codeVerifier: string } {
  const codeVerifier = base64UrlEncode(crypto.randomBytes(32))
  const codeChallenge = base64UrlEncode(crypto.createHash("sha256").update(codeVerifier).digest())
  const params = new URLSearchParams({
    provider,
    redirect_to: redirectTo,
    code_challenge: codeChallenge,
    code_challenge_method: "s256",
  })
  return { url: authUrl(`/authorize?${params.toString()}`), codeVerifier }
}

/** Exchange the `code` the loopback handoff carried back for a session, using
 * the verifier generated alongside its authorize URL. */
export async function exchangeOAuthCode(code: string, codeVerifier: string): Promise<SupabaseSession> {
  const data = await post("/token?grant_type=pkce", { auth_code: code, code_verifier: codeVerifier })
  const session = sessionFrom(data)
  if (!session.accessToken) throw new Error("SIGN_IN_REJECTED")
  return session
}
