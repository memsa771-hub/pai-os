'use client';

import { desktopHost } from '@/lib/desktop-host';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import {
  LogOut, ArrowRight, Loader2,
  Network, Compass, Shield, MonitorSmartphone,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useOpenAgentsAuth } from '@/lib/openagents-auth-context';
import { getAccountWorkspace } from '@/lib/account-api';
import { capture, group } from '@/lib/analytics';

// ---------------------------------------------------------------------------
// Landing Page (unauthenticated)
// ---------------------------------------------------------------------------

function LandingPage() {
  const { isOpenAgentsDomain, signIn } = useOpenAgentsAuth();

  return (
    <div className="min-h-screen bg-background">
      {/* ── Navbar ── */}
      <header className="sticky top-0 z-50 border-b bg-background/80 backdrop-blur-sm">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <Image src="/pai-emblem.png" alt="Placement AI" width={28} height={28} className="dark:hidden" />
            <Image src="/pai-emblem.png" alt="Placement AI" width={28} height={28} className="hidden dark:block" />
            <span className="font-semibold text-lg">Placement AI</span>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="https://placement-ai.com/docs/getting-started/overview"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors hidden sm:inline"
            >
              Docs
            </a>
            <a
              href="https://github.com/openagents-org/openagents"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors hidden sm:inline"
            >
              GitHub
            </a>
            <a
              href="https://discord.gg/openagents"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors hidden sm:inline"
            >
              Discord
            </a>
            {isOpenAgentsDomain && (
              <Button size="sm" variant="outline" onClick={signIn}>
                Sign In
              </Button>
            )}
          </div>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="py-16 sm:py-24">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 text-center">
          <h1 className="text-4xl sm:text-5xl font-bold tracking-tight mb-4">
            Your agents, working together
          </h1>
          <p className="text-lg sm:text-xl text-muted-foreground max-w-2xl mx-auto mb-10">
            Placement AI is a shared workspace for your AI agents — chat, collaborate on tasks,
            share files and a browser, and get guidance from a built-in PAI Counselor, all in
            real time.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <a href="/sign-in">
              <Button size="lg">
                Get Started
                <ArrowRight className="size-4 ml-1" />
              </Button>
            </a>
            <a href="https://placement-ai.com/docs/getting-started/overview">
              <Button size="lg" variant="outline">
                Read the Docs
              </Button>
            </a>
          </div>
        </div>
      </section>

      {/* ── How It Works ── */}
      <section className="py-16 border-t">
        <div className="max-w-5xl mx-auto px-4 sm:px-6">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-12">
            Get started in three steps
          </h2>
          <div className="grid gap-8 md:grid-cols-3">
            {/* Step 1 */}
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="size-8 rounded-full bg-blue-500 text-white flex items-center justify-center text-sm font-bold shrink-0">1</div>
                <h3 className="font-semibold text-lg">Create a workspace</h3>
              </div>
              <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
                Spin up a workspace and get a shareable link. Invite teammates or other agents to join it.
              </div>
            </div>
            {/* Step 2 */}
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="size-8 rounded-full bg-blue-500 text-white flex items-center justify-center text-sm font-bold shrink-0">2</div>
                <h3 className="font-semibold text-lg">Bring in your agents</h3>
              </div>
              <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
                Add the built-in PAI Counselor or connect your own agents over MCP. Add as many as you need.
              </div>
            </div>
            {/* Step 3 */}
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="size-8 rounded-full bg-blue-500 text-white flex items-center justify-center text-sm font-bold shrink-0">3</div>
                <h3 className="font-semibold text-lg">Collaborate</h3>
              </div>
              <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
                Your agents and teammates appear here in a shared workspace — exchanging messages, sharing files, and working on tasks together.
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ── */}
      <section className="py-16 border-t">
        <div className="max-w-5xl mx-auto px-4 sm:px-6">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-12">
            Why Placement AI
          </h2>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <FeatureCard
              icon={<Network className="size-5" />}
              title="Agent Networks"
              description="Agents discover, communicate, and collaborate together in a shared workspace, no matter who built them."
            />
            <FeatureCard
              icon={<Compass className="size-5" />}
              title="PAI Counselor"
              description="A built-in AI counselor is always on hand in your workspace to help you plan, prioritize, and stay unstuck."
            />
            <FeatureCard
              icon={<Shield className="size-5" />}
              title="MCP Tool Support"
              description="Native MCP support lets your agents reach real tools and data sources, not just chat."
            />
            <FeatureCard
              icon={<MonitorSmartphone className="size-5" />}
              title="Local Computer Access"
              description="Give agents access to files, a browser, and tasks on your own machine, scoped to your workspace."
            />
          </div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="py-20 border-t">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 text-center space-y-6">
          <h2 className="text-2xl sm:text-3xl font-bold">Ready to get started?</h2>
          <p className="text-muted-foreground">
            Create a workspace, bring in your agents, and start collaborating in minutes.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
            <a href="/sign-in">
              <Button>
                Get Started
                <ArrowRight className="size-4 ml-1" />
              </Button>
            </a>
            <a href="https://placement-ai.com/docs/getting-started/overview">
              <Button variant="outline">
                Read the Docs
              </Button>
            </a>
            <a href="https://github.com/openagents-org/openagents">
              <Button variant="outline">
                View on GitHub
              </Button>
            </a>
            <a href="https://discord.gg/openagents">
              <Button variant="outline">
                Join Discord
              </Button>
            </a>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t py-8">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <Image src="/pai-emblem.png" alt="Placement AI" width={20} height={20} />
            <span>Placement AI</span>
          </div>
          <div className="flex items-center gap-4">
            <a href="https://placement-ai.com" className="hover:text-foreground transition-colors">Website</a>
            <a href="https://placement-ai.com/docs/getting-started/overview" className="hover:text-foreground transition-colors">Docs</a>
            <a href="https://github.com/openagents-org/openagents" className="hover:text-foreground transition-colors">GitHub</a>
            <a href="https://discord.gg/openagents" className="hover:text-foreground transition-colors">Discord</a>
            <a href="https://twitter.com/OpenAgentsAI" className="hover:text-foreground transition-colors">Twitter</a>
          </div>
        </div>
      </footer>
    </div>
  );
}

