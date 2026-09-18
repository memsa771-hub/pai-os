import { apiBase, webBase } from "./endpoints"
import { authFetch } from "./http"
import { registrationPasswordError } from "../../shared/account-registration"
import * as supabase from "./supabase"
import { startHandoffServer, type Handoff } from "./handoff-server"
import {
  clearSession,
  isFresh,
  loadSession,
  saveSession,
  toAccountInfo,
  type AccountInfo,
  type AccountSession,
} from "./session-store"

/**
 * The signed-in user, as the main process holds them.
 *
 * Account identity lives here rather than in the renderer because two different
 * origins need it: the launcher's own UI (a file:// page) and the embedded
 * workspace view (https://app.placement-ai.com). Browser auth state is
 * per-origin and cannot be shared between them — only main can feed both.
 *
 * Signing in is a workspace-scoped gate, never an app-scoped one: everything
 * under My Agents works signed out, and nothing here is touched until the user
 * asks for something that is theirs (their workspaces, authorizing this
 * machine).
 *
 * Identity is Supabase Auth, spoken directly (see supabase.ts) — the same
 * project every client (web, desktop, and later mobile) authenticates
 * against. workspace/backend verifies the resulting access token itself; this
 * process never mints its own session format.
 */

/** One membership from GET /v1/account/workspaces. */
export interface AccountWorkspace {
  workspaceId: string
  name: string
  slug: string
  // No `token`: the backend stopped returning the workspace machine token to
  // clients (see workspace/backend/app/stream_ticket.py). Nothing here read
  // it — the embedded workspace view authenticates with the Supabase bearer.
  role: "owner" | "admin" | "member" | "viewer"
  lastActivityAt: string | null
}

export interface AccountDeps {
  /** The configured workspace endpoint, if the user set one. */
  endpoint: () => string | undefined
  /** Hand a URL to the OS browser (web-security's openExternalSafely). */
  openExternal: (url: string) => void
  /** Told whenever the account appears, changes or goes away. */
  onChange: (account: AccountInfo | null) => void
}

export interface SignUpOutcome {
  account: AccountInfo | null
  /** True when Supabase requires confirming the email before a session
   * exists — the caller should show a "check your email" state, not treat
   * this as a failure. */
  needsEmailConfirmation: boolean
}

/** Codes the sign-in form turns into wording; see renderer/lib/account-errors. */
const BAD_CREDENTIALS = "SIGN_IN_BAD_CREDENTIALS"
const TOO_MANY_ATTEMPTS = "SIGN_IN_TOO_MANY_ATTEMPTS"

/** Supabase's own names for "that is not a valid login". */
const REJECTION_PATTERN = /invalid_grant|Invalid login credentials|invalid_credentials/i

/**
 * The browser round trip needs a page on the workspace site to come back
 * through. A deployment that predates it would land the user on a bare 404
 * saying "workspace not found", which explains nothing and looks like their
 * account is broken.
 */
const BROWSER_UNAVAILABLE = "SIGN_IN_BROWSER_UNAVAILABLE"

/** workspace/backend's success code in its {code, message, data} envelope. */
const API_SUCCESS = 0

export class AccountManager {
  private _session: AccountSession | null = null
  private _loaded = false
  private _pending: Handoff | null = null
  /** The PKCE code verifier for the OAuth round trip in flight — generated
   * alongside its authorize URL, consumed once the loopback handoff returns
   * the code. Never leaves this process. */
  private _pendingCodeVerifier: string | null = null

  constructor(private _deps: AccountDeps) {}

  /** The account, or null. Reads the stored session on first use. */
  getAccount(): AccountInfo | null {
    this._ensureLoaded()
    return this._session ? toAccountInfo(this._session) : null
  }

  private _ensureLoaded(): void {
    if (this._loaded) return
    this._loaded = true
    this._session = loadSession()
  }

  private _set(session: AccountSession | null): void {
    this._session = session
    this._loaded = true
    if (session) saveSession(session)
    else clearSession()
    this._deps.onChange(session ? toAccountInfo(session) : null)
  }

  private _fromSupabase(session: supabase.SupabaseSession): AccountSession {
    return {
      kind: "supabase",
      token: session.accessToken,
      refreshToken: session.refreshToken,
      email: session.email,
      displayName: session.username,
      expiresAt: session.expiresAt,
    }
  }

