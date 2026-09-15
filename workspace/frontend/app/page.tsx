'use client';

import { desktopHost } from '@/lib/desktop-host';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import {
  Plus, LogOut, Clock, Loader2,
  ArrowRight,
  Network, Compass, Shield, MonitorSmartphone,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useOpenAgentsAuth } from '@/lib/openagents-auth-context';
import { listAccountWorkspaces, createAccountWorkspace, type AccountWorkspace } from '@/lib/account-api';
import { timeAgo } from '@/lib/helpers';
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
            <Image src="/logo-icon.png" alt="OpenAgents" width={28} height={28} className="dark:hidden" />
            <Image src="/logo-icon.png" alt="OpenAgents" width={28} height={28} className="hidden dark:block" />
            <span className="font-semibold text-lg">OpenAgents</span>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="https://openagents.org/docs/getting-started/overview"
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
            OpenAgents is a shared workspace for your AI agents — chat, collaborate on tasks,
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
            <a href="https://openagents.org/docs/getting-started/overview">
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
            Why OpenAgents
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
            <a href="https://openagents.org/docs/getting-started/overview">
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
            <Image src="/logo-icon.png" alt="OpenAgents" width={20} height={20} />
            <span>OpenAgents</span>
          </div>
          <div className="flex items-center gap-4">
            <a href="https://openagents.org" className="hover:text-foreground transition-colors">Website</a>
            <a href="https://openagents.org/docs/getting-started/overview" className="hover:text-foreground transition-colors">Docs</a>
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

function Kicker({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="inline-block rounded-full border-2 border-black bg-white px-3 py-1 text-[11px] font-extrabold uppercase tracking-wider text-neutral-900"
      style={{ boxShadow: '3px 3px 0 0 #000' }}
    >
      {children}
    </span>
  );
}

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

const ROLE_STYLE: Record<AccountWorkspace['role'], { label: string; badge: string }> = {
  owner: { label: 'Owner', badge: 'border-2 border-black bg-amber-300 text-black' },
  admin: { label: 'Admin', badge: 'border-2 border-black bg-violet-300 text-black' },
  member: { label: 'Member', badge: 'border-2 border-black bg-blue-200 text-black' },
  viewer: { label: 'Viewer', badge: 'border-2 border-black bg-zinc-200 text-black' },
};

// Deterministic gradient + initials for a workspace avatar tile, so each
// workspace has a stable, recognizable color without storing one.
const TILE_GRADIENTS = [
  'from-violet-500 to-indigo-500',
  'from-blue-500 to-cyan-500',
  'from-emerald-500 to-teal-500',
  'from-amber-500 to-orange-500',
  'from-rose-500 to-pink-500',
  'from-fuchsia-500 to-purple-500',
];

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

