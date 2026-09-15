/**
 * Where the account half of the app talks to.
 *
 * Two hosts, both derived from the single endpoint the user can configure
 * (Settings → workspace endpoint), so a self-hosted deployment moves both at
 * once. Identity itself (Supabase Auth) is a separate, fixed project — see
 * main/auth/supabase.ts — not something a self-hosted deployment overrides
 * here.
 *
 *   api   workspace-endpoint.openagents.org   REST (sessions, memberships)
 *   web   workspace.openagents.org            the workspace pages
 *
 * The renderer has the same web/api mapping in `lib/workspace-urls.ts`; main
 * cannot import it (different tsconfig root) so the rule is spelled out again
 * here rather than reached for across the boundary.
 */

export const DEFAULT_API_BASE = "https://workspace-endpoint.openagents.org"

/** REST base for the account API — what `workspaceEndpoint` normalizes to. */
export function apiBase(configured?: string): string {
  return (configured || DEFAULT_API_BASE).replace(/\/$/, "")
}

/**
 * The web origin that serves the workspace pages. `workspace-endpoint.x` and
 * `workspace.x` are the same deployment; anything else is assumed to serve
 * both from one origin.
 *
 * The override exists for the case the derivation cannot cover: a front end
 * served from somewhere unrelated to its API — a preview deployment, or a
 * local dev server being pointed at the hosted backend.
 */
export function webBase(configured?: string): string {
  const override = process.env.OPENAGENTS_WORKSPACE_WEB_BASE
  if (override) return override.replace(/\/$/, "")
  return apiBase(configured)
    .replace("workspace-endpoint.", "workspace.")
    .replace(/\/v1$/, "")
}
