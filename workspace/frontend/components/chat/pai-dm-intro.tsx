'use client';

import { MessageSquare } from 'lucide-react';

/**
 * Empty-state for a DM with the built-in PAI Counselor assistant: instead of a blank
 * thread, PAI Counselor "opens" with what she can do, plus quick actions that send a
 * real message so her tool loop answers.
 */
export function PaiDmIntro({ agentLabel, onQuick }: {
  agentLabel: string;
  onQuick: (text: string) => void;
}) {
  const quickPrompt = 'Help me plan my education journey';
  return (
    <div className="flex-1 overflow-y-auto px-4 lg:px-8 py-6">
      <div className="mx-auto w-full max-w-3xl xl:max-w-4xl 2xl:max-w-6xl">
        <div className="flex items-start gap-3">
          <img src="/pai-avatar.png" alt="" className="size-8 shrink-0 rounded-full object-cover mt-0.5" draggable={false} />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold leading-tight">{agentLabel}</div>
            <div className="mt-1.5 space-y-3 text-sm leading-relaxed">
              <p>Tell me where you are in your education journey, or what you want to achieve, and I&apos;ll help you figure out the best next step.</p>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <button
                onClick={() => onQuick(quickPrompt)}
                className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
              >
                <MessageSquare className="size-3.5" />{quickPrompt}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
