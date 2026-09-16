'use client';

import { Users } from 'lucide-react';
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
} from '@/components/ui/sidebar';
import { useWorkspace } from '@/lib/workspace-context';
import { useT } from '@/lib/i18n';

/**
 * Who's connected to this workspace right now — real SSE-tracked presence,
 * deduped per person (the same person open on Web and Desktop at once shows
 * as one row, "you"). Today that's just the workspace's one student across
 * their own devices; the same list shows real other people the moment this
 * workspace ever has any. Never fabricated data — empty when nothing is
 * actually connected.
 */
export function NavPresence() {
  const { onlineUsers, currentUser } = useWorkspace();
  const t = useT();

  if (onlineUsers.length === 0) return null;

  return (
    <SidebarGroup>
      <SidebarGroupLabel>
        <Users className="me-1 size-3" />
        {t('nav.onlineWithCount', { count: onlineUsers.length })}
      </SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu className="gap-0.5">
          {onlineUsers.map((user) => {
            const label =
              user.id === currentUser.id ? t('nav.you', { name: user.name }) : user.name;
            return (
              <SidebarMenuItem key={user.id}>
                <div className="flex h-8 items-center gap-2 rounded-md px-2 text-sm">
                  <span className="size-2 shrink-0 rounded-full bg-emerald-500" />
                  <span className="min-w-0 truncate text-foreground">{label}</span>
                </div>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