// ---------------------------------------------------------------------------
function WorkspaceTile({ workspace, highlight = false }: { workspace: AccountWorkspace; highlight?: boolean }) {
  const router = useRouter();
  // Open by slug only — no token in the URL. The workspace page authenticates
  // the signed-in user and resolves the token from their account.
  const href = `/${workspace.slug}`;
  const gradient = TILE_GRADIENTS[hashString(workspace.slug) % TILE_GRADIENTS.length];
  const role = ROLE_STYLE[workspace.role] ?? { label: workspace.role, badge: ROLE_STYLE.viewer.badge };

  return (
    <button
      onClick={() => router.push(href)}
      className="group relative text-left rounded-2xl border-[2.5px] border-black bg-white p-5 transition-all duration-100 hover:-translate-y-1 hover:shadow-[6px_6px_0_0_#000] focus:outline-none focus-visible:-translate-y-1 focus-visible:shadow-[6px_6px_0_0_#000]"
      style={highlight ? { boxShadow: `5px 5px 0 0 ${BRAND.teal}` } : undefined}
    >
      {highlight && (
        <span
          className="absolute -top-3 left-4 rounded-full border-2 border-black px-2.5 py-0.5 text-[10px] font-extrabold uppercase tracking-wide text-neutral-950"
          style={{ backgroundColor: BRAND.teal }}
        >
          ✨ Start here
        </span>
      )}
      <div className="flex items-start gap-3">
        <div className={`size-11 shrink-0 rounded-xl border-2 border-black bg-gradient-to-br ${gradient} flex items-center justify-center text-white font-bold`}>
          {initialsOf(workspace.name)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="font-extrabold tracking-tight text-neutral-900 truncate">{workspace.name}</h3>
            <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wide ${role.badge}`}>
              {role.label}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-neutral-500 font-mono">{workspace.slug}</p>
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between text-xs text-neutral-500">
        <span className="flex items-center gap-1">
          <Clock className="size-3" />
          {workspace.lastActivityAt ? timeAgo(workspace.lastActivityAt) : 'No activity yet'}
        </span>
        <span
          className="flex items-center gap-1 font-bold opacity-0 -translate-x-1 transition-all group-hover:opacity-100 group-hover:translate-x-0"
          style={{ color: BRAND.blue }}
        >
          Open <ArrowRight className="size-3.5" />
        </span>
      </div>
    </button>
  );
}

function CreateTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="group flex min-h-[132px] flex-col items-center justify-center gap-2 rounded-2xl border-[2.5px] border-dashed border-black bg-white/50 p-5 text-neutral-700 transition-all duration-100 hover:-translate-y-1 hover:bg-white hover:shadow-[6px_6px_0_0_#000] focus:outline-none focus-visible:-translate-y-1 focus-visible:shadow-[6px_6px_0_0_#000]"
    >
      <div className="flex size-11 items-center justify-center rounded-xl border-2 border-black">
        <Plus className="size-5" />
      </div>
      <span className="text-sm font-extrabold">New workspace</span>
    </button>
  );
}

function MembershipHome({
  idToken,
  userEmail,
  onSignOut,
}: {
  idToken: string;
  userEmail: string;
  onSignOut: () => void;
}) {
  const router = useRouter();
  const [workspaces, setWorkspaces] = useState<AccountWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);

  const viewTrackedRef = useRef(false);
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    // Network-level failures surface as the browser's raw fetch error
    // ("Failed to fetch" / "Load failed" / "NetworkError…") and are usually
    // transient — a rolling deploy, a flaky mobile/VPN hop, or a blocked
    // request. Retry with backoff before showing anything, and translate the
    // raw error into something actionable instead of leaving a cryptic
    // sticky banner over an empty workspace list.
    const backoffs = [0, 1500, 4000];
    let lastErr: unknown = null;
    for (const delay of backoffs) {
      if (delay) await new Promise((r) => setTimeout(r, delay));
      try {
        const list = await listAccountWorkspaces(idToken);
        setWorkspaces(list);
        // Funnel checkpoint: the signed-in user reached their workspace list
        // (which includes the auto-provisioned first workspace). Once per
        // visit — load() also reruns after create/delete.
        if (!viewTrackedRef.current) {
          viewTrackedRef.current = true;
          capture('membership_home_viewed', { workspace_count: list.length });
        }
        setLoading(false);
        return;
      } catch (err: unknown) {
        lastErr = err;
      }
    }
    const msg = lastErr instanceof Error ? lastErr.message : 'Failed to load workspaces';
    if (/username setup required/i.test(msg)) {
      router.push('/sign-up');
      setLoading(false);
      return;
    }
    setError(
      /failed to fetch|load failed|networkerror/i.test(msg)
        ? "Can't reach the OpenAgents server right now. Check your network (VPN / proxy / firewall) and press Retry."
        : msg,
    );
    setLoading(false);
  }, [idToken, router]);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError('');
    try {
      const ws = await createAccountWorkspace(idToken, newName.trim() || 'Untitled workspace');
      group('workspace', ws.slug);
      capture('workspace_created', { source: 'membership_home', workspace_id: ws.slug });
      router.push(`/${ws.slug}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create workspace');
      setCreating(false);
    }
  };

  const openCreate = () => {
    setShowCreate(true);
    setNewName('');
  };

  // A lone owned workspace = the one we auto-provisioned at sign-up.
  const firstWorkspace =
    !loading && workspaces.length === 1 && workspaces[0].role === 'owner' ? workspaces[0] : null;

  // Single-workspace users go STRAIGHT IN — no picker, no "this is your first
  // workspace" card. A list with one option is pure friction (especially on a
  // phone). Once per browser session, so deliberately navigating back to the
  // home page still shows the list (rename/delete/create live here).
  const willAutoEnter =
    !!firstWorkspace &&
    typeof window !== 'undefined' &&
    sessionStorage.getItem('oa_auto_entered_first_ws') !== '1';
  const autoEnteredRef = useRef(false);
  useEffect(() => {
    if (!willAutoEnter || !firstWorkspace || autoEnteredRef.current) return;
    autoEnteredRef.current = true;
    sessionStorage.setItem('oa_auto_entered_first_ws', '1');
    capture('first_workspace_auto_entered', { workspace_id: firstWorkspace.slug });
    router.push(`/${firstWorkspace.slug}`);
  }, [willAutoEnter, firstWorkspace, router]);

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

  return (
    <div className="min-h-screen text-neutral-900" style={{ background: PAGE_BG }}>
      <header className="sticky top-0 z-10 border-b-2 border-black bg-white/85 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <a
            href="https://openagents.org"
            className="flex items-center gap-2.5 rounded-md transition-transform hover:-translate-y-0.5 focus:outline-none"
            title="Back to OpenAgents home"
          >
            <Image src="/logo-icon.png" alt="OpenAgents" width={26} height={26} />
            <span className="text-lg font-extrabold tracking-tight">OpenAgents</span>
          </a>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div
                className="size-7 rounded-full border-2 border-black flex items-center justify-center text-white text-xs font-bold"
                style={{ background: `linear-gradient(135deg, ${BRAND.blue}, ${BRAND.teal})` }}
              >
                {(userEmail[0] || '?').toUpperCase()}
              </div>
              <span className="text-sm text-neutral-600 hidden sm:inline">{userEmail}</span>
            </div>
            <button
              onClick={handleSignOut}
              title="Sign out"
              className="inline-flex size-8 items-center justify-center rounded-md border-2 border-black bg-white text-neutral-700 transition-all hover:bg-neutral-100 hover:shadow-[2px_2px_0_0_#000]"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10 sm:py-14">
        {/* Hero */}
        <div className="mb-8">
          <Kicker>Workspaces</Kicker>
          <h1 className="mt-4 text-3xl sm:text-4xl font-black tracking-tight">Your workspaces</h1>
          <p className="mt-2 text-neutral-600">
            Jump back into a workspace, or start something new.
            {!loading && workspaces.length > 0 && (
              <span className="text-neutral-400">
                {' '}· {workspaces.length} workspace{workspaces.length !== 1 ? 's' : ''}
              </span>
            )}
          </p>
        </div>

        {error && (
          <div
            className="mb-6 flex items-center justify-between gap-3 rounded-xl border-2 border-black bg-red-100 p-3 text-sm font-medium text-red-700"
            style={{ boxShadow: '3px 3px 0 0 #000' }}
          >
            <span>{error}</span>
            <button
              onClick={load}
              className="shrink-0 rounded-lg border-2 border-black bg-white px-3 py-1 text-xs font-bold text-black hover:bg-zinc-100 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {showCreate && (
          <div
            className="mb-6 rounded-2xl border-[2.5px] border-black bg-white p-5"
            style={{ boxShadow: '6px 6px 0 0 #000' }}
          >
            <form onSubmit={handleCreate} className="space-y-3">
              <h3 className="font-extrabold tracking-tight">Name your workspace</h3>
              <Input
                placeholder="e.g. Marketing team, Acme Corp…"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                autoFocus
                className="border-2 border-black focus-visible:border-black focus-visible:ring-0"
              />
              <div className="flex items-center gap-2">
                <BrutalBtn type="submit" disabled={creating} color="blue">
                  {creating ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
                  Create workspace
                </BrutalBtn>
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="inline-flex items-center rounded-[5px] px-4 py-2.5 text-sm font-bold text-neutral-600 transition-colors hover:text-black"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        )}

        {loading || willAutoEnter ? (
          // Auto-entering renders the same skeleton as loading: the redirect
          // fires from the effect above, so the picker never flashes.
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[132px] rounded-2xl border-[2.5px] border-black bg-white/60 animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {!showCreate && <CreateTile onClick={openCreate} />}
            {workspaces.map((ws) => (
              <WorkspaceTile key={ws.workspaceId} workspace={ws} />
            ))}
          </div>
        )}

      </main>
    </div>
  );
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
        <Image src="/logo-icon.png" alt="OpenAgents" width={44} height={44} />
        <h1 className="text-2xl font-black tracking-tight">Sign in to OpenAgents</h1>
        <p className="text-neutral-600 text-sm text-center max-w-md">
          Sign in to see your workspaces.
        </p>
      </div>
      <BrutalBtn onClick={host ? signIn : () => router.push('/sign-in')} color="blue">
        Sign in to OpenAgents
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

  // On the OpenAgents-hosted app, `/` is the enforced-login Membership Home.
  if (oa.isOpenAgentsDomain) {
    if (!oa.user || !oa.idToken) return <SignInGate signIn={oa.signIn} />;
    return <MembershipHome idToken={oa.idToken} userEmail={oa.user.email} onSignOut={oa.signOut} />;
  }

  // Non-OpenAgents / self-hosted host: show the informational landing page for
  // now. (The legacy email/password dashboard was removed in v1.0; proper
  // self-hosted account handling is a later decision.)
  return <LandingPage />;
}
