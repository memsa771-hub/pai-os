import { SIGN_IN_PATH } from './config';
import { desktopHost } from './desktop-host';

// Sign-in / sign-out entry points for the workspace app.
//
// Placement AI serves its own login at /sign-in on this origin — on hosted
// web that is https://app.placement-ai.com/sign-in (email or
// username + password, plus Google/GitHub OAuth — see app/sign-in/page.tsx).
// These helpers used to bounce to a central openagents.org/login instead, a
// leftover from the OpenAgents product: on localhost that path fell through to
// a `fallbackSignIn` that is a no-op outside the desktop app, so the "Log in"
// button on the workspace gate did nothing at all, and signing out left no way
// back in. Everything now routes to the one login page that exists.
//
// The desktop app is the exception: the launcher owns the session and has its
// own native sign-in UI, so the embedded view asks the host instead of
// navigating itself.

// SIGN_IN_PATH is a path, never an absolute origin: hosted web, localhost and
// every self-hosted deployment each serve their own. See lib/config.ts.

/**
 * Send the user to the sign-in page.
 *
 * After a successful sign-in, /sign-in lands on `/`, which resolves the
 * student's one workspace and redirects to it — so there is nothing to
 * preserve in a returnTo.
 *
 * @param fallbackSignIn legacy parameter, kept so existing call sites compile;
 *   the desktop host is consulted directly and the web path needs no callback.
 */
export function goToCentralLogin(fallbackSignIn?: () => void): void {
  if (typeof window === 'undefined') return;
  const host = desktopHost();
  if (host) { host.signIn(); return; }
  void fallbackSignIn;
  window.location.href = SIGN_IN_PATH;
}

/** Forget which workspace this browser last opened (see setWorkspaceCookie in
 * app/[workspaceId]/page.tsx). Signing out should not leave that behind for
 * another 30 days.
 *
 * Both spellings are expired: the host-only cookie this app writes now, and
 * the `.openagents.org` one older builds wrote, so a browser that still has
 * the old one is cleaned up rather than carrying it until it lapses.
 * `oa_has_workspace` is likewise expired but no longer written — see
 * setWorkspaceCookie. */
function clearWorkspaceCookies(): void {
  for (const name of ['oa_workspace', 'oa_has_workspace']) {
    document.cookie = `${name}=;path=/;max-age=0`;
    document.cookie = `${name}=;path=/;max-age=0;samesite=lax;domain=.openagents.org`;
  }
}

/**
 * Sign out on this origin and land on the sign-in page, so signing back in is
 * one click rather than a dead end.
 */
export async function goToCentralLogout(signOut: () => Promise<void>): Promise<void> {
  try {
    await signOut();
  } catch {
    /* already signed out */
  }
  if (typeof document !== 'undefined') clearWorkspaceCookies();
  // The launcher owns the desktop session: it clears its own and decides what
  // the embedded view shows next.
  if (typeof window === 'undefined' || desktopHost()) return;
  window.location.href = SIGN_IN_PATH;
}
