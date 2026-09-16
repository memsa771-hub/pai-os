import { create } from "zustand"

import { showGlobalToast } from "../hooks/useToast"
import { accountError } from "../lib/account-errors"
import i18n from "../i18n"
import type { AccountInfo } from "../types"

/**
 * PAI account and its one Workspace. There is no picker and no second area:
 * signed in, the whole window is the account's Workspace; signed out, it is
 * Welcome/sign-in. Settings lives on top of it as a small overlay (see
 * store/ui.ts), not a second place to be.
 */
interface AccountState {
  account: AccountInfo | null
  /** What the Workspace shows while signed out. `check-email` follows a
   * sign-up when Supabase requires confirming the address before a session
   * exists. */
  authMode: "welcome" | "sign-in" | "sign-up" | "check-email"
  /** The address a "check-email" state is waiting on. */
  pendingEmail: string | null
  /** False until the first read from main lands — not "no account". */
  ready: boolean
  signingIn: boolean
  /** Last failure, for the surface that asked. Cleared by the next attempt. */
  error: string | null
  showWelcome: () => void
  init: () => Promise<void>
  /** Open the workspace's own sign-in gate. */
  openSignIn: () => void
  openSignUp: () => void
  /** Abandon a sign-in that moved to the browser, freeing its loopback port. */
  cancelSignIn: () => void
  /** Sign in with an email and password, in the app. */
  signInWithPassword: (email: string, password: string) => Promise<void>
  /** Sign in with a username and password — resolved to an email
   * server-side only. */
  signInWithUsername: (username: string, password: string) => Promise<void>
  signUpWithPassword: (email: string, password: string, username: string) => Promise<void>
  /**
   * Sign in through the browser, for the providers that will not authenticate
   * inside an app window. Resolves false on failure, with the reason in
   * `error` — the caller decides where to show it.
   */
  signIn: (provider: "google" | "github") => Promise<boolean>
  signOut: () => Promise<void>
  clearError: () => void
}

export const useAccountStore = create<AccountState>((set, get) => ({
  account: null,
  authMode: "welcome",
  pendingEmail: null,
  ready: false,
  signingIn: false,
  error: null,

  showWelcome: () => set({ authMode: "welcome" }),

  openSignIn: () => set({ authMode: "sign-in" }),

  openSignUp: () => set({ authMode: "sign-up" }),

  init: async () => {
    // The embedded page's own session broke (an expired refresh token it
    // could not renew) — it asked main to sign out and show the native gate.
    window.api.onWorkspaceSignIn?.(() => get().openSignIn())
    window.api.onAccountChanged((account) => {
      // Signing out shows the sign-in gate right where the Workspace was.
      set(account
        ? { account, signingIn: false }
        : { account: null, signingIn: false, authMode: "sign-in" },
      )
    })
    // Google and GitHub sign-ins leave the app; the window itself does not
    // change, so this is the only thing that says where they went.
    window.api.onSignInExternal(() => {
      set({ signingIn: true })
      showGlobalToast(i18n.t("account.externalOpened"), "info")
    })
    window.api.onSignInFailed(({ message }) => {
      set({ signingIn: false })
      showGlobalToast(accountError(message, i18n.t.bind(i18n)), "error")
    })
    try {
      set({ account: await window.api.getAccount() })
    } catch (err) {
      console.error("getAccount failed:", err)
    } finally {
      set({ ready: true })
    }
  },

  cancelSignIn: () => {
    void window.api.cancelSignIn()
    set({ signingIn: false })
  },

  signIn: async (provider) => {
    if (get().signingIn) return false
    set({ signingIn: true, error: null })
    try {
      set({ account: await window.api.signIn(provider) })
      return true
    } catch (err) {
      set({ error: (err as Error).message })
      return false
    } finally {
      set({ signingIn: false })
    }
  },

  signInWithPassword: async (email, password) => {
    const account = await window.api.signInWithPassword(email, password)
    set({ account, error: null })
  },

  signInWithUsername: async (username, password) => {
    const account = await window.api.signInWithUsername(username, password)
    set({ account, error: null })
  },

  signUpWithPassword: async (email, password, username) => {
    const { account, needsEmailConfirmation } = await window.api.signUpWithPassword(email, password, username)
    if (needsEmailConfirmation) {
      set({ authMode: "check-email", pendingEmail: email, error: null })
      return
    }
    set({ account, authMode: "sign-in", error: null })
  },

  signOut: async () => {
    try {
      await window.api.signOut()
    } catch (err) {
      console.error("signOut failed:", err)
    }
    // Same reasoning as the listener above: stay here, where an account-less
    // Workspace IS the sign-in.
    set({ account: null, authMode: "sign-in", error: null })
  },

  clearError: () => set({ error: null }),
}))
