'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useOpenAgentsAuth } from '@/lib/openagents-auth-context';
import { claimUsername } from '@/lib/auth-api';
import { consumePkceVerifier, exchangeOAuthCode } from '@/lib/supabase-auth';

function AuthCallback() {
  const router = useRouter();
  const { applySession } = useOpenAgentsAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const oauthError = params.get('error_description') || params.get('error');
    if (oauthError) { setError(oauthError); return; }
    if (!code) { setError('Missing sign-in code. Please try again.'); return; }

    void (async () => {
      try {
        const verifier = consumePkceVerifier();
        if (!verifier) throw new Error('Sign-in expired. Please try again.');
        const session = await exchangeOAuthCode(code, verifier);
        applySession(session);
        if (!session.user.username) {
          router.replace('/sign-up');
          return;
        }
        try {
          await claimUsername(session.accessToken, session.user.username);
        } catch (claimError) {
          const message = claimError instanceof Error ? claimError.message : '';
          if (/already taken/i.test(message)) {
            router.replace('/sign-up?username-race=1');
            return;
          }
          throw claimError;
        }
        router.replace('/');
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Sign-in failed. Please try again.');
      }
    })();
  }, [applySession, router]);

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-8 bg-background">
        <h1 className="text-xl font-semibold text-destructive">Sign-in failed</h1>
        <p className="max-w-md text-center text-sm text-muted-foreground">{error}</p>
        <a href="/sign-in" className="rounded-lg bg-primary px-6 py-3 font-medium text-primary-foreground">Back to sign in</a>
      </div>
    );
  }
  return <div className="fixed inset-0 flex items-center justify-center bg-background">Signing you in…</div>;
}

export default function AuthCallbackPage() {
  return <AuthCallback />;
}
