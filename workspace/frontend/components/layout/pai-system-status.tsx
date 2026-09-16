'use client';

import { Cog } from 'lucide-react';
import { AgentAvatar } from '@/components/agents/agent-avatar';
import { cn } from '@/lib/utils';
import { useT } from '@/lib/i18n';
import { useOperatorStatus } from '@/hooks/use-operator-status';
import type { OperatorRun } from '@/lib/types';

type OperatorTone = 'ready' | 'working' | 'approval' | 'error';

const DOT_CLASS: Record<OperatorTone, string> = {
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
 */
export function usePaiSystemStatus() {
  const run = useOperatorStatus(true);
  return { operatorRun: run, tone: operatorTone(run) };
}

export function PaiSystemStatus({
  onOpenCounselor,
  isCounselorActive,
}: {
  onOpenCounselor: () => void;
  isCounselorActive: boolean;
}) {
  const t = useT();
  const { operatorRun, tone } = usePaiSystemStatus();

  const operatorStatusLabel = {
    ready: t('paiSystem.operatorReady'),
    working: t('paiSystem.operatorWorking'),
    approval: t('paiSystem.operatorNeedsApproval'),
    error: t('paiSystem.operatorError'),
  }[tone];

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
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-foreground">{t('views.paiCounselor')}</span>
            <span className="size-1.5 shrink-0 rounded-full bg-emerald-500" aria-hidden="true" title={t('paiSystem.counselorActive')} />
          </div>
          <div className="truncate text-2xs text-muted-foreground">{t('paiSystem.counselorRole')}</div>
        </div>
      </button>

      {/* PAI Operator — identity + status only. Not a button: nothing here
          navigates anywhere or opens a chat. */}
      <div className="flex items-center gap-2 px-1 py-0.5" aria-live="polite">
        <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Cog className="size-3" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-foreground">{t('paiSystem.operatorName')}</span>
            <span className={cn('size-1.5 shrink-0 rounded-full', DOT_CLASS[tone])} aria-hidden="true" title={operatorStatusLabel} />
          </div>
          <div className="truncate text-2xs text-muted-foreground">
            {tone === 'working' && operatorRun?.currentStep
              ? operatorRun.currentStep
              : `${t('paiSystem.operatorRole')} · ${operatorStatusLabel}`}
          </div>
        </div>
      </div>
    </div>
  );
}
