import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AccountManager } from "./account"
import type { AccountSession } from "./session-store"

// No Electron in a unit test, so authFetch falls through to the global fetch
// the cases below stub. See auth/http.ts.
vi.mock("electron", () => ({ net: {} }))

/**
 * The sign-in as a whole: what URL the browser is sent to, and what the app
 * ends up holding when the page answers.
 *
 * session-store is mocked because the real one writes through Electron's
 * userData path; everything else here is the real thing, loopback server
 * included, with fetch standing in for the browser and for Supabase.
 */

// Must match vitest.config.ts's test.env.SUPABASE_URL — dummy, test-only,
// not a real project.
const SUPABASE_URL = "https://test-project.supabase.co"
const API_BASE = "https://api.placement-ai.com"

let stored: AccountSession | null = null

vi.mock("./session-store", async () => {
  const actual =
    await vi.importActual<typeof import("./session-store")>("./session-store")
  return {
    ...actual,
    loadSession: () => stored,
    saveSession: (s: AccountSession) => {
      stored = s
    },
    clearSession: () => {
      stored = null
    },
  }
})

const SUPABASE_SESSION_BODY = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  expires_in: 3600,
  user: { id: "u1", email: "a@example.com", user_metadata: { username: "abby" } },
}

beforeEach(() => {
  stored = null
})

/**
 * The real fetch, captured before anything stubs it: the stand-in browser below
 * genuinely posts to the loopback server, and must keep doing so while the
 * landing-page probe beside it is answering from a stub.
 */
const realFetch = globalThis.fetch

/**
 * The landing-page probe runs before any browser opens; a present page is the
 * normal case, so it is stubbed once for the whole suite. The case where it is
 * missing has its own test.
 */
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })),
  )
})

afterEach(() => vi.unstubAllGlobals())

/** Stand in for the browser: read the opened authorize URL, POST a code back
 * to the loopback port, as the /auth/desktop page would. */
