'use client';

import {
  CircleUser, FileText, Globe, Inbox, KanbanSquare, MessageSquare, Waypoints,
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
import { AgentAvatar } from '@/components/agents/agent-avatar';
import { useWorkspace } from '@/lib/workspace-context';
import { countFiles } from '@/components/files/file-utils';
import { useT } from '@/lib/i18n';
import { PAI_PRIMARY_CONVERSATION_ID } from '@/lib/primary-conversation';
import { INBOX_UI_ENABLED, TASKS_UI_ENABLED, WORKFLOWS_UI_ENABLED } from '@/lib/config';
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

  const isPaiCounselorActive = viewMode === 'threads' && currentSessionId === PAI_PRIMARY_CONVERSATION_ID;

  const openPaiCounselor = (): void => {
    setCurrentSessionId(PAI_PRIMARY_CONVERSATION_ID);
    openView('threads');
    setSelectedAgentName(null);
    onNavigate?.();
  };

  const items: NavItem[] = [
    // Directly under PAI Counselor — see the matching note in nav-rail.
    { mode: 'profile', label: t('views.profile'), icon: <CircleUser /> },
    {
      mode: 'threads',
      label: t('views.threads'),
      icon: <MessageSquare />,
      count: sessions.filter((s) => !s.sessionId.startsWith('routine:') && !s.sessionId.startsWith('task:')).length,
    },
    { mode: 'files', label: t('views.files'), icon: <FileText />, count: countFiles(files) },
    { mode: 'browser', label: t('views.browser'), icon: <Globe />, count: browserTabs.length },
    ...(TASKS_UI_ENABLED
      ? [{
          mode: 'tasks' as const,
          label: t('views.tasks'),
          icon: <KanbanSquare />,
          // Count only what demands the user: tasks blocked on their input.
          count: tasks.filter((task) => task.status === 'need_input').length,
          urgent: true,
        }]
      : []),
    ...(WORKFLOWS_UI_ENABLED
      ? [{
          mode: 'workflows' as const,
          label: t('views.workflows'),
          icon: <Waypoints />,
          count: workflows.length,
        }]
      : []),
    ...(INBOX_UI_ENABLED
      ? [{
          mode: 'inbox' as const,
          label: t('views.inbox'),
          icon: <Inbox />,
          count: unreadNotificationCount > 0 ? unreadNotificationCount : undefined,
        }]
      : []),
  ];

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{t('nav.collaboration')}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu className="gap-0.25">
          <SidebarMenuItem>
            <SidebarMenuButton
              tooltip={t('views.paiCounselor')}
              isActive={isPaiCounselorActive}
              onClick={openPaiCounselor}
            >
              <AgentAvatar name="pai" size={16} className="[&_svg]:size-full!" />
              <span>{t('views.paiCounselor')}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroupContent>
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
