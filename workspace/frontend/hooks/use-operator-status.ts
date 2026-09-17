'use client';

import { useEffect, useRef, useState } from 'react';
import { workspaceApi } from '@/lib/api';
import type { OperatorRun } from '@/lib/types';

const ACTIVE_POLL_MS = 4_000;
const IDLE_POLL_MS = 20_000;

/**
 * PAI Operator's most recent run — the read side of the "PAI is working…"
 * indicator. Backed by realtime SSE first: every meaningful status change
 * PAI Operator makes is persisted to Postgres AND published as a
 * "workspace.operator.run_updated" event on target "core" (see
 * `operator._publish_run_updated`), and this hook subscribes to exactly that
 * stream via `getOperatorEventsUrl()` — the same SSE mechanism chat messages
 * already use (see hooks/use-polling.ts), just filtered by target instead of
 * channel since Operator has no thread of its own.
 *
 * Polling is only the fallback: it seeds the initial state (SSE only carries
 * events published after the connection opens) and takes back over if the
 * SSE connection drops. This hook never simulates progress on its own — it
 * only ever reflects a value that came from the server.
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
    let eventSource: EventSource | null = null;
    let polling = false;

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
      if (cancelled || !polling) return;
      const stillWorking = latest && !['completed', 'needs_user_action', 'failed'].includes(latest.status);
      timeoutRef.current = setTimeout(tick, stillWorking ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    };

    const startPolling = () => {
      if (polling) return;
      polling = true;
      void tick();
    };

    const stopPolling = () => {
      polling = false;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };

    // Seed initial state, then prefer the realtime stream.
    workspaceApi.listOperatorRuns({ limit: 1 }).then(({ runs }) => {
      if (!cancelled) setRun(runs[0] ?? null);
    }).catch(() => {
      // Best-effort — SSE (or the polling fallback below) will catch up.
    });

    try {
      eventSource = new EventSource(workspaceApi.getOperatorEventsUrl());
      eventSource.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data);
          if (event.type !== 'workspace.operator.run_updated') return;
          if (!cancelled) setRun(workspaceApi.mapOperatorRun(event.payload));
        } catch {
          // malformed event
        }
      };
      eventSource.onerror = () => {
        eventSource?.close();
        eventSource = null;
        startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      cancelled = true;
      stopPolling();
      eventSource?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return run;
}
