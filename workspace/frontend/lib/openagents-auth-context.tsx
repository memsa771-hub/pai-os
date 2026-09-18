'use client';

import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { capture, identify } from './analytics';
import { desktopHost } from './desktop-host';
import { clearAuthSession, loadAuthSession, saveAuthSession } from './auth-session';
import { refreshSession, signOut as supabaseSignOut, type AuthSession } from './supabase-auth';

interface OpenAgentsUser {
  email: string;
  displayName: string;
  photoURL: string | null;
}

interface OpenAgentsAuthContextValue {
  user: OpenAgentsUser | null;
  idToken: string | null;
  loading: boolean;
  isOpenAgentsDomain: boolean;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  /** Adopt a freshly obtained Supabase session (from /sign-in, /sign-up, or
   * the OAuth/email-confirmation callback pages) without a page reload. */
  applySession: (session: AuthSession) => void;
}

// `workspace` is the desktop build: PAI Desktop serves the bundle from
// pai://workspace/, so that is this app's own host there — the same way
// placement-ai.com is on the web. Without it the desktop app would decide it
// was a third-party deployment and show the marketing landing page.
const PAI_HOSTNAMES = ['placement-ai.com', 'workspace.openagents.org', 'localhost', 'workspace'];

/**
 * Whether this page is Placement AI's own deployment rather than a third-party
 * self-hosted one. Getting this wrong is not cosmetic: `false` means the app
 * never offers a login button at all, only the "add a token to the URL"
 * screen, so a student on that host simply cannot get in.
 *
 * `www.` is stripped rather than listed, so the www host of every domain here
 * works and a future domain does not have to remember to add both. Loopback IPs
 * are matched too — dev machines and phones on a LAN reach the dev server by
 * address, not by the name "localhost".
 */
export function isPaiHostname(hostname: string): boolean {
  const host = (hostname || '').toLowerCase().replace(/^www\./, '');
  return PAI_HOSTNAMES.includes(host) || host === '127.0.0.1' || host === '[::1]';
}

// Refresh well before expiry so a page load never races a lapsed token.
const REFRESH_MARGIN_SECONDS = 60;

const OpenAgentsAuthContext = createContext<OpenAgentsAuthContextValue | null>(null);

export function useOpenAgentsAuth() {
  const ctx = useContext(OpenAgentsAuthContext);
  if (!ctx) throw new Error('useOpenAgentsAuth must be used within OpenAgentsAuthProvider');
  return ctx;
}

export function OpenAgentsAuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<OpenAgentsUser | null>(null);
  const [idToken, setIdToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [isOpenAgentsDomain, setIsOpenAgentsDomain] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const clearSession = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    clearAuthSession();
    setUser(null);
    setIdToken(null);
  }, []);

  const applySession = useCallback((session: AuthSession) => {
    saveAuthSession(session);
    const displayName = session.user.username || session.user.email;
    setUser({ email: session.user.email, displayName, photoURL: null });
    setIdToken(session.accessToken);
    // Email is the cross-surface person key: identify on every auth restore
    // so sign-ins on different clients are attributed to the same person.
    identify(session.user.email, { email: session.user.email, display_name: displayName });

    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    const delayMs = Math.max(0, (session.expiresAt - REFRESH_MARGIN_SECONDS) * 1000 - Date.now());
    refreshTimer.current = setTimeout(async () => {
      try {
        applySession(await refreshSession(session.refreshToken));
      } catch {
        clearSession();
      }
    }, delayMs);
  }, [clearSession]);

  useEffect(() => {
    const hostname = typeof window !== 'undefined' ? window.location.hostname : '';
    const isDomain = isPaiHostname(hostname);
    setIsOpenAgentsDomain(isDomain);

    if (!isDomain) {
      setLoading(false);
      return;
    }

    // In the desktop app the launcher owns the session (native sign-in UI,
    // main-process token refresh) and pushes it here — this page never signs
    // itself in or manages a Supabase session of its own.
    if (desktopHost()) {
      setLoading(false);
      return;
    }

    const stored = loadAuthSession();
    if (stored) {
      applySession(stored);
    }
    setLoading(false);

    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => desktopHost()?.onSession?.((session) => {
    setUser({ email: session.email, displayName: session.displayName || session.email, photoURL: null });
    setIdToken(session.token);
  }), []);

  /** Web sign-in happens on /sign-in (see app/sign-in/page.tsx) instead of
   * through this callback; this only covers the desktop path, where the
   * embedded view asks the launcher chrome to bring up its own native
   * sign-in UI (which offers the actual provider choice) rather than
   * managing a Supabase session itself. */
  const signIn = useCallback(async () => {
    desktopHost()?.signIn();
  }, []);

  const signOut = useCallback(async () => {
    const token = idToken;
    clearSession();
    const host = desktopHost();
    if (host) { host.signOut(); return; }
    if (token) await supabaseSignOut(token);
  }, [idToken, clearSession]);

  return (
    <OpenAgentsAuthContext.Provider
      value={{ user, idToken, loading, isOpenAgentsDomain, signIn, signOut, applySession }}
    >
      {children}
    </OpenAgentsAuthContext.Provider>
  );
}
