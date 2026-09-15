'use client';

import { useEffect, useState } from 'react';

import { forwardToDesktop, parseDesktopHandoff } from '@/lib/desktop-handoff';

/**
 * The desktop launcher's OAuth sign-in landing.
 *
 * The launcher opens the system browser directly at Supabase's authorize URL
 * (redirect_to = this page, with the loopback port+state it invented). When
 * Supabase redirects back here with `?code=...`, this page's only job is to
 * hand that code to the loopback listener — the launcher's main process holds
 * the matching PKCE code verifier (it generated the authorize URL) and
 * exchanges the code for a session itself. This page never sees the session.
 */

type Phase = 'working' | 'done' | 'failed';

function DesktopAuth() {
  const [phase, setPhase] = useState<Phase>('working');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const handoff = parseDesktopHandoff(window.location.search);
    const code = params.get('code');
    const oauthError = params.get('error_description') || params.get('error');

    if (!handoff) {
      setPhase('failed');
      setError('This link is missing the information the desktop app needs.');
      return;
    }

    void (async () => {
      try {
        if (oauthError) throw new Error(oauthError);
        if (!code) throw new Error('Missing sign-in code.');
        await forwardToDesktop(handoff, { code });
        setPhase('done');
      } catch (e) {
        setPhase('failed');
        setError(e instanceof Error ? e.message : 'Could not reach the desktop app.');
      }
    })();
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-5">
        <img
          src="/logo-icon.png"
          alt="OpenAgents"
          className={`size-16 dark:hidden ${phase === 'working' ? 'animate-[pulse_2s_ease-in-out_infinite]' : ''}`}
        />
        <img
          src="/logo-white.png"
          alt="OpenAgents"
          className={`size-16 hidden dark:block ${phase === 'working' ? 'animate-[pulse_2s_ease-in-out_infinite]' : ''}`}
        />
        <div className="text-center max-w-md px-8">
          <h1
            className={`text-xl font-semibold tracking-tight ${phase === 'failed' ? 'text-destructive' : ''}`}
          >
            {phase === 'done'
              ? 'You are signed in'
              : phase === 'failed'
                ? 'Sign-in failed'
                : 'Signing you in…'}
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {phase === 'done'
              ? 'Return to OpenAgents Launcher to continue.'
              : (error ?? 'OpenAgents Workspace')}
          </p>
        </div>
      </div>
    </div>
  );
}

export default function DesktopAuthPage() {
  return <DesktopAuth />;
}
