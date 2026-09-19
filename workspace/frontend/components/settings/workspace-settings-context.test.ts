import { describe, expect, it } from 'vitest';
import { canManageWorkspace } from './workspace-settings-context';
import type { WorkspaceMe } from '@/lib/types';

function access(overrides: Partial<WorkspaceMe>): WorkspaceMe {
  return {
    email: null,
    displayName: null,
    authenticated: false,
    isOwner: false,
    tokenAccess: false,
    ...overrides,
  };
}

describe('canManageWorkspace', () => {
  it('allows the personal workspace account', () => {
    expect(canManageWorkspace(access({ authenticated: true, isOwner: true }))).toBe(true);
  });

  it('keeps supported machine-token access working', () => {
    expect(canManageWorkspace(access({ tokenAccess: true }))).toBe(true);
  });

  it('does not turn a merely authenticated account into workspace access', () => {
    expect(canManageWorkspace(access({ authenticated: true }))).toBe(false);
    expect(canManageWorkspace(null)).toBe(false);
  });
});
