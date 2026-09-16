'use client';

import { Cog } from 'lucide-react';
import { AgentAvatar } from '@/components/agents/agent-avatar';
import { cn } from '@/lib/utils';
import { useT } from '@/lib/i18n';
import { useOperatorStatus } from '@/hooks/use-operator-status';
import type { OperatorRun } from '@/lib/types';

export type OperatorTone = 'ready' | 'working' | 'approval' | 'error';

export const OPERATOR_DOT_CLASS: Record<OperatorTone, string> = {
  ready: 'bg-emerald-500',
  working: 'animate-pulse bg-primary',
  approval: 'bg-amber-500',
  error: 'bg-destructive',
};

function operatorTone(run: OperatorRun | null): OperatorTone {
  if (!run || run.status === 'completed') return 'ready';
  if (run.status === 'failed') return 'error';
  if (run.status === 'needs_user_action') return 'approval';
  return 'working';
}

/**
 * PAI's two built-in intelligences, shown as identity + status — never a
 * picker. PAI Counselor is the only one that is ever a click target (it
 * opens the one canonical conversation); PAI Operator here is plain text; it
 * has no chat, no route, nothing to select. Same component on Web and
 * Desktop, since both render workspace/frontend.
 *
 * Shared by the sidebar identity block and the chat header's compact badge
 * (see components/chat/chat-view.tsx) — one source for tone/label so the two
 * can never disagree about what "working" means.
 */
export function usePaiSystemStatus() {
  const t = useT();
  const run = useOperatorStatus(true);
  const tone = operatorTone(run);
  const label = {
    ready: t('paiSystem.operatorReady'),
    working: t('paiSystem.operatorWorking'),
    approval: t('paiSystem.operatorNeedsApproval'),
    error: t('paiSystem.operatorError'),
  }[tone];
  return { operatorRun: run, tone, label };
}

export function PaiSystemStatus({
  onOpenCounselor,
  isCounselorActive,
}: {
  onOpenCounselor: () => void;
  isCounselorActive: boolean;
}) {
  const t = useT();
  const { operatorRun, tone, label: operatorStatusLabel } = usePaiSystemStatus();

  return (
    <div className="flex flex-col gap-2.5 px-2 py-1.5">
      {/* PAI Counselor — the only clickable, selectable row here. */}
      <button
        type="button"
        onClick={onOpenCounselor}
        aria-current={isCounselorActive || undefined}
        className={cn(
          'flex items-center gap-2 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-sidebar-accent',
          isCounselorActive && 'bg-sidebar-accent',
        )}
      >
        <AgentAvatar name="pai" size={20} className="shrink-0 [&_svg]:size-full!" />
        <div className="min-w-0 flex-1" title={t('paiSystem.counselorRole')}>
          <span className="block truncate text-sm font-medium text-foreground">{t('views.paiCounselor')}</span>
          <span className="flex items-center gap-1 text-2xs leading-tight text-muted-foreground">
            <span className="size-1.5 shrink-0 rounded-full bg-emerald-500" aria-hidden="true" />
            <span className="truncate">{t('paiSystem.counselorActive')}</span>
          </span>
        </div>
      </button>

      {/* PAI Operator — identity + status only. Not a button: nothing here
          navigates anywhere or opens a chat. */}
      <div
        className="flex items-center gap-2 px-1 py-0.5"
        aria-live="polite"
        title={`${t('paiSystem.operatorRole')} — ${operatorRun?.currentStep || operatorStatusLabel}`}
      >
        <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Cog className={cn('size-3', tone === 'working' && 'animate-spin')} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">{t('paiSystem.operatorName')}</span>
          <span className="flex items-center gap-1 text-2xs leading-tight text-muted-foreground">
            <span className={cn('size-1.5 shrink-0 rounded-full', OPERATOR_DOT_CLASS[tone])} aria-hidden="true" />
            <span className="truncate">
              {tone === 'working' && operatorRun?.currentStep ? operatorRun.currentStep : operatorStatusLabel}
            </span>
          </span>
        </div>
      </div>
    </div>
  );
}
