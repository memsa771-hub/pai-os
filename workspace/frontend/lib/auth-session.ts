// localStorage persistence for the Supabase AuthSession — the browser-side
// equivalent of packages/launcher's session-store.ts.
import type { AuthSession } from './supabase-auth';

const STORAGE_KEY = 'oa_supabase_session';

export function saveAuthSession(session: AuthSession): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    /* storage unavailable (private mode quota) — session lives for this page only */
  }
}

/** The stored session, or null if absent or malformed. Does not check
 * expiry — callers refresh proactively (see openagents-auth-context.tsx). */
export function loadAuthSession(): AuthSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as AuthSession;
    if (!s?.accessToken || !s?.refreshToken || !s?.user?.email) {
      clearAuthSession();
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

export function clearAuthSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
