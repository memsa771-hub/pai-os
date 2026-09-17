'use client';

import { createContext, useContext } from 'react';
import type { Workspace, WorkspaceMe } from '@/lib/types';

/**
 * Context for the /{workspaceId}/settings/* admin dashboard.
 *
 * Deliberately NOT the full WorkspaceProvider: the dashboard only needs the
 * workspace record and who the caller is — none of the SSE/polling/presence
 * machinery — so the settings layout resolves credentials itself, configures
 * the shared workspaceApi singleton, and provides this slim context instead.
 */
export interface AdminSettingsValue {
  /** The slug or id from the URL (what API paths are addressed by). */
  workspaceId: string;
  workspace: Workspace;
  me: WorkspaceMe;
  /** The resolved workspace (machine) token — '' when accessing via identity
   * bearer only. */
  token: string;
  /** Re-fetch the workspace record after a mutation. */
  refreshWorkspace: () => Promise<void>;
  /** Query string ('' or '?token=…') to append to intra-app links so a
   * token-link visitor keeps access while navigating. */
  query: string;
}

/** True when the caller may change workspace settings.
 *
 * A workspace has one human — its owner — so this is simply "is that person",
 * plus the machine token an agent authenticates with. No role hierarchy. */
export function canAdminister(me: WorkspaceMe | null): boolean {
  return Boolean(me?.isOwner || me?.tokenAccess);
}

export const AdminSettingsContext = createContext<AdminSettingsValue | null>(null);

export function useAdminSettings(): AdminSettingsValue {
  const ctx = useContext(AdminSettingsContext);
  if (!ctx) throw new Error('useAdminSettings must be used inside the settings layout');
  return ctx;
}