function browserThatSignsIn(respond: (target: URL) => unknown): {
  opened: string[]
  openExternal: (url: string) => void
} {
  const opened: string[] = []
  return {
    opened,
    openExternal: (url) => {
      opened.push(url)
      const target = new URL(url)
      const redirectTo = new URL(target.searchParams.get("redirect_to") || "")
      const port = redirectTo.searchParams.get("port")
      const state = redirectTo.searchParams.get("state")
      void realFetch(`http://127.0.0.1:${port}/desktop-auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state, ...(respond(target) as object) }),
      })
    },
  }
}

describe("AccountManager.signIn", () => {
  it("opens Supabase's authorize URL with a desktop redirect_to, PKCE challenge included", async () => {
    const browser = browserThatSignsIn(() => ({ code: "auth-code-1" }))
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("/auth/desktop")) return { ok: true, status: 200, json: async () => ({}) }
      if (url.includes("grant_type=pkce")) return { ok: true, status: 200, json: async () => SUPABASE_SESSION_BODY }
      return { ok: true, status: 200, json: async () => ({}) }
    }))
    const manager = new AccountManager({
      endpoint: () => undefined,
      openExternal: browser.openExternal,
      onChange: () => {},
    })

    await manager.signIn("google")

    const opened = new URL(browser.opened[0])
    expect(opened.origin).toBe(SUPABASE_URL)
    expect(opened.pathname).toBe("/auth/v1/authorize")
    expect(opened.searchParams.get("provider")).toBe("google")
    expect(opened.searchParams.get("code_challenge_method")).toBe("s256")
    const redirectTo = new URL(opened.searchParams.get("redirect_to")!)
    expect(redirectTo.origin).toBe("https://app.placement-ai.com")
    expect(redirectTo.pathname).toBe("/auth/desktop")
  })

  it("refuses before opening a browser it cannot be answered from", async () => {
    // A deployment without /auth/desktop has no way back into the app; finding
    // that out after the user signed in would waste the whole trip.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) })),
    )
    const opened: string[] = []
    const manager = new AccountManager({
      endpoint: () => undefined,
      openExternal: (url) => opened.push(url),
      onChange: () => {},
    })

    await expect(manager.signIn("google")).rejects.toThrow(
      "SIGN_IN_BROWSER_UNAVAILABLE",
    )
    expect(opened).toHaveLength(0)
  })

  it("exchanges the returned code for a session and reports the account", async () => {
    const seen: Array<unknown> = []
    const browser = browserThatSignsIn(() => ({ code: "auth-code-1" }))
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("grant_type=pkce")) return { ok: true, status: 200, json: async () => SUPABASE_SESSION_BODY }
      return { ok: true, status: 200, json: async () => ({}) }
    }))
    const manager = new AccountManager({
      endpoint: () => undefined,
      openExternal: browser.openExternal,
      onChange: (info) => seen.push(info),
    })

    const account = await manager.signIn("github")

    expect(account.email).toBe(SUPABASE_SESSION_BODY.user.email)
    expect(stored?.kind).toBe("supabase")
    expect(stored?.refreshToken).toBe("refresh-1")
    expect(await manager.bearer()).toBe("access-1")
    expect(seen).toHaveLength(1)
  })

  it("surfaces the reason when the page reports one", async () => {
    const browser = browserThatSignsIn(() => ({ error: "SIGN_IN_REJECTED" }))
    const manager = new AccountManager({
      endpoint: () => undefined,
      openExternal: browser.openExternal,
      onChange: () => {},
    })

    await expect(manager.signIn("google")).rejects.toThrow("SIGN_IN_REJECTED")
    expect(stored).toBeNull()
  })

  it("ends a session whose refresh token no longer works rather than sending a stale token", async () => {
    stored = {
      kind: "supabase",
      token: "stale",
      refreshToken: "stale-refresh",
      email: "a@example.com",
      displayName: null,
      expiresAt: Math.floor(Date.now() / 1000) - 10,
    }
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 400, json: async () => ({ error: "invalid_grant" }) })))
    const gone: Array<unknown> = []
    const manager = new AccountManager({
      endpoint: () => undefined,
      openExternal: () => {},
      onChange: (info) => gone.push(info),
    })

    await expect(manager.bearer()).rejects.toThrow("SESSION_EXPIRED")
    expect(stored).toBeNull()
    expect(gone).toEqual([null])
  })
})

describe("AccountManager.signInWithPassword", () => {
  it("signs in directly against Supabase", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe(`${SUPABASE_URL}/auth/v1/token?grant_type=password`)
      return { ok: true, status: 200, json: async () => SUPABASE_SESSION_BODY }
    })
    vi.stubGlobal("fetch", fetchMock)
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: () => {}, onChange: () => {} })

    const account = await manager.signInWithPassword("a@example.com", "pw")

    expect(account.email).toBe(SUPABASE_SESSION_BODY.user.email)
    expect(stored?.kind).toBe("supabase")
  })

  it("reports a rejected password without a raw Supabase error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid_grant", error_description: "Invalid login credentials" }),
    })))
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: () => {}, onChange: () => {} })

    await expect(manager.signInWithPassword("a@example.com", "wrong")).rejects.toThrow("SIGN_IN_BAD_CREDENTIALS")
    expect(stored).toBeNull()
  })
})

describe("AccountManager.signInWithUsername", () => {
  it("asks workspace/backend, which never returns the resolved email", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `${API_BASE}/v1/auth/sign-in-username`) {
        expect(JSON.parse(String(init?.body))).toEqual({ username: "abby", password: "pw" })
        // code 0 is SUCCESS in workspace/backend's envelope — see
        // app/response.py. This mock used to say 200, which no endpoint ever
        // returns, so the test passed against a shape the server never sends
        // while real sign-ins were rejected as bad credentials.
        return { ok: true, status: 200, json: async () => ({ code: 0, data: {
          access_token: "access-1", refresh_token: "refresh-1", expires_in: 3600, token_type: "bearer",
        } }) }
      }
      expect(url).toBe(`${SUPABASE_URL}/auth/v1/user`)
      return { ok: true, status: 200, json: async () => SUPABASE_SESSION_BODY.user }
    })
    vi.stubGlobal("fetch", fetchMock)
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: () => {}, onChange: () => {} })

    const account = await manager.signInWithUsername("abby", "pw")

    expect(account.email).toBe(SUPABASE_SESSION_BODY.user.email)
    expect(stored?.kind).toBe("supabase")
  })

  it("gives the same generic error for an unknown username as a wrong password", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false,
      status: 401,
      json: async () => ({ code: 401, message: "Invalid username or password" }),
    })))
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: () => {}, onChange: () => {} })

    await expect(manager.signInWithUsername("nobody", "pw")).rejects.toThrow("SIGN_IN_BAD_CREDENTIALS")
  })

  it("surfaces a rate limit", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 429, json: async () => ({ code: 429 }) })))
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: () => {}, onChange: () => {} })

    await expect(manager.signInWithUsername("abby", "pw")).rejects.toThrow("SIGN_IN_TOO_MANY_ATTEMPTS")
  })
})

describe("AccountManager.signUpWithPassword", () => {
  function backend(overrides: Record<string, { ok?: boolean; status?: number; body?: unknown }> = {}) {
    const calls: string[] = []
    const fetchMock = vi.fn(async (url: string) => {
      calls.push(url)
      if (url.includes("/auth/v1/signup")) {
        const override = overrides["signup"]
        if (override) return { ok: override.ok ?? false, status: override.status ?? 500, json: async () => override.body ?? {} }
        return { ok: true, status: 200, json: async () => SUPABASE_SESSION_BODY }
      }
      if (url.includes("/v1/auth/username-available")) {
        return { ok: true, status: 200, json: async () => ({ code: 200, data: { available: true } }) }
      }
      if (url.includes("/v1/auth/claim-username")) {
        const override = overrides["claim"]
        if (override) return { ok: override.ok ?? false, status: override.status ?? 500, json: async () => override.body ?? {} }
        return { ok: true, status: 200, json: async () => ({}) }
      }
      return { ok: true, status: 200, json: async () => ({}) }
    })
    return { calls, fetch: fetchMock }
  }

  it("registers with Supabase and claims the username with the fresh session", async () => {
    const service = backend()
    vi.stubGlobal("fetch", service.fetch)
    const onChange = vi.fn()
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: vi.fn(), onChange })

    const result = await manager.signUpWithPassword(" A@EXAMPLE.COM ", "NewAccount1!", " abby ")

    expect(result.needsEmailConfirmation).toBe(false)
    expect(result.account?.email).toBe(SUPABASE_SESSION_BODY.user.email)
    expect(stored?.kind).toBe("supabase")
    expect(onChange).toHaveBeenCalledWith(result.account)
    const claimCall = service.calls.find((u) => u.includes("claim-username"))
    expect(claimCall).toBeDefined()
  })

  it("reports needsEmailConfirmation instead of a session when Supabase requires it", async () => {
    const service = backend({ signup: { ok: true, status: 200, body: { user: { id: "u1" } } } })
    vi.stubGlobal("fetch", service.fetch)
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: vi.fn(), onChange: vi.fn() })

    const result = await manager.signUpWithPassword("a@example.com", "NewAccount1!", "abby")

    expect(result.needsEmailConfirmation).toBe(true)
    expect(result.account).toBeNull()
    expect(stored).toBeNull()
  })

  it("keeps duplicate registration as a distinct error", async () => {
    const service = backend({ signup: { ok: false, status: 422, body: { msg: "User already registered" } } })
    vi.stubGlobal("fetch", service.fetch)
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: vi.fn(), onChange: vi.fn() })

    await expect(manager.signUpWithPassword("a@example.com", "NewAccount1!", "abby")).rejects.toThrow("SIGN_UP_EMAIL_EXISTS")
    expect(stored).toBeNull()
  })

  it.each(["short1A", "lowercaseonly", "Qwerty123"])("rejects a weak registration password before submitting it: %s", async (password) => {
    const service = backend()
    vi.stubGlobal("fetch", service.fetch)
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: vi.fn(), onChange: vi.fn() })
    await expect(manager.signUpWithPassword("a@example.com", password, "abby")).rejects.toThrow("SIGN_UP_WEAK_PASSWORD")
    expect(service.calls).toHaveLength(0)
  })

  it("rejects an invalid email before submitting it", async () => {
    const service = backend()
    vi.stubGlobal("fetch", service.fetch)
    const manager = new AccountManager({ endpoint: () => undefined, openExternal: vi.fn(), onChange: vi.fn() })
    await expect(manager.signUpWithPassword("not-an-email", "NewAccount1!", "abby")).rejects.toThrow("SIGN_UP_INVALID_EMAIL")
    expect(service.calls).toHaveLength(0)
  })
})

/**
 * The envelope workspace/backend actually sends.
 *
 * It answers {code, message, data} where SUCCESS is ZERO (app/response.py:
 * ResponseCode.SUCCESS = 0). signInWithUsername checked for code === 200 and
 * so threw "bad credentials" on every SUCCESSFUL response. The case above did
 * not catch it because its mock returned 200 as well — the mock and the code
 * shared one wrong assumption, so they agreed with each other and not with the
 * server. These pin the real shape.
 */
describe("AccountManager.signInWithUsername envelope", () => {
  function managerFor(body: unknown) {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url).includes("/v1/auth/sign-in-username")) {
        return { ok: true, status: 200, json: async () => body }
      }
      return { ok: true, status: 200, json: async () => SUPABASE_SESSION_BODY.user }
    }))
    return new AccountManager({
      endpoint: () => undefined, openExternal: () => {}, onChange: () => {},
    })
  }

  const TOKENS = {
    access_token: "access-1", refresh_token: "refresh-1",
    expires_in: 3600, token_type: "bearer",
  }

  it("accepts code 0, which is what SUCCESS is", async () => {
    const account = await managerFor({ code: 0, message: "ok", data: TOKENS })
      .signInWithUsername("abby", "pw")
    expect(account.email).toBe("a@example.com")
  })

  it("rejects code 200, which no endpoint returns — the old bug cannot return", async () => {
    await expect(managerFor({ code: 200, data: TOKENS }).signInWithUsername("abby", "pw"))
      .rejects.toThrow()
  })

  it("still rejects a genuine failure envelope", async () => {
    await expect(
      managerFor({ code: 401, message: "Invalid username or password", data: null })
        .signInWithUsername("abby", "wrong"),
    ).rejects.toThrow()
  })

  it("reports rate limiting as itself, not as a bad password", async () => {
    await expect(
      managerFor({ code: 429, message: "Too many attempts.", data: null })
        .signInWithUsername("abby", "pw"),
    ).rejects.toThrow(/attempt/i)
  })
})