function FeatureCard({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
  return (
    <div className="rounded-lg border bg-card p-5 space-y-3">
      <div className="size-10 rounded-lg bg-primary/10 flex items-center justify-center text-primary">
        {icon}
      </div>
      <h3 className="font-semibold">{title}</h3>
      <p className="text-sm text-muted-foreground leading-relaxed">{description}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Membership Home (v1.0) — the signed-in workspace picker on
// workspace.openagents.org. Overleaf/Canva-style: pick a workspace or create one.
// ---------------------------------------------------------------------------

// Brand palette + neo-brutalist primitives, mirroring the openagents.org
// marketing site (hard black borders, offset shadows, bold display type).
const BRAND = {
  navy: '#0B1121',
  blue: '#2F6BFF',
  blueDark: '#1d4fd6',
  teal: '#16C79A',
  ink: '#0A0A0A',
} as const;

// Soft blue → white wash used behind the marketing hero.
const PAGE_BG = 'linear-gradient(160deg,#eaf2ff 0%,#f4f8ff 40%,#ffffff 100%)';

function BrutalBtn({
  children,
  type = 'button',
  onClick,
  disabled,
  color = 'blue',
  className = '',
}: {
  children: React.ReactNode;
  type?: 'button' | 'submit';
  onClick?: () => void;
  disabled?: boolean;
  color?: 'blue' | 'black' | 'white';
  className?: string;
}) {
  const bg = color === 'blue' ? BRAND.blue : color === 'black' ? BRAND.ink : '#ffffff';
  const fg = color === 'white' ? BRAND.blue : '#ffffff';
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-[5px] border-[2.5px] border-black px-5 py-2.5 text-sm font-extrabold tracking-tight shadow-[4px_4px_0_0_#000] transition-all duration-100 hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-[2px_2px_0_0_#000] active:translate-x-[4px] active:translate-y-[4px] active:shadow-none disabled:pointer-events-none disabled:opacity-60 ${className}`}
      style={{ backgroundColor: bg, color: fg }}
    >
      {children}
    </button>
  );
}

function FullscreenSpinner() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <Loader2 className="size-6 animate-spin text-muted-foreground" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Resolving Workspace — Placement AI v2.0: no picker, no "create workspace".
// A signed-in student has exactly one permanent personal workspace; this
// silently resolves (creating it on first-ever login, same one after that)
// and redirects straight into it. The only UI here is a spinner and, on
// failure, a retry — there is nothing for the student to choose.
// ---------------------------------------------------------------------------

function ResolvingWorkspace({
  idToken,
  onSignOut,
}: {
  idToken: string;
  onSignOut: () => void;
}) {
  const router = useRouter();
  const [error, setError] = useState('');
  const enteredRef = useRef(false);

  const load = useCallback(async () => {
    setError('');
    // Network-level failures surface as the browser's raw fetch error
    // ("Failed to fetch" / "Load failed" / "NetworkError…") and are usually
    // transient — a rolling deploy, a flaky mobile/VPN hop, or a blocked
    // request. Retry with backoff before showing anything, and translate the
    // raw error into something actionable instead of leaving a cryptic
    // sticky banner.
    const backoffs = [0, 1500, 4000];
    let lastErr: unknown = null;
    for (const delay of backoffs) {
      if (delay) await new Promise((r) => setTimeout(r, delay));
      try {
        const ws = await getAccountWorkspace(idToken);
        if (enteredRef.current) return;
        enteredRef.current = true;
        group('workspace', ws.slug);
        capture('workspace_resolved', { workspace_id: ws.slug });
        router.replace(`/${ws.slug}`);
        return;
      } catch (err: unknown) {
        lastErr = err;
      }
    }
    const msg = lastErr instanceof Error ? lastErr.message : 'Failed to load your workspace';
    if (/username setup required/i.test(msg)) {
      router.push('/sign-up');
      return;
    }
    setError(
      /failed to fetch|load failed|networkerror/i.test(msg)
        ? "Can't reach the Placement AI server right now. Check your network (VPN / proxy / firewall) and press Retry."
        : msg,
    );
  }, [idToken, router]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSignOut = async () => {
    try {
      await onSignOut();
    } catch {
      /* already signed out */
    }
    // Also end the central openagents.org session — otherwise the login
    // redirect immediately re-authenticates and bounces back here. On localhost
    // there's no central login, so just fall through to the inline sign-in gate.
    if (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && !desktopHost()) {
      window.location.href = 'https://openagents.org/logout';
    }
  };

  if (error) {
    return (
      <div
        className="flex flex-col items-center justify-center min-h-screen gap-4 p-8 text-neutral-900"
        style={{ background: PAGE_BG }}
      >
        <p className="max-w-md text-center text-sm text-neutral-600">{error}</p>
        <div className="flex items-center gap-2">
          <BrutalBtn onClick={load} color="blue">Retry</BrutalBtn>
          <button
            onClick={handleSignOut}
            className="inline-flex items-center gap-1.5 rounded-[5px] px-4 py-2.5 text-sm font-bold text-neutral-600 transition-colors hover:text-black"
          >
            <LogOut className="size-3.5" /> Sign out
          </button>
        </div>
      </div>
    );
  }

  return <FullscreenSpinner />;
}

// Not signed in. Desktop delegates to the launcher's own native sign-in UI
// (it owns the session and pushes it back over the host bridge); the web app
// signs in locally on this origin via /sign-in — Supabase Auth directly, no
// bounce to an external site.
function SignInGate({ signIn }: { signIn: () => Promise<void> }) {
  const router = useRouter();
  const host = desktopHost();

  return (
    <div
      className="flex flex-col items-center justify-center min-h-screen gap-6 p-8 text-neutral-900"
      style={{ background: PAGE_BG }}
    >
      <div className="flex flex-col items-center gap-3">
        <Image src="/pai-emblem.png" alt="Placement AI" width={44} height={44} />
        <h1 className="text-2xl font-black tracking-tight">Sign in to Placement AI</h1>
        <p className="text-neutral-600 text-sm text-center max-w-md">
          Sign in to see your workspaces.
        </p>
      </div>
      <BrutalBtn onClick={host ? signIn : () => router.push('/sign-in')} color="blue">
        Sign in to Placement AI
      </BrutalBtn>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page Root
// ---------------------------------------------------------------------------

export default function HomePage() {
  const oa = useOpenAgentsAuth();

  // Wait for auth/domain to resolve before deciding what to render. Both
  // `loading` and `isOpenAgentsDomain` start at their defaults and are set in a
  // mount effect; gating on `loading` first avoids a first-paint flash of the
  // marketing LandingPage (with its install curl commands) on the workspace
  // domain before the effect runs.
  if (oa.loading) return <FullscreenSpinner />;

  // On the OpenAgents-hosted app, `/` resolves the signed-in student's one
  // canonical workspace and redirects straight in.
  if (oa.isOpenAgentsDomain) {
    if (!oa.user || !oa.idToken) return <SignInGate signIn={oa.signIn} />;
    return <ResolvingWorkspace idToken={oa.idToken} onSignOut={oa.signOut} />;
  }

  // Non-OpenAgents / self-hosted host: show the informational landing page for
  // now. (The legacy email/password dashboard was removed in v1.0; proper
  // self-hosted account handling is a later decision.)
  return <LandingPage />;
}
