import { describe, expect, it } from "vitest"
import { apiBase, DEFAULT_API_BASE } from "./endpoints"

/**
 * What the embedded workspace view is told its backend is.
 *
 * The view gets this over `workspace-view:config` and exposes it as
 * __OA_API_URL__; without it the page falls back to the origin baked into its
 * own bundle. Main used to send `deps.endpoint()` — the RAW setting, which is
 * undefined whenever the user has not overridden it, i.e. normally. That was
 * harmless only while the bundle's default and main's default were the same
 * string. They stopped being the same when development began resolving to
 * localhost, and the view then talked to a host that does not resolve and
 * showed "Can't reach the Placement AI server" over a healthy local backend.
 *
 * So the invariant is: whatever main resolves for itself is what the view is
 * told. apiBase() is that resolution, and it must never return undefined.
 */
describe("the API base handed to the embedded view", () => {
  it("resolves to something concrete when nothing is configured", () => {
    // The normal case: no workspaceEndpoint setting at all.
    expect(apiBase(undefined)).toBe(DEFAULT_API_BASE)
    expect(apiBase(undefined)).toMatch(/^https?:\/\//)
  })

  it("is never empty, which is what left the view guessing", () => {
    for (const configured of [undefined, "", "   "]) {
      expect(apiBase(configured as string | undefined)).toBeTruthy()
    }
  })

  it("honours a self-hosted endpoint, trailing slash and all", () => {
    expect(apiBase("https://pai.school.example/")).toBe("https://pai.school.example")
    expect(apiBase("http://localhost:8000")).toBe("http://localhost:8000")
  })

  it("points at the local stack in development, where the bundle would not", () => {
    // electron.vite.config.ts bakes localhost into PAI_API_BASE for `dev`, so
    // this is what a dev run must hand the view — NOT the bundle's production
    // fallback.
    if (process.env.PAI_API_BASE) {
      expect(apiBase(undefined)).toBe(process.env.PAI_API_BASE.replace(/\/$/, ""))
    }
  })
})
