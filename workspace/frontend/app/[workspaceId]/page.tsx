'use client';

import { use, Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { WorkspaceProvider, useWorkspace } from '@/lib/workspace-context';
import { LayoutProvider } from '@/components/layout/layout-context';
import { Wrapper } from '@/components/layout/wrapper';
import { usePaiAuth } from '@/lib/pai-auth-context';
import { goToSignIn } from '@/lib/auth-redirects';
import { API_URL } from '@/lib/config';
import { LogIn } from 'lucide-react';
import { useT } from '@/lib/i18n';

function WorkspaceLoadingSplash() {
  const t = useT();
  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-5">
        <img
          src="/pai-emblem.png"
          alt="Placement AI"
          className="size-16 animate-[pulse_2s_ease-in-out_infinite] dark:hidden"
        />
        <img
          src="/pai-emblem.png"
          alt="Placement AI"
          className="size-16 animate-[pulse_2s_ease-in-out_infinite] hidden dark:block"
        />
        <div className="text-center">
          <h1 className="text-xl font-semibold tracking-tight">Placement AI</h1>
          <p className="text-sm text-muted-foreground mt-0.5">{t('workspaceGate.workspace')}</p>
        </div>
      </div>
      <div className="absolute bottom-0 left-0 right-0 h-1 bg-muted overflow-hidden">
        <div className="h-full w-1/3 bg-primary rounded-full animate-[loading-bar_1.5s_ease-in-out_infinite]" />
      </div>
      <style>{`
        @keyframes loading-bar {
          0% { transform: translateX(-100%); }
          50% { transform: translateX(150%); }
          100% { transform: translateX(400%); }
        }
      `}</style>
    </div>
  );
}

/**
 * Remember which workspace this browser last opened.
 *
 * `token` is the SELF-HOSTED case only — an operator who pasted their own
 * ?token=. On Placement AI it is omitted, because the backend no longer hands
 * the browser a workspace machine token: a 30-day JS-readable cookie holding
 * a never-expiring, full-write, workspace-wide credential was the single
 * worst place that token could live.
 *
 * HOST-ONLY. This carried `domain=.openagents.org`, which a browser rejects
 * outright anywhere off that domain — so on placement-ai.com, on localhost and
 * on every self-hosted deployment the cookie was never actually written, and
 * the self-hosted `?token=` it exists to remember never persisted. Scoping it
 * to whatever host serves the app is both correct for Placement AI's own
 * origin and the only way the self-hosted case works at all.
 *
 * `oa_has_workspace` is gone with it: nothing in this product ever read it. It
 * existed for the OpenAgents marketing site to detect a returning user across
 * subdomains, which is not a relationship Placement AI has.
 */
function setWorkspaceCookie(slug: string, token?: string) {
  const maxAge = 30 * 24 * 60 * 60;
  const attrs = `path=/;max-age=${maxAge};samesite=lax${location.protocol === 'https:' ? ';secure' : ''}`;
  const payload = token ? { slug, token } : { slug };
  document.cookie = `oa_workspace=${encodeURIComponent(JSON.stringify(payload))};${attrs}`;
}

function IdentityGate({ children }: { children: React.ReactNode }) {
  const { currentUser, setUserName } = useWorkspace();
  const t = useT();

  useEffect(() => {
    if (!currentUser.name.trim()) {
      setUserName(t('workspaceGate.guest'));
    }
  }, [currentUser.name, setUserName, t]);

  return <>{children}</>;
}

/**
 * Open a workspace by id/slug with no token in the URL. Placement AI v2.0: a
 * signed-in student has exactly one canonical workspace — resolve it (same
 * `GET /v1/account/workspace` call that provisions it on first login) and
 * use its token, plus the bearer. If the URL's id/slug doesn't match the
 * caller's own canonical workspace, that's access denied: there is no
 * picker and no way to reach another workspace by guessing its slug.
 */
type BearerState =
  | { kind: 'loading' }
  | { kind: 'ok'; streamTicket: string }
  | { kind: 'not_found' }
  | { kind: 'no_access' }
  | { kind: 'error' };

