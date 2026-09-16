'use client';

import { useEffect, useRef, useState } from 'react';
import { workspaceApi } from '@/lib/api';
import type { OperatorRun } from '@/lib/types';

const ACTIVE_POLL_MS = 4_000;
const IDLE_POLL_MS = 20_000;

/**
 * PAI Operator's most recent run, kept current by polling — the read side of
 * the "PAI is working…" indicator. Operator is never chatted with directly,
 * so there is no SSE thread to piggyback on the way messages do; a run is
 * rare and short-lived enough that polling is the right amount of
 * infrastructure for it, not a new realtime channel.
 *
 * Polls faster while something is actually running, and stops entirely once
 * it settles into a terminal state — this hook is not a persistent status
 * bar, it is "is there anything to show right now".
 */
export function useOperatorStatus(enabled: boolean): OperatorRun | null {
  const [run, setRun] = useState<OperatorRun | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!enabled) {
      setRun(null);
      return;
    }
    let cancelled = false;

    const tick = async () => {
      let latest: OperatorRun | null = null;
      try {
        // Unfiltered: the most recent run regardless of status, so a just-
        // finished run's result (e.g. "needs your approval") stays visible
        // instead of vanishing the instant it leaves the active states.
        const { runs } = await workspaceApi.listOperatorRuns({ limit: 1 });
        latest = runs[0] ?? null;
        if (!cancelled) setRun(latest);
      } catch {
        // Best-effort — try again on the next tick.
      }
      if (cancelled) return;
      const stillWorking = latest && !['completed', 'needs_user_action', 'failed'].includes(latest.status);
      timeoutRef.current = setTimeout(tick, stillWorking ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return run;
}
