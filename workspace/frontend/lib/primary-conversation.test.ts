import { describe, expect, it } from 'vitest';
import type { WorkspaceSession } from './types';
import { defaultWorkspaceConversation } from './primary-conversation';

const session = (sessionId: string, lastEventAt: number): WorkspaceSession => ({
  sessionId,
  workspaceId: 'workspace-1',
  createdBy: sessionId === 'pai-counselor' ? 'pai' : 'human:user',
  title: sessionId,
  status: 'active',
  starred: false,
  participants: sessionId === 'pai-counselor' ? ['pai'] : [],
  master: sessionId === 'pai-counselor' ? 'pai' : null,
  orchestrationMode: 'dynamic',
  orchestrationInstruction: null,
  workflowId: null,
  createdAt: new Date(lastEventAt).toISOString(),
  lastEventAt,
});

describe('defaultWorkspaceConversation', () => {
  it('always prefers the canonical PAI conversation', () => {
    const pai = session('pai-counselor', 1);
    expect(defaultWorkspaceConversation([
      pai,
      session('newer-generic-thread', 100),
    ])).toBe(pai);
  });

  it('falls back to the newest generic thread when PAI is unavailable', () => {
    expect(defaultWorkspaceConversation([
      session('older', 1),
      session('newer', 2),
    ])?.sessionId).toBe('newer');
  });
});