  /**
   * Run a sign-in: open Supabase's OAuth authorize page in the user's browser
   * and wait for the loopback callback page to hand the resulting code back.
   *
   * The system browser, not an in-app window: Google and GitHub refuse OAuth
   * in an embedded webview ("this browser is not secure"), and the user's
   * existing session there is usually what makes this one click.
   */
  async signIn(provider: "google" | "github"): Promise<AccountInfo> {
    // A second click while one is in flight replaces it — the first browser tab
    // is already stale, and leaving its port open would be a second live gate.
    this.cancelSignIn()

    const origin = webBase(this._deps.endpoint())
    // Checked before a port is opened and a browser is launched: without the
    // landing page there is no way back into the app, and finding that out
    // after the user has signed in wastes the whole trip.
    if (!(await landingPageExists(origin))) throw new Error(BROWSER_UNAVAILABLE)

    const handoff = await startHandoffServer(origin)
    this._pending = handoff

    const redirectTo = `${origin}/auth/desktop?port=${handoff.port}&state=${encodeURIComponent(handoff.state)}`
    const { url, codeVerifier } = supabase.buildOAuthAuthorizeUrl(provider, redirectTo)
    this._pendingCodeVerifier = codeVerifier
    this._deps.openExternal(url)

    try {
      const result = await handoff.result
      if (result.error) throw new Error(result.error)
      if (!result.code) throw new Error("SIGN_IN_EMPTY_HANDOFF")

      const verifier = this._pendingCodeVerifier
      if (!verifier) throw new Error("SIGN_IN_EMPTY_HANDOFF")
      const session = await supabase.exchangeOAuthCode(result.code, verifier)
      this._set(this._fromSupabase(session))
      return toAccountInfo(this._session!)
    } finally {
      handoff.close()
      if (this._pending === handoff) this._pending = null
      this._pendingCodeVerifier = null
    }
  }

  /**
   * The session as the embedded workspace view needs it — token included.
   *
   * Synchronous because the view's preload has to plant it before the page's
   * first script runs; callers that can afford to await should call `bearer()`
   * first so what is planted is freshly renewed.
   */
  embeddedSession(): {
    token: string
    email: string
    displayName: string | null
    expiresAt: number
  } | null {
    this._ensureLoaded()
    if (!this._session) return null
    const { token, email, displayName, expiresAt } = this._session
    return { token, email, displayName, expiresAt }
  }

  /** Sign in with an email and password, without leaving the app. */
  async signInWithPassword(email: string, password: string): Promise<AccountInfo> {
    this.cancelSignIn()
    try {
      const session = await supabase.signInWithPassword(email, password)
      this._set(this._fromSupabase(session))
      return toAccountInfo(this._session!)
    } catch (err) {
      throw new Error(translateSupabaseError((err as Error).message))
    }
  }

