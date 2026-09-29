'use client';

import { useCallback, useEffect, useState } from 'react';
import { Check, Cloud, Copy, Monitor, Pencil, Plus, Sparkles, Trash2, UserRoundCog, X } from 'lucide-react';
import { toast } from 'sonner';
import { AgentAvatar } from '@/components/agents/agent-avatar';
import { useLayout } from '@/components/layout/layout-context';
import { useConfirm } from '@/components/ui/dialogs-provider';
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard';
import { workspaceApi } from '@/lib/api';
import { agentLabel } from '@/lib/helpers';
import { useT } from '@/lib/i18n';
import { PAI_PRIMARY_CONVERSATION_ID } from '@/lib/primary-conversation';
import { cn } from '@/lib/utils';
import { useWorkspace } from '@/lib/workspace-context';

export function AgentProfilePanel({ docked = false }: { docked?: boolean } = {}) {
  const { selectedAgentName, setSelectedAgentName, isMobile, setViewMode, openMobileDetail } = useLayout();
  const { agents, refreshWorkspace, createSession, setCurrentSessionId } = useWorkspace();
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const confirm = useConfirm();
  const t = useT();
  const agent = agents.find((item) => item.agentName === selectedAgentName);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [description, setDescription] = useState('');
  const [savingDescription, setSavingDescription] = useState(false);
  const [descriptionDirty, setDescriptionDirty] = useState(false);

  useEffect(() => {
    setEditingName(false);
    setNameDraft(agent?.displayName || '');
    setDescription(agent?.description || '');
    setDescriptionDirty(false);
  }, [agent?.agentName, agent?.displayName, agent?.description]);

  const saveDisplayName = useCallback(async () => {
    if (!agent) return;
    const displayName = nameDraft.trim();
    if (displayName === (agent.displayName || '')) {
      setEditingName(false);
      return;
    }
    setSavingName(true);
    try {
      await workspaceApi.updateMember(agent.agentName, { display_name: displayName });
      await refreshWorkspace();
      setEditingName(false);
      toast.success(t('agents.displayNameSaved'));
    } catch (error) {
      const raw = error instanceof Error ? error.message : '';
      const detail = raw.match(/"message"\s*:\s*"([^"]+)"/)?.[1];
      toast.error(detail || t('agents.displayNameSaveFailed'));
    } finally {
      setSavingName(false);
    }
  }, [agent, nameDraft, refreshWorkspace, t]);

  const saveDescription = useCallback(async () => {
    if (!agent || !descriptionDirty) return;
    setSavingDescription(true);
    try {
      await workspaceApi.updateMember(agent.agentName, { description });
      await refreshWorkspace();
      setDescriptionDirty(false);
      toast.success(t('agents.descriptionSaved'));
    } catch {
      toast.error(t('agents.descriptionSaveFailed'));
    } finally {
      setSavingDescription(false);
    }
  }, [agent, description, descriptionDirty, refreshWorkspace, t]);

  const removeAgent = useCallback(async () => {
    if (!agent || agent.builtin) return;
    const approved = await confirm({
      title: t('agents.removeAgentTitle', { agent: agent.agentName }),
      description: t('agents.removeAgentBody'),
      confirmText: t('agents.remove'),
      destructive: true,
    });
    if (!approved) return;
    try {
      await workspaceApi.removeMember(agent.agentName);
      toast.success(t('agents.removed', { agent: agent.agentName }));
      setSelectedAgentName(null);
      await refreshWorkspace();
    } catch {
      toast.error(t('agents.removeFailed'));
    }
  }, [agent, confirm, refreshWorkspace, setSelectedAgentName, t]);

  const startThread = useCallback(async () => {
    if (!agent) return;
    if (agent.builtin) setCurrentSessionId(PAI_PRIMARY_CONVERSATION_ID);
    else await createSession({ master: agent.agentName, participants: [agent.agentName] });
    setSelectedAgentName(null);
    setViewMode('threads');
    if (isMobile) openMobileDetail();
  }, [agent, createSession, isMobile, openMobileDetail, setCurrentSessionId, setSelectedAgentName, setViewMode]);

  if (!agent) return null;
  const isSystem = Boolean(agent.builtin);
  const isOnline = agent.status === 'online';
  const displayType = isSystem
    ? 'Built-in / System Agent'
    : agent.agentType
      ? agent.agentType.charAt(0).toUpperCase() + agent.agentType.slice(1)
      : t('common.unknown');
  const infoItems = isSystem
    ? [
        { icon: <Cloud className="size-3.5" />, label: 'Role', value: "Placement AI's primary education counselor" },
        { icon: <Sparkles className="size-3.5" />, label: 'Capabilities', value: 'Education planning, university guidance, application guidance, and future specialist coordination' },
      ]
    : [
        { icon: <Monitor className="size-3.5" />, label: t('agents.fieldType'), value: displayType },
        { icon: <UserRoundCog className="size-3.5" />, label: t('agents.fieldAgentId'), value: `openagents:${agent.agentName}`, copyable: true },
      ];

  return (
    <>
      {!docked && <div className="absolute inset-0 z-10 bg-black/10" onClick={() => setSelectedAgentName(null)} />}
      <div className={cn(
        'flex h-full flex-col border-l bg-background animate-in slide-in-from-right duration-200',
        docked ? 'relative w-[320px] shrink-0' : cn('absolute inset-y-0 right-0 z-20 shadow-xl', isMobile ? 'left-0 w-full' : 'w-[320px]'),
      )}>
        <div className="flex items-center justify-end px-3 pt-3">
          <button onClick={() => setSelectedAgentName(null)} className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-zinc-200/60 dark:hover:bg-zinc-800" title={t('common.close')}>
            <X className="size-4" />
          </button>
        </div>

        <div className="px-5 pb-4">
          <div className="flex items-center gap-3">
            <AgentAvatar name={agent.agentName} size={40} status={agent.status} showStatus />
            <div className="min-w-0 flex-1">
              {editingName ? (
                <div className="flex items-center gap-1.5">
                  <input
                    value={nameDraft}
                    onChange={(event) => setNameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.nativeEvent.isComposing) return;
                      if (event.key === 'Enter') void saveDisplayName();
                      if (event.key === 'Escape') setEditingName(false);
                    }}
                    placeholder={agent.agentName}
                    maxLength={64}
                    className="min-w-0 flex-1 rounded border bg-transparent px-1.5 py-0.5 text-[15px] font-semibold outline-none focus:ring-1 focus:ring-foreground/20"
                    autoFocus
                  />
                  <button onClick={() => void saveDisplayName()} disabled={savingName} className="rounded bg-primary px-2 py-1 text-[11px] font-medium text-primary-foreground disabled:opacity-50">
                    {savingName ? t('common.saving') : t('common.save')}
                  </button>
                </div>
              ) : (
                <div className="group/name flex min-w-0 items-center gap-1.5">
                  <h3 className="truncate text-[15px] font-semibold">{agentLabel(agent)}</h3>
                  {!isSystem && <button onClick={() => setEditingName(true)} className="text-muted-foreground opacity-0 hover:text-foreground group-hover/name:opacity-100" title={t('agents.editDisplayName')}><Pencil className="size-3" /></button>}
                </div>
              )}
              {!isSystem && agent.displayName && agent.displayName !== agent.agentName && <p className="mt-0.5 truncate text-[11px] text-muted-foreground">@{agent.agentName}</p>}
              <div className="mt-1 flex items-center gap-1.5">
                {isSystem && <span className="rounded bg-primary/10 px-1.5 py-px text-[11px] font-medium text-primary">Placement AI</span>}
                <span className={cn('inline-flex items-center gap-1 rounded px-1.5 py-px text-[11px] font-medium', isOnline ? 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400')}>
                  <span className={cn('size-1.5 rounded-full', isOnline ? 'bg-green-500' : 'bg-zinc-400')} />{agent.status}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto px-3.5">
          {!isSystem && (
            <div className="overflow-hidden rounded-lg border">
              <div className="border-b px-3.5 py-2.5 text-xs font-medium">{t('agents.description')}</div>
              <div className="p-3">
                <textarea
                  className="min-h-[60px] w-full resize-none bg-transparent text-[13px] leading-relaxed outline-none"
                  placeholder={t('agents.descriptionPlaceholder', { agent: agent.agentName })}
                  value={description}
                  onChange={(event) => { setDescription(event.target.value); setDescriptionDirty(true); }}
                  onBlur={() => void saveDescription()}
                  rows={3}
                />
                {descriptionDirty && <div className="mt-1.5 flex justify-end"><button onClick={() => void saveDescription()} disabled={savingDescription} className="rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground disabled:opacity-50">{savingDescription ? t('common.saving') : t('common.save')}</button></div>}
              </div>
            </div>
          )}

          <div className="overflow-hidden rounded-lg border">
            <div className="border-b px-3.5 py-2.5 text-xs font-medium">{isSystem ? 'About PAI Counselor' : t('agents.connectionDetails')}</div>
            <div className="divide-y">
              {infoItems.map((item) => (
                <div key={item.label} className="flex items-start gap-3 px-3.5 py-3">
                  <div className="flex w-[80px] shrink-0 items-center gap-1.5 pt-px text-muted-foreground">{item.icon}<span className="text-xs">{item.label}</span></div>
                  <div className="flex min-w-0 flex-1 items-start gap-1">
                    <span className={cn('break-all text-[13px] leading-snug', isSystem ? 'font-medium' : 'font-mono')}>{item.value}</span>
                    {'copyable' in item && item.copyable && <button className="flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-zinc-100 dark:hover:bg-zinc-800" title={t('agents.copyField', { field: item.label })} onClick={() => copyToClipboard(item.value)}>{isCopied ? <Check className="size-3" /> : <Copy className="size-3" />}</button>}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {!isSystem && (
            <div className="overflow-hidden rounded-lg border border-red-200 dark:border-red-900/50">
              <div className="border-b border-red-200 px-3.5 py-2.5 text-xs font-medium text-red-600 dark:border-red-900/50 dark:text-red-400">{t('agents.dangerZone')}</div>
              <div className="flex items-center justify-between gap-3 p-3">
                <p className="text-[11px] text-muted-foreground">{t('agents.removeAgentHint')}</p>
                <button onClick={() => void removeAgent()} className="flex shrink-0 items-center gap-1.5 rounded-lg border border-red-200 px-3 py-2 text-xs font-medium text-red-600 hover:bg-red-50 dark:border-red-900/50 dark:text-red-400"><Trash2 className="size-3.5" />{t('agents.remove')}</button>
              </div>
            </div>
          )}
        </div>

        <div className="border-t px-3.5 py-3">
          <button onClick={() => void startThread()} className="flex w-full items-center justify-center gap-1.5 rounded-lg border bg-background px-3 py-2 text-xs font-medium hover:bg-zinc-50 dark:hover:bg-zinc-800"><Plus className="size-3" />{isSystem ? 'Open conversation' : t('agents.startThread')}</button>
        </div>
      </div>
    </>
  );
}
