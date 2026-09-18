'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { usePaiAuth } from '@/lib/pai-auth-context';
import { signInWithPassword, startOAuthRedirect } from '@/lib/supabase-auth';
import { signInWithUsername } from '@/lib/auth-api';
import { desktopHost } from '@/lib/desktop-host';

export default function SignInPage() {
  const router = useRouter();
  const { applySession } = usePaiAuth();
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const session = identifier.includes('@')
        ? await signInWithPassword(identifier.trim(), password)
        : await signInWithUsername(identifier.trim(), password);
      applySession(session);
      router.push('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  const oauth = async (provider: 'google' | 'github') => {
    setError(null);
    // Inside the desktop app's embedded view, OAuth can't complete on this
    // origin (a custom Electron scheme Supabase can't redirect back to) —
    // delegate to the launcher's own native sign-in, which runs the browser
    // round trip outside this view.
    const host = desktopHost();
    if (host) { host.signIn(); return; }
    try {
      await startOAuthRedirect(provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start sign-in');
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-8">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <Image src="/pai-emblem.png" alt="Placement AI" width={40} height={40} />
          <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
        </div>

        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="identifier">Email or Username</Label>
            <Input
              id="identifier"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              autoFocus
              required
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button type="submit" disabled={busy || !identifier.trim() || !password}>
            {busy ? 'Signing in…' : 'Sign In'}
          </Button>
        </form>

        <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
          <div className="h-px flex-1 bg-border" />
          OR
          <div className="h-px flex-1 bg-border" />
        </div>

        <div className="grid gap-2">
          <Button type="button" variant="outline" onClick={() => oauth('google')} disabled={busy}>
            Continue with Google
          </Button>
          <Button type="button" variant="outline" onClick={() => oauth('github')} disabled={busy}>
            Continue with GitHub
          </Button>
        </div>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Don&apos;t have an account?{' '}
          <Link href="/sign-up" className="font-medium text-foreground underline-offset-4 hover:underline">
            Create account
          </Link>
        </p>
      </div>
    </div>
  );
}
