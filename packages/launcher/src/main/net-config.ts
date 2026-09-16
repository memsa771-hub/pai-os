/**
 * The proxy the embedded Workspace (and the app's own self-update) go out
 * through.
 */
import { session } from "electron"
import { slog } from "./bootstrap/startup-log"
import type { Store } from "./store"
import { WORKSPACE_PARTITION } from "./workspace-bundle"

/** The updater downloads on its own session so its proxy can be set apart. */
export const UPDATER_NET_PARTITION = "electron-updater"

/**
 * Apply proxy settings (Settings → Network) everywhere Chromium's network
 * stack draws from: the default session (renderer fetch, the net module), the
 * updater's own session (self-update downloads — Chromium's net stack ignores
 * HTTP_PROXY entirely, so without this the proxy configured here did nothing
 * for updates), and the embedded Workspace's own partition, which starts out
 * with no proxy at all and would otherwise fail every request with a bare
 * "Failed to fetch" on a machine where nothing else does.
 */
export function applyProxyFromSettings(store: Store): void {
  const http = ((store.get("httpProxy") as string) || "").trim()
  const https = ((store.get("httpsProxy") as string) || "").trim()
  const no = ((store.get("noProxy") as string) || "").trim()

  const rules = [http && `http=${http}`, https && `https=${https}`]
    .filter(Boolean)
    .join(";")
  // No explicit proxy means "behave like the browser": follow whatever the OS
  // is configured to use. `direct` here used to force every Chromium request —
  // including the update feed — to bypass a system proxy the user had
  // deliberately set up, which on a restricted network is the difference
  // between slow and not working at all.
  const config: Electron.ProxyConfig = rules
    ? { proxyRules: rules, proxyBypassRules: no || undefined }
    : { mode: "system" }

  if (session?.defaultSession) {
    void session.defaultSession.setProxy(config)
  }
  try {
    void session
      .fromPartition(UPDATER_NET_PARTITION, { cache: false })
      .setProxy(config)
  } catch (err) {
    slog(`failed to apply proxy to updater session: ${(err as Error).message}`)
  }
  try {
    void session.fromPartition(WORKSPACE_PARTITION).setProxy(config)
  } catch (err) {
    slog(`failed to apply proxy to workspace session: ${(err as Error).message}`)
  }
}
