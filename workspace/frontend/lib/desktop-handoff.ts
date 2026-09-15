// Login handoff for the desktop launcher.
//
// Google and GitHub refuse to authenticate inside an embedded view, so the
// launcher's own OAuth sign-in opens the system browser at Supabase's
// authorize URL directly (redirect_to = this page, with a port+state it
// invented), and that browser has no way back into the app except a port it
// can post to.
//
// The PKCE code verifier lives in the launcher's main process (it generated
// the authorize URL), not in this browser tab — so this page's only job is to
// read the `code` Supabase redirected back with and hand it to the loopback
// listener unchanged. The launcher exchanges it for a session itself.

export interface DesktopHandoff {
  port: number;
  state: string;
}

/** Where the launcher sends people to start a browser sign-in. */
export const DESKTOP_AUTH_PATH = '/auth/desktop';

/** Anything above the privileged range; the launcher binds an ephemeral port. */
const MIN_PORT = 1024;
const MAX_PORT = 65535;

/** Read the loopback target out of /auth/desktop's own query. */
export function parseDesktopHandoff(search: string): DesktopHandoff | null {
  const params = new URLSearchParams(search);
  const port = Number(params.get('port'));
  const state = params.get('state');
  if (!Number.isInteger(port) || port < MIN_PORT || port > MAX_PORT) return null;
  if (!state) return null;
  return { port, state };
}

interface ForwardPayload {
  state: string;
  /** The raw Supabase PKCE authorization code — the launcher exchanges it
   * for a session itself (it holds the matching code verifier). */
  code?: string;
  error?: string;
}

/**
 * Hand the OAuth result to the launcher's loopback listener.
 *
 * A cross-origin POST it can read (the listener answers with our origin in
 * Access-Control-Allow-Origin, and grants the private-network access Chrome
 * requires of a public page reaching 127.0.0.1). Where the browser refuses that
 * outright, fall back to navigating to the same endpoint with the payload in
 * the query — a sign-in that completes beats one that is tidy.
 */
export async function forwardToDesktop(
  handoff: DesktopHandoff,
  payload: Omit<ForwardPayload, 'state'>,
): Promise<void> {
  const url = `http://127.0.0.1:${handoff.port}/desktop-auth`;
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: handoff.state, ...payload }),
    });
    if (!res.ok) throw new Error(`Launcher refused the sign-in (${res.status})`);
  } catch {
    const query = new URLSearchParams({ state: handoff.state });
    if (payload.code) query.set('code', payload.code);
    if (payload.error) query.set('error', payload.error);
    window.location.replace(`${url}?${query.toString()}`);
  }
}
