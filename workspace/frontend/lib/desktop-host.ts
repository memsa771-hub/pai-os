import { DEFAULT_APP_URL } from './config';

/** What the launcher's main process pushes on sign-in/renewal — just enough
 * for the embedded view to attach the bearer token; the refresh token itself
 * stays in the main process, which pushes a fresh event when it renews. */
export interface HostSession {
  token: string;
  email: string;
  displayName: string | null;
  expiresAt: number;
}

/** Optional desktop capabilities. The web application has no host bridge. */
export interface DesktopHost {
  /**
   * The session this view was opened with, present from the first script.
   * Optional: an older preload can briefly coexist with a newer bundle in dev.
   */
  session?: HostSession | null;
  /** Browser-facing Placement AI origin for public links. */
  appUrl?: string;
  signIn(): void;
  signOut(): void;
  /**
   * The desktop app owns the session and tells the page when it is renewed.
   * Optional: an older preload can briefly coexist with a newer bundle in dev.
   */
  onSession?(callback: (session: HostSession) => void): () => void;
}

/**
 * Build a URL that another person can open in a normal browser.
 *
 * Desktop pages run on the private `pai://workspace` protocol, so their
 * window.location.origin must never be copied into a public share link.
 */
export function publicAppUrl(path: string): string {
  const hostOrigin = desktopHost()?.appUrl?.replace(/\/$/, '');
  const browserOrigin = typeof window !== 'undefined' ? window.location.origin : '';
  const origin = hostOrigin || browserOrigin || DEFAULT_APP_URL;
  return `${origin}${path.startsWith('/') ? path : `/${path}`}`;
}

export function desktopHost(): DesktopHost | null {
  if (typeof window === 'undefined') return null;
  return (window as unknown as { __paiHost__?: DesktopHost }).__paiHost__ ?? null;
}
