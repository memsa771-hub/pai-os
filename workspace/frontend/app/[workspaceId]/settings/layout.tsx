'use client';

import { use, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { ArrowLeft, CircleUser, Globe, LogIn, Settings2, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  WorkspaceSettingsContext,
  type WorkspaceSettingsValue,
} from '@/components/settings/workspace-settings-context';
import { workspaceApi } from '@/lib/api';
import type { Workspace, WorkspaceMe } from '@/lib/types';
import { usePaiAuth } from '@/lib/pai-auth-context';
import { goToSignIn } from '@/lib/auth-redirects';
import { useT } from '@/lib/i18n';

/** Read a self-hosted workspace token persisted by the main workspace view
 * (see setWorkspaceCookie in app/[workspaceId]/page.tsx).
 *
 * On Placement AI this always returns null: the cookie no longer carries a
 * token, because the backend no longer gives the browser one. Only a
 * self-hosted operator who pasted their own ?token= has one here. */
function readCookieToken(workspaceId: string): string | null {
  if (typeof document === 'undefined') return null;
  const raw = document.cookie.split('; ').find((c) => c.startsWith('oa_workspace='));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(decodeURIComponent(raw.slice('oa_workspace='.length)));
    if (parsed?.slug === workspaceId && typeof parsed.token === 'string') return parsed.token;
  } catch { /* malformed cookie */ }
  return null;
}

const SECTIONS = [
  { slug: 'profile', labelKey: 'admin.navProfile', icon: CircleUser },
  { slug: 'general', labelKey: 'admin.navGeneral', icon: Settings2 },
  { slug: 'security', labelKey: 'admin.navSecurity', icon: ShieldCheck },
  { slug: 'integrations', labelKey: 'admin.navIntegrations', icon: Globe },
  { slug: 'preferences', labelKey: 'admin.navPreferences', icon: SlidersHorizontal },
] as const;

function SettingsShell({ workspaceId, children }: { workspaceId: string; children: React.ReactNode }) {
  const t = useT();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { user, idToken, loading: authLoading, isPaiDeployment, signIn } = usePaiAuth();

  const urlToken = searchParams.get('token');
  const query = urlToken ? `?token=${encodeURIComponent(urlToken)}` : '';

  // ── Credential resolution: ?token= → oa_workspace cookie → bearer ──
  // null = still resolving; '' = no workspace token, which is the normal
  // Placement AI case: the bearer alone authorizes every call this page makes.
  const [token, setToken] = useState<string | null>(null);
  useEffect(() => {
    if (urlToken) { setToken(urlToken); return; }
    const fromCookie = readCookieToken(workspaceId);
    if (fromCookie) { setToken(fromCookie); return; }
    if (authLoading) return;
    // A signed-in student authenticates with the bearer, not a machine token.
    setToken('');
  }, [urlToken, workspaceId, idToken, authLoading]);

  // Load the personal workspace and the access mode once credentials settle.
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [me, setMe] = useState<WorkspaceMe | null>(null);
  const [error, setError] = useState<'denied' | 'load' | null>(null);

  useEffect(() => {
    if (token === null) return;
    workspaceApi.configure(workspaceId, token, idToken || undefined);
    let cancelled = false;
    Promise.all([workspaceApi.getWorkspace(), workspaceApi.getMe()])
      .then(([ws, meData]) => {
        if (cancelled) return;
        setWorkspace(ws);
        setMe(meData);
        setError(null);
      })
      .catch((e) => {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : '';
        setError(msg.includes('API 401') || msg.includes('API 403') ? 'denied' : 'load');
      });
    return () => { cancelled = true; };
  }, [token, workspaceId, idToken]);

  const refreshWorkspace = useCallback(async () => {
    setWorkspace(await workspaceApi.getWorkspace());
  }, []);

  const ctxValue = useMemo<WorkspaceSettingsValue | null>(() => {
    if (!workspace || !me) return null;
    return { workspaceId, workspace, me, token: token || '', refreshWorkspace, query };
  }, [workspaceId, workspace, me, token, refreshWorkspace, query]);

  if (error === 'denied') {
    // A signed-out visitor on the hosted domain may simply need to log in.
    if (isPaiDeployment && !user && !authLoading) {
      return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background p-8">
          <div className="flex flex-col items-center gap-2 text-center">
            <h1 className="text-xl font-semibold">{t('workspaceGate.signInTitle')}</h1>
            <p className="max-w-md text-sm text-muted-foreground">{t('workspaceGate.signInBody')}</p>
          </div>
          <button
            onClick={() => goToSignIn()}
            className="flex items-center gap-3 rounded-lg bg-primary px-6 py-3 font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <LogIn className="size-5" />
            {t('workspaceGate.logIn')}
          </button>
        </div>
      );
    }
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background p-8 text-center">
        <h1 className="text-xl font-semibold">{t('admin.accessDeniedTitle')}</h1>
        <p className="max-w-md text-sm text-muted-foreground">{t('admin.accessDeniedBody')}</p>
        <Button variant="outline" asChild>
          <Link href={`/${workspaceId}${query}`}>{t('admin.backToWorkspace')}</Link>
        </Button>
      </div>
    );
  }

  if (error === 'load') {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background p-8 text-center">
        <p className="text-sm text-muted-foreground">{t('admin.loadFailed')}</p>
        <Button variant="outline" onClick={() => window.location.reload()}>
          {t('admin.retry')}
        </Button>
      </div>
    );
  }

  if (!ctxValue) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="animate-pulse text-sm text-muted-foreground">{t('admin.loading')}</p>
      </div>
    );
  }

  return (
    <WorkspaceSettingsContext.Provider value={ctxValue}>
      <div className="min-h-screen bg-background">
        <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-5xl items-center gap-3 px-4">
            <Link
              href={`/${workspaceId}${query}`}
              className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <ArrowLeft className="size-4" />
              <span className="hidden sm:inline">{t('admin.backToWorkspace')}</span>
            </Link>
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-sm font-semibold">
                {ctxValue.workspace.name}
                <span className="ms-2 font-normal text-muted-foreground">· {t('admin.title')}</span>
              </h1>
            </div>
          </div>
        </header>

        <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 md:flex-row md:gap-10">
          <nav className="flex shrink-0 gap-1 overflow-x-auto md:w-48 md:flex-col md:overflow-visible">
            {SECTIONS.map(({ slug, labelKey, icon: Icon }) => {
              const href = `/${workspaceId}/settings/${slug}${query}`;
              const active = pathname?.endsWith(`/settings/${slug}`);
              return (
                <Link
                  key={slug}
                  href={href}
                  className={`flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors ${
                    active
                      ? 'bg-muted font-medium text-foreground'
                      : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
                  }`}
                >
                  <Icon className="size-4" />
                  {t(labelKey)}
                </Link>
              );
            })}
          </nav>
          <main className="min-w-0 flex-1 pb-16">{children}</main>
        </div>
      </div>
    </WorkspaceSettingsContext.Provider>
  );
}

export default function SettingsLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = use(params);
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <p className="animate-pulse text-sm text-muted-foreground">…</p>
        </div>
      }
    >
      <SettingsShell workspaceId={workspaceId}>{children}</SettingsShell>
    </Suspense>
  );
}
