import React, { useEffect, useMemo, useRef } from 'react';
import { ThemeProvider, useTheme } from 'next-themes';

import { Toaster } from '@/components/ui/sonner';
import { DialogsProvider } from '@/components/ui/dialogs-provider';
import { PaiAuthProvider } from '@/lib/pai-auth-context';
import { DEFAULT_LOCALE, I18nProvider } from '@/lib/i18n';

import Home from '@/app/page';
import NotFound from '@/app/not-found';
import WorkspacePage from '@/app/[workspaceId]/page';
import SettingsLayout from '@/app/[workspaceId]/settings/layout';
import SettingsIndex from '@/app/[workspaceId]/settings/page';
import SettingsGeneral from '@/app/[workspaceId]/settings/general/page';
import SettingsIntegrations from '@/app/[workspaceId]/settings/integrations/page';
import SettingsPreferences from '@/app/[workspaceId]/settings/preferences/page';
import SettingsProfile from '@/app/[workspaceId]/settings/profile/page';
import SettingsSecurity from '@/app/[workspaceId]/settings/security/page';
import SharePage from '@/app/share/[token]/page';

import { DesktopRouter, type RouteTable } from './router';
import { reportLocale, reportTheme, useHostAppearance, useHostNotices } from './host';

/**
 * The desktop build's root.
 *
 * Stands in for `app/layout.tsx`, which cannot be reused as-is: it is a Server
 * Component that resolves the locale from request headers and renders <html>
 * and <body>. Everything BELOW that — the provider stack — is the same, in the
 * same order, and every page component underneath is imported from `app/`
 * untouched.
 *
 * The analytics snippets the web layout injects are deliberately absent: a
 * signed desktop binary should not fetch and run third-party script at start-up.
 */

/**
 * A promise `use()` can read without waiting.
 *
 * React reads `status`/`value` off a thenable and returns it synchronously
 * when they are there — the convention Next.js itself follows for the params
 * it hands a page. A bare `Promise.resolve()` has neither, so `use()` suspends
 * instead; see Page below for why that is fatal here.
 */
type Fulfilled<T> = Promise<T> & { status: 'fulfilled'; value: T };

function fulfilled<T>(value: T): Fulfilled<T> {
  const promise = Promise.resolve(value) as Fulfilled<T>;
  promise.status = 'fulfilled';
  promise.value = value;
  return promise;
}

/**
 * Next's page components take `params` as a promise and unwrap it with `use()`.
 *
 * Handing them a plain resolved promise suspends the first render of every
 * page that takes params, and there is no Suspense boundary above the router
 * to catch it: React ends the render with "an unknown Component is an async
 * Client Component" and the window goes blank. Every route except `/` takes
 * params, so that was the whole app.
 *
 * Memoised on the values it carries — a fresh promise per render would be a
 * fresh identity for `use()` on every pass.
 */
function Page({
  params,
  children,
}: {
  params: Record<string, string>;
  children: (params: Promise<Record<string, string>>) => React.ReactNode;
}): React.JSX.Element {
  const key = JSON.stringify(params);
  const promise = useMemo(() => fulfilled(params), [key]);
  return <>{children(promise)}</>;
}

/**
 * A settings page, inside the layout that gives it its rail and header.
 *
 * The page gets `params` as well as the layout. Most settings pages ignore it
 * — they read the workspace from context — but the index page takes it and
 * unwraps it with `use()`, and `use(undefined)` throws hard enough to blank
 * the window. It only showed once the layout had LOADED, since a failing
 * layout never renders its children, which is why "open workspace settings"
 * was a white screen while every other route looked fine.
 *
 * Passing it to all of them costs nothing: a component that does not name the
 * prop never sees it.
 */
function settingsRoute(
  pattern: string,
  Component: React.ComponentType<{ params: Promise<{ workspaceId: string }> }>,
): RouteTable[number] {
  return {
    pattern,
    render: (params) => (
      <Page params={params}>
        {(promise) => {
          const workspaceParams = promise as Promise<{ workspaceId: string }>;
          return (
            <SettingsLayout params={workspaceParams}>
              <Component params={workspaceParams} />
            </SettingsLayout>
          );
        }}
      </Page>
    ),
  };
}

/**
 * The app's routes, mirroring the `app/` directory. Most specific first: the
 * matcher takes the first pattern that fits, and `/:workspaceId` would
 * otherwise swallow `/invite/abc`.
 */
const ROUTES: RouteTable = [
  { pattern: '/', render: () => <Home /> },
  {
    pattern: '/share/:token',
    render: (params) => (
      <Page params={params}>
        {(promise) => <SharePage params={promise as Promise<{ token: string }>} />}
      </Page>
    ),
  },
  settingsRoute('/:workspaceId/settings', SettingsIndex),
  settingsRoute('/:workspaceId/settings/general', SettingsGeneral),
  settingsRoute('/:workspaceId/settings/integrations', SettingsIntegrations),
  settingsRoute('/:workspaceId/settings/preferences', SettingsPreferences),
  settingsRoute('/:workspaceId/settings/profile', SettingsProfile),
  settingsRoute('/:workspaceId/settings/security', SettingsSecurity),
  {
    pattern: '/:workspaceId',
    render: (params) => (
      <Page params={params}>
        {(promise) => (
          <WorkspacePage params={promise as Promise<{ workspaceId: string }>} />
        )}
      </Page>
    ),
  },
];

export default function App(): React.JSX.Element {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <I18nProvider>
        <AppearanceSync />
        <PaiAuthProvider>
          <DialogsProvider>
            <DesktopRouter routes={ROUTES} notFound={<NotFound />} />
          </DialogsProvider>
        </PaiAuthProvider>
        <Toaster />
        <HostNotices />
      </I18nProvider>
    </ThemeProvider>
  );
}

/** The launcher's notices, shown here where its own toasts are covered. */
function HostNotices(): null {
  useHostNotices();
  return null;
}

/**
 * Keeps the launcher's theme in step with this app's.
 *
 * Only acts on a value that differs from what it already holds, which is
 * what stops the two from handing a change back and forth forever.
 *
 * Renders nothing; it exists for the effects. On the web `useHostAppearance`
 * returns null and every branch here is skipped.
 *
 * Language is not synced: the workspace is English-only, so there is nothing
 * for the host to hand this app, and nothing for this app to report back
 * beyond the fixed `DEFAULT_LOCALE`.
 */
function AppearanceSync(): null {
  const host = useHostAppearance();
  const { theme, setTheme } = useTheme();
  // Both effects run in the same commit. Remember a host-originated update so
  // the workspace's still-stale value is not immediately sent back, creating
  // an endless light/dark ping-pong that looks like a blinking window.
  const applyingHostTheme = useRef<string | null>(null);

  // Host → app.
  useEffect(() => {
    if (!host) return;
    if (host.theme && host.theme !== theme) {
      applyingHostTheme.current = host.theme;
      setTheme(host.theme);
    }
  }, [host, theme, setTheme]);

  // App → host. `theme` is undefined until next-themes has read storage.
  useEffect(() => {
    if (!host || !theme) return;
    if (applyingHostTheme.current) {
      if (theme === applyingHostTheme.current) applyingHostTheme.current = null;
      return;
    }
    if (theme === host.theme) return;
    reportTheme(theme);
  }, [host, theme]);

  useEffect(() => {
    if (!host || host.locale === DEFAULT_LOCALE) return;
    reportLocale(DEFAULT_LOCALE);
  }, [host]);

  return null;
}
