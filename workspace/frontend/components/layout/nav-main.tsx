'use client';

import {
  FileText, Globe, Inbox, KanbanSquare, MessageSquare, Sparkles, Waypoints,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar';
import { useWorkspace } from '@/lib/workspace-context';
import { countFiles } from '@/components/files/file-utils';
import { useT } from '@/lib/i18n';
import { PaiSystemStatus } from './pai-system-status';
import { PAI_PRIMARY_CONVERSATION_ID } from '@/lib/primary-conversation';
import { useLayout, type ViewMode } from './layout-context';

interface NavItem {
  mode: ViewMode;
  label: string;
  icon: React.ReactNode;
  count?: number;
  /** Render the count as a red attention badge (something needs the user). */
  urgent?: boolean;
}

/** `onNavigate` lets the mobile drawer close itself once a view is picked. */
export function NavMain({ onNavigate }: { onNavigate?: () => void }) {
  const { viewMode, openView, setSelectedAgentName } = useLayout();
  const {
    sessions, files, browserTabs, tasks, workflows, unreadNotificationCount,
    currentSessionId, setCurrentSessionId,
  } = useWorkspace();
  const t = useT();

  const isOnboarding = sessions.length === 0;
  const isPaiCounselorActive = viewMode === 'threads' && currentSessionId === PAI_PRIMARY_CONVERSATION_ID;

  const openPaiCounselor = (): void => {
    setCurrentSessionId(PAI_PRIMARY_CONVERSATION_ID);
    openView('threads');
    setSelectedAgentName(null);
    onNavigate?.();
  };

  const items: NavItem[] = [
    isOnboarding
      ? { mode: 'threads', label: t('views.onboarding'), icon: <Sparkles /> }
      : {
          mode: 'threads',
          label: t('views.threads'),
          icon: <MessageSquare />,
          count: sessions.filter((s) => !s.sessionId.startsWith('routine:') && !s.sessionId.startsWith('task:')).length,
        },
    { mode: 'files', label: t('views.files'), icon: <FileText />, count: countFiles(files) },
    { mode: 'browser', label: t('views.browser'), icon: <Globe />, count: browserTabs.length },
    {
      mode: 'tasks',
      label: t('views.tasks'),
      icon: <KanbanSquare />,
      // Count only what demands the user: tasks blocked on their input.
      count: tasks.filter((task) => task.status === 'need_input').length,
      urgent: true,
    },
    {
      mode: 'workflows',
      label: t('views.workflows'),
      icon: <Waypoints />,
      count: workflows.length,
    },
    {
      mode: 'inbox',
      label: t('views.inbox'),
      icon: <Inbox />,
      count: unreadNotificationCount > 0 ? unreadNotificationCount : undefined,
    },
  ];

  return (
    <SidebarGroup>
      <SidebarGroupContent>
        <PaiSystemStatus onOpenCounselor={openPaiCounselor} isCounselorActive={isPaiCounselorActive} />
      </SidebarGroupContent>
      <SidebarGroupLabel>{t('nav.collaboration')}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu className="gap-0.25">
          {items.map((item) => (
            <SidebarMenuItem key={item.mode}>
              <SidebarMenuButton
                tooltip={item.label}
                isActive={viewMode === item.mode}
                onClick={() => {
                  openView(item.mode);
                  onNavigate?.();
                }}
              >
                {item.icon}
                <span>{item.label}</span>
              </SidebarMenuButton>
              {item.count !== undefined && item.count > 0 && (
                <SidebarMenuBadge className="group-data-[collapsible=icon]:hidden">
                  <Badge
                    variant={item.urgent || item.mode === 'inbox' ? 'destructive' : 'secondary'}
                    appearance="light"
                    size="sm"
                    shape="circle"
                    className="min-w-5 justify-center px-1.5 tabular-nums"
                  >
                    {item.count}
                  </Badge>
                </SidebarMenuBadge>
              )}
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
