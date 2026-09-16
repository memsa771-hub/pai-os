/**
 * Where the account half of the app talks to.
 *
 * Two hosts, both derived from the single endpoint the user can configure
 * (Settings → workspace endpoint), so a self-hosted deployment moves both at
 * once. Identity itself (Supabase Auth) is a separate, fixed project — see
 * main/auth/supabase.ts — not something a self-hosted deployment overrides
 * here.
 *
 *   api   the backend REST API (sessions, the account's workspace)
 *   web   the workspace pages (the same product Web signs into)
 *
 * Defaults are baked in at build time from workspace/.env (see
 * electron.vite.config.ts's `define` block), matching the values Web itself
 * uses (NEXT_PUBLIC_API_URL / the app's own canonical origin) — never
 * invented here. The renderer has the same web/api mapping in
 * `lib/workspace-urls.ts`; main cannot import it (different tsconfig root)
 * so the rule is spelled out again here rather than reached for across the
 * boundary.
 */

export const DEFAULT_API_BASE = process.env.PAI_API_BASE || "https://workspace-endpoint.openagents.org"
export const DEFAULT_WEB_BASE = process.env.PAI_WEB_BASE || "https://placement-ai.com"

export const WORKSPACE_API_HOSTNAME = new URL(DEFAULT_API_BASE).hostname
export const WORKSPACE_WEB_HOSTNAME = new URL(DEFAULT_WEB_BASE).hostname

/** REST base for the account API — what `workspaceEndpoint` normalizes to. */
export function apiBase(configured?: string): string {
  return (configured || DEFAULT_API_BASE).replace(/\/$/, "")
}

/**
 * The web origin that serves the workspace pages.
 *
 * The hosted deployment's two origins are unrelated domains (its own API
 * host, and the product's public web address), so they cannot be derived
 * from one another — both are known constants instead. A self-hosted
 * deployment's API and web pages are assumed to share one origin (the
 * common case), unless `PAI_WEB_BASE_OVERRIDE` says otherwise.
 */
export function webBase(configured?: string): string {
  const override = process.env.PAI_WEB_BASE_OVERRIDE
  if (override) return override.replace(/\/$/, "")
  if (configured) return apiBase(configured)
  return DEFAULT_WEB_BASE
}