  /** Sign in with a username and password — resolved to an email server-side
   * only, by workspace/backend, which never returns it to us. */
  async signInWithUsername(username: string, password: string): Promise<AccountInfo> {
    this.cancelSignIn()
    const res = await authFetch(`${apiBase(this._deps.endpoint())}/v1/auth/sign-in-username`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    })
    const json = (await res.json().catch(() => null)) as {
      code?: number
      data?: Record<string, unknown>
      message?: string
    } | null
    if (res.status === 429 || json?.code === 429) throw new Error(TOO_MANY_ATTEMPTS)
    // workspace/backend's envelope is {code, message, data} where SUCCESS is
    // ZERO, not 200 (app/response.py: ResponseCode.SUCCESS = 0). This checked
    // for 200 and so rejected every successful sign-in as a bad credential.
    // The unit test did not catch it because its fetch mock returned code: 200
    // too — the mock encoded the same wrong assumption as the code.
    if (!res.ok || json?.code !== API_SUCCESS || !json.data) {
      throw new Error(BAD_CREDENTIALS)
    }
    const session = await supabase.sessionFromTokens(json.data)
    this._set(this._fromSupabase(session))
    return toAccountInfo(this._session!)
  }

  /** Register a new account. Returns needsEmailConfirmation: true (and a
   * null account) when Supabase requires confirming the email first — the
   * caller shows a "check your email" state rather than treating it as a
   * failure. The username is sent as signup metadata immediately; it is
   * attached to the workspace-backend account (claim-username) once a real
   * session exists, here or after confirmation. */
  async signUpWithPassword(
    email: string,
    password: string,
    username: string,
  ): Promise<SignUpOutcome> {
    const normalizedEmail = email.trim().toLowerCase()
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail))
      throw new Error("SIGN_UP_INVALID_EMAIL")
    const passwordError = registrationPasswordError(password)
    if (passwordError) throw new Error(passwordError)
    this.cancelSignIn()

    const normalizedUsername = username.trim().toLowerCase()
    if (!(await this._usernameAvailable(normalizedUsername))) {
      throw new Error("SIGN_UP_USERNAME_TAKEN")
    }

    let result: supabase.SignUpResult
    try {
      result = await supabase.signUpWithPassword(normalizedEmail, password, normalizedUsername)
    } catch (err) {
      const message = (err as Error).message
      if (/already registered|user_already_exists/i.test(message)) throw new Error("SIGN_UP_EMAIL_EXISTS")
      throw new Error(translateSupabaseError(message))
    }

    if (!result.session) return { account: null, needsEmailConfirmation: true }

    this._set(this._fromSupabase(result.session))
    await this._claimUsername(result.session.accessToken, normalizedUsername).catch((error) => {
      throw error
      /* non-fatal — the account exists either way */
    })
    return { account: toAccountInfo(this._session!), needsEmailConfirmation: false }
  }

  private async _usernameAvailable(username: string): Promise<boolean> {
    const res = await authFetch(`${apiBase(this._deps.endpoint())}/v1/auth/username-available`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username }),
    })
    const json = (await res.json().catch(() => null)) as { data?: { available?: boolean }; message?: string } | null
    if (!res.ok) throw new Error(json?.message || `HTTP ${res.status}`)
    return json?.data?.available === true
  }

  private async _claimUsername(accessToken: string, username: string): Promise<void> {
    const res = await authFetch(`${apiBase(this._deps.endpoint())}/v1/auth/claim-username`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify({ username }),
    })
    if (res.status === 409) throw new Error("SIGN_UP_USERNAME_RACE")
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
  }

  /** Give the loopback port back without waiting for the browser. */
  cancelSignIn(): void {
    this._pending?.close()
    this._pending = null
    this._pendingCodeVerifier = null
  }

  signOut(): void {
    this.cancelSignIn()
    const session = this._session
    this._set(null)
    if (session) void supabase.signOut(session.token)
  }

  /** The bearer to send, renewed if it is about to lapse. */
  async bearer(): Promise<string> {
    this._ensureLoaded()
    const session = this._session
    if (!session) throw new Error("NOT_SIGNED_IN")
    if (isFresh(session)) return session.token

    try {
      const renewed = await supabase.refreshSession(session.refreshToken)
      this._set(this._fromSupabase(renewed))
      return renewed.accessToken
    } catch {
      this._set(null)
      throw new Error("SESSION_EXPIRED")
    }
  }

  /**
   * The user's workspaces. The endpoint also reconciles legacy access and
   * gives a brand-new account an empty workspace to own, so a signed-in user
   * always has at least one.
   */
  async listWorkspaces(): Promise<AccountWorkspace[]> {
    return this._get<AccountWorkspace[]>("/v1/account/workspaces")
  }

  private _get<T>(path: string): Promise<T> {
    return this._request<T>(path, { method: "GET" })
  }

  private async _request<T>(path: string, init: RequestInit): Promise<T> {
    const token = await this.bearer()
    const res = await authFetch(`${apiBase(this._deps.endpoint())}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
    })
    const json = (await res.json().catch(() => null)) as {
      data?: T
      message?: string
    } | null
    if (res.status === 401) {
      // The server has the last word on whether we are still someone: drop the
      // session rather than leave the UI showing an account that cannot act.
      this._set(null)
      throw new Error("SESSION_EXPIRED")
    }
    if (!res.ok) throw new Error(json?.message || `HTTP ${res.status}`)
    return json?.data as T
  }
}

/** POST /v1/auth/sign-in-username relays Supabase's own token response
 * shape verbatim — parse it the same way supabase.ts does. */
function supabaseSessionFromBackend(data: Record<string, unknown>): supabase.SupabaseSession {
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

/**
 * Whether this deployment serves /auth/desktop — the page a browser sign-in
 * returns through. Any answer other than "not there" counts as present: a
 * network blip must not be reported as a missing feature.
 */
async function landingPageExists(webOrigin: string): Promise<boolean> {
  try {
    const res = await authFetch(`${webOrigin}/auth/desktop`, { method: "GET" })
    return res.status !== 404
  } catch {
    return true
  }
}

function translateSupabaseError(message: string): string {
  if (REJECTION_PATTERN.test(message)) return BAD_CREDENTIALS
  if (/rate limit|too many/i.test(message)) return TOO_MANY_ATTEMPTS
  return message
}
