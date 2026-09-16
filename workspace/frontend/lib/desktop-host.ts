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
  signIn(): void;
  signOut(): void;
  /**
   * The desktop app owns the session and tells the page when it is renewed.
   * Optional: an older preload can briefly coexist with a newer bundle in dev.
   */
  onSession?(callback: (session: HostSession) => void): () => void;
}

export function desktopHost(): DesktopHost | null {
  if (typeof window === 'undefined') return null;
  return (window as unknown as { __paiHost__?: DesktopHost }).__paiHost__ ?? null;
}
