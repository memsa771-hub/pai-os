'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { usePaiAuth } from '@/lib/pai-auth-context';
import { signUpWithPassword, startOAuthRedirect } from '@/lib/supabase-auth';
import { claimUsername, isUsernameAvailable } from '@/lib/auth-api';
import { registrationPasswordError } from '@/lib/password-policy';
import { desktopHost } from '@/lib/desktop-host';

export default function SignUpPage() {
  const router = useRouter();
  const { applySession, idToken } = usePaiAuth();
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [checkEmail, setCheckEmail] = useState(false);

  useEffect(() => {
    if (idToken) router.replace('/');
  }, [idToken, router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const normalizedUsername = username.trim().toLowerCase();
    if (!/^[a-z0-9_-]{3,32}$/.test(normalizedUsername)) {
      setError('Username must be 3-32 characters: letters, numbers, _ or -');
      return;
    }

    if (registrationPasswordError(password)) {
      setError('Password is too weak — use 8+ characters with a mix of letters, numbers, and symbols.');
      return;
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setBusy(true);
    try {
      if (!(await isUsernameAvailable(normalizedUsername))) {
        setError('That username is already taken. Choose another username.');
        return;
      }
      const { session, needsEmailConfirmation } = await signUpWithPassword(
        email.trim(),
        password,
        normalizedUsername,
      );
      // No session yet means Supabase requires confirming the email first —
      // the username (already sent as signup metadata) gets attached to the
      // account once a real session exists (see app/auth/callback/page.tsx).
      if (needsEmailConfirmation || !session) {
        setCheckEmail(true);
        return;
      }
      applySession(session);
      try {
        await claimUsername(session.accessToken, normalizedUsername);
      } catch (err) {
        const message = err instanceof Error ? err.message : '';
        setError(/already taken/i.test(message)
          ? 'That username was just taken. Choose another username.'
          : message || 'Could not save username');
        return;
      }
      router.push('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-up failed');
    } finally {
      setBusy(false);
    }
  };

  const oauth = async (provider: 'google' | 'github') => {
    setError(null);
    // Inside the desktop app's embedded view, OAuth can't complete on this
    // origin — delegate to the launcher's own native sign-in/sign-up instead.
    const host = desktopHost();
    if (host) { host.signIn(); return; }
    try {
      await startOAuthRedirect(provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start sign-in');
    }
  };

  if (checkEmail) {
    return (
      <div className="flex min-h-screen items-center justify-center p-8">
        <div className="w-full max-w-sm text-center">
          <Image src="/pai-emblem.png" alt="Placement AI" width={40} height={40} className="mx-auto mb-4" />
          <h1 className="text-xl font-semibold tracking-tight">Check your email</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            We sent a confirmation link to {email.trim()}. Click it to finish creating your account.
          </p>
        </div>
      </div>
    );
  }

  if (idToken) {
    return (
      <div className="flex min-h-screen items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">Opening Placement AI…</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-8">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <Image src="/pai-emblem.png" alt="Placement AI" width={40} height={40} />
          <h1 className="text-xl font-semibold tracking-tight">Create account</h1>
        </div>

        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="username">Username</Label>
            <Input
              id="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              autoFocus
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="confirm-password">Confirm Password</Label>
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={busy}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button
            type="submit"
            disabled={busy || !username.trim() || !email.trim() || !password || !confirmPassword}
          >
            {busy ? 'Creating account…' : 'Create Account'}
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
          Already have an account?{' '}
          <Link href="/sign-in" className="font-medium text-foreground underline-offset-4 hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
