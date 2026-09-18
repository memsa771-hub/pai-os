import { ipcMain, type BrowserWindow } from "electron"

import {
  isThemeMode,
  toLauncherLanguage,
  toWorkspaceLocale,
  type ThemeMode,
} from "../../shared/appearance-bridge"

import { openExternalSafely } from "../web-security"
import { apiBase } from "./endpoints"
import { WorkspaceHost, type ViewBounds } from "../workspace-host"
import { AccountManager, type AccountWorkspace } from "./account"
import type { AccountInfo } from "./session-store"

/**
 * The account's IPC surface, kept out of index.ts.
 *
 * Every channel here is workspace-scoped: nothing under My Agents calls into
 * it, so a machine that never signs in never reaches this file.
 */

export interface AccountIpcDeps {
  /**
   * The look and feel the launcher is currently in, and a way to change it.
   * The hosted workspace shares both — see shared/appearance-bridge.
   */
  appearance: () => { theme: ThemeMode; language: string }
  setAppearance: (next: { theme?: ThemeMode; language?: string }) => void
  /** The configured workspace endpoint (store `workspaceEndpoint`), normalized. */
  endpoint: () => string | undefined
  /** The window to notify when the account changes; null before it exists. */
  getWindow: () => BrowserWindow | null
}

const NOTICE_TYPES = new Set(["info", "success", "error", "warning"])

export function registerAccountIpc(deps: AccountIpcDeps): AccountManager {
  let workspaceHost: WorkspaceHost | null = null
  let viewRequest = 0
  const account = new AccountManager({
    endpoint: deps.endpoint,
    openExternal: (url) => void openExternalSafely(url),
    onChange: (info: AccountInfo | null) => {
      if (!info) {
        // Every way an account ends — signed out here or from the page,
        // expired, refused on renewal — ends the same way: the live page goes
        // and its storage is wiped, so a stale page cannot be reused and the
        // next account inherits nothing of this one's.
        viewRequest++
        void workspaceHost?.signOut()
      } else {
        // A renewal: the loaded page keeps running on the new token.
        const session = account.embeddedSession()
        if (session) workspaceHost?.sendSession(session)
      }
      deps.getWindow()?.webContents.send("account:changed", info)
    },
  })

  const host = new WorkspaceHost({
    getWindow: deps.getWindow,
    endpoint: deps.endpoint,
    session: () => account.embeddedSession(),
  })

  workspaceHost = host

  ipcMain.handle("account:get", () => account.getAccount())
  ipcMain.handle("account:sign-in", (_e, provider: "google" | "github") => account.signIn(provider))
  ipcMain.handle(
    "account:sign-in-password",
    (_e, email: string, password: string) =>
      account.signInWithPassword(String(email || ""), String(password || "")),
  )
  ipcMain.handle(
    "account:sign-in-username",
    (_e, username: string, password: string) =>
      account.signInWithUsername(String(username || ""), String(password || "")),
  )
  ipcMain.handle("account:cancel-sign-in", () => account.cancelSignIn())
  ipcMain.handle(
    "account:sign-up-password",
    (_e, email: string, password: string, username: string) =>
      account.signUpWithPassword(String(email || ""), String(password || ""), String(username || "")),
  )
  ipcMain.handle("account:sign-out", async () => {
    // onChange tears the page down; resolve once its storage is gone too.
    account.signOut()
    await host.whenCleared()
  })
  // ── The embedded workspace view ──────────────────────────────────────────
  // The renderer owns the layout and tells main which rectangle of it the
  // workspace fills; main owns the page. See workspace-host.ts.

  ipcMain.handle(
    "workspace-view:show",
    async (
      _e,
      target: string | null,
      bounds: ViewBounds,
      token?: string | null,
    ) => {
      const request = ++viewRequest
      // Renew before the page reads the session: the preload plants whatever
      // is current, synchronously, and a token that lapses an hour into the
      // session would otherwise land the user on the web app's sign-in gate.
      if (account.getAccount()) await account.bearer().catch(() => null)
      // A view created while the last sign-out is still wiping storage would
      // lose the session it was just given.
      await host.whenCleared()
      // Switching to local tools or signing out during refresh cancels this
      // request, so a delayed result cannot put a native view over that page.
      if (request !== viewRequest) return
      host.show(target === null ? null : String(target || ""), bounds, token ?? null)
    },
  )
  ipcMain.handle("workspace-view:set-bounds", (_e, bounds: ViewBounds) =>
    host.setBounds(bounds),
  )
  ipcMain.handle("workspace-view:hide", () => {
    viewRequest++
    host.hide()
  })
  ipcMain.handle("workspace-view:reload", () => host.reload())
  ipcMain.handle("workspace-view:home", () => host.openHome())
  // The launcher's toasts are drawn under the view; repeat them inside it.
  ipcMain.handle("workspace-view:notice", (_e, notice: unknown) => {
    const { message, type } = (notice ?? {}) as { message?: unknown; type?: unknown }
    if (typeof message !== "string" || !message || typeof type !== "string" || !NOTICE_TYPES.has(type)) return
    host.sendNotice({ message: message.slice(0, 1000), type })
  })
  // The page asks for a sign-in only when its session no longer works, so the
  // account behind it is ended first and the native sign-in takes over.
  ipcMain.on("workspace-view:sign-in", (event) => {
    if (!host.isWorkspaceSender(event.sender)) return
    account.signOut()
    deps.getWindow()?.webContents.send("workspace:sign-in")
  })
  ipcMain.on("workspace-view:sign-out", (event) => {
    if (host.isWorkspaceSender(event.sender)) account.signOut()
  })
  ipcMain.on("workspace-view:config", (event) => {
    const { theme, language } = deps.appearance()
    event.returnValue = {
      session: host.currentSession(),
      // The RESOLVED base, not the raw setting. `deps.endpoint()` is undefined
      // whenever the user has not overridden it — which is the normal case —
      // and the page then fell back to the origin baked into its own bundle.
      // That was harmless only while the bundle's default and main's default
      // were the same string. They are not: a dev run resolves to
      // http://localhost:8000 here while the bundle still says
      // https://api.placement-ai.com, so the embedded view talked to a host
      // that does not resolve and showed "Can't reach the Placement AI
      // server" on top of a perfectly healthy local backend.
      //
      // Sending what main itself uses keeps the two halves of one window
      // talking to one backend, in every configuration.
      apiUrl: apiBase(deps.endpoint()),
      theme,
      locale: toWorkspaceLocale(language),
    }
  })

  // Changed inside the workspace — the launcher follows, so a choice made on
  // either side holds for the whole window.
  ipcMain.on("workspace-view:theme-changed", (_e, theme: unknown) => {
    if (isThemeMode(theme)) deps.setAppearance({ theme })
  })
  ipcMain.on("workspace-view:locale-changed", (_e, locale: unknown) => {
    deps.setAppearance({ language: toLauncherLanguage(String(locale || "")) })
  })

  // Changed in the launcher — the workspace follows.
  ipcMain.handle(
    "workspace-view:appearance",
    (_e, next: { theme: ThemeMode; language: string }) => {
      host.sendAppearance({
        theme: next.theme,
        locale: toWorkspaceLocale(next.language),
      })
    },
  )

  return account
}

export type { AccountWorkspace }
