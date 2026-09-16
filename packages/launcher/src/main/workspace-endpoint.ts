import { WORKSPACE_WEB_HOSTNAME, WORKSPACE_API_HOSTNAME } from "./auth/endpoints"

/**
 * Normalize a user-entered workspace endpoint (Settings → Network) to the API
 * origin, applied on the way in so it matches what the connection test probes
 * and what the embedded view is given.
 */
export function normalizeWorkspaceEndpoint(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined
  const raw = value.trim()
  if (!raw) return undefined
  try {
    const url = new URL(raw)
    if (url.protocol !== "http:" && url.protocol !== "https:") return undefined
    if (url.hostname === WORKSPACE_WEB_HOSTNAME) {
      return url.origin.replace(WORKSPACE_WEB_HOSTNAME, WORKSPACE_API_HOSTNAME)
    }
    return url.origin
  } catch {
    return undefined
  }
}