function BearerWorkspace({ workspaceId, idToken }: { workspaceId: string; idToken: string }) {
  const t = useT();
  const [state, setState] = useState<BearerState>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: 'loading' });
    (async () => {
      try {
        const { getAccountWorkspace } = await import('@/lib/account-api');
        const ws = await getAccountWorkspace(idToken);
        if (cancelled) return;
        if (ws.slug === workspaceId || ws.workspaceId === workspaceId) {
          setWorkspaceCookie(ws.slug || workspaceId);
          setState({ kind: 'ok', streamTicket: ws.streamTicket || '' });
          return;
        }
        // The URL doesn't point at this student's own canonical workspace.
        // Previously we rendered the full workspace shell with an empty
        // token — every API call then failed silently and a nonexistent
        // slug looked like a working (empty) workspace. Probe existence so
        // a typo'd link and "this isn't your workspace" get distinct,
        // explicit error screens instead.
        const res = await fetch(`${API_URL}/v1/workspaces/${encodeURIComponent(workspaceId)}`, { cache: 'no-store' });
        if (cancelled) return;
        setState(res.status === 404 ? { kind: 'not_found' } : { kind: 'no_access' });
      } catch {
        if (!cancelled) setState({ kind: 'error' });
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, idToken, attempt]);

  if (state.kind === 'loading') return <WorkspaceLoadingSplash />;

  if (state.kind === 'ok') {
    return (
      <WorkspaceProvider
        workspaceId={workspaceId}
        token=""
        bearerToken={idToken}
        streamTicket={state.streamTicket}
      >
        <IdentityGate>
          <LayoutProvider>
            <Wrapper />
          </LayoutProvider>
        </IdentityGate>
      </WorkspaceProvider>
    );
  }

  const title =
    state.kind === 'not_found' ? t('workspaceGate.notFoundTitle')
    : state.kind === 'no_access' ? t('workspaceGate.noAccessTitle')
    : t('workspaceGate.loadFailedTitle');
  const body =
    state.kind === 'not_found' ? t('workspaceGate.notFoundBody')
    : state.kind === 'no_access' ? t('workspaceGate.noAccessBody')
    : t('workspaceGate.loadFailedBody');

  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-4 p-8 bg-background">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-muted-foreground text-sm text-center max-w-md">{body}</p>
      <div className="flex items-center gap-2">
        {state.kind === 'error' && (
          <button
            onClick={() => setAttempt((a) => a + 1)}
            className="px-4 py-2 rounded-lg border border-input text-sm font-medium hover:bg-accent transition-colors"
          >
            {t('common.retry')}
          </button>
        )}
        <a
          href="/"
          className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          {t('workspaceGate.goHome')}
        </a>
      </div>
    </div>
  );
}

function WorkspaceContent({ workspaceId }: { workspaceId: string }) {
  const t = useT();
  const searchParams = useSearchParams();
  const token = searchParams.get('token');
  const { user, idToken, loading: authLoading, isPaiDeployment, signIn } = usePaiAuth();

  useEffect(() => {
    if (token) {
      setWorkspaceCookie(workspaceId, token);
    }
  }, [workspaceId, token]);

  // Has workspace token in URL — use it directly
  if (token) {
    return (
      <WorkspaceProvider workspaceId={workspaceId} token={token} bearerToken={idToken || undefined}>
        <IdentityGate>
          <LayoutProvider>
            <Wrapper />
          </LayoutProvider>
        </IdentityGate>
      </WorkspaceProvider>
    );
  }

  // No token — check if user is logged in via OpenAgents
  if (isPaiDeployment) {
    if (authLoading) {
      return <WorkspaceLoadingSplash />;
    }

    if (user && idToken) {
      // Logged in, no ?token in the URL — resolve the workspace token from the
      // account service using the user's identity, so the URL stays clean
      // (/{slug}) while realtime (SSE, which needs a token) still works.
      return <BearerWorkspace workspaceId={workspaceId} idToken={idToken} />;
    }

    // Not logged in — show login prompt
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-6 p-8 bg-background">
        <div className="flex flex-col items-center gap-2">
          <h1 className="text-xl font-semibold">{t('workspaceGate.signInTitle')}</h1>
          <p className="text-muted-foreground text-sm text-center max-w-md">
            {t('workspaceGate.signInBody')}
          </p>
        </div>
        {/* Goes to this app's own /sign-in, which offers every supported
            method — not just Google — so the label and icon stay
            method-neutral. */}
        <button
          onClick={() => goToSignIn()}
          className="flex items-center gap-3 px-6 py-3 rounded-lg bg-primary text-primary-foreground font-medium hover:bg-primary/90 transition-colors"
        >
          <LogIn className="size-5" />
          {t('workspaceGate.logIn')}
        </button>
      </div>
    );
  }

  // Not on OpenAgents domain and no token — show token instructions
  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-4 p-8 bg-background">
      <h1 className="text-xl font-semibold text-destructive">{t('workspaceGate.missingToken')}</h1>
      <p className="text-muted-foreground text-sm">
        {t('workspaceGate.missingTokenBefore')}{' '}
        <code className="bg-muted px-2 py-0.5 rounded">?token=your_workspace_token</code>{' '}
        {t('workspaceGate.missingTokenAfter')}
      </p>
    </div>
  );
}

export default function WorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = use(params);

  return (
    <Suspense fallback={<WorkspaceLoadingSplash />}>
      <WorkspaceContent workspaceId={workspaceId} />
    </Suspense>
  );
}
