'use client';

import { createContext, useContext } from 'react';
import type { Workspace, WorkspaceMe } from '@/lib/types';

/** Data shared by the personal workspace settings pages. */
export interface WorkspaceSettingsValue {
  workspaceId: string;
  workspace: Workspace;
  /** Kept for authorization state, not for displaying a human role hierarchy. */
  me: WorkspaceMe;
  /** Machine credential for supported self-hosted/token access; empty for PAI accounts. */
  token: string;
  refreshWorkspace: () => Promise<void>;
  query: string;
}

/**
 * The hosted product admits the workspace owner; self-hosted installations may
 * also use their machine token. The backend remains the final authority.
 */
export function canManageWorkspace(me: WorkspaceMe | null): boolean {
  return Boolean(me?.isOwner || me?.tokenAccess);
}

export const WorkspaceSettingsContext = createContext<WorkspaceSettingsValue | null>(null);

export function useWorkspaceSettings(): WorkspaceSettingsValue {
  const ctx = useContext(WorkspaceSettingsContext);
  if (!ctx) throw new Error('useWorkspaceSettings must be used inside the settings layout');
  return ctx;
}
