import type { Workspace } from "@renderer/types"

/**
 * The renderer's copy of the api -> web origin mapping. Main has the same rule
 * in main/auth/endpoints.ts and cannot be imported from here (different
 * tsconfig root), so it is spelled out rather than reached for across the
 * boundary — keep the two in step.
 */
const DEFAULT_WORKSPACE_WEB_BASE_URL = "https://app.placement-ai.com"
const HOSTED_API_HOST = "api.placement-ai.com"
const HOSTED_WEB_HOST = "app.placement-ai.com"

/** Full workspace URL, including the access token when the workspace has one. */
export function workspaceUrl(ws: Workspace): string {
  const url = workspacePageUrl(ws)
  return ws.token ? `${url}?token=${encodeURIComponent(ws.token)}` : url
}

/**
 * Workspace URL without the access token — for opening in a browser, where a
 * token in the address bar would leak into history and screen shares. The
 * browser session is expected to already have (or prompt for) access.
 */
export function workspacePageUrl(ws: Workspace): string {
  return `${workspaceWebBaseUrl(ws.endpoint)}/${ws.slug || ws.id}`
}

export function workspaceWebBaseUrl(endpoint?: string): string {
  const baseUrl = (endpoint || DEFAULT_WORKSPACE_WEB_BASE_URL).replace(/\/$/, "")
  // Only the hosted deployment has two origins to map between. A self-hosted
  // endpoint serves its API and its pages from one origin, so it is left as
  // it is; the previous "workspace-endpoint" -> "workspace" substring swap was
  // the OpenAgents naming convention and rewrote nothing for Placement AI.
  return baseUrl.replace(HOSTED_API_HOST, HOSTED_WEB_HOST).replace(/\/v1$/, "")
}

/**
 * Whether a workspace this device joined opens in the app's own Workspace.
 *
 * The embedded Workspace needs a signed-in account and talks to the configured
 * deployment only, so a workspace on another deployment, or any workspace while
 * signed out, opens in the browser instead.
 */
export function opensInApp(ws: Workspace, configuredEndpoint: string | undefined, signedIn: boolean): boolean {
  return signedIn && workspaceWebBaseUrl(ws.endpoint) === workspaceWebBaseUrl(configuredEndpoint)
}

export function workspaceDisplayHost(endpoint?: string): string {
  const baseUrl = workspaceWebBaseUrl(endpoint)
  try {
    return new URL(baseUrl).host
  } catch {
    return baseUrl.replace(/^https?:\/\//, "").replace(/\/$/, "")
  }
}
