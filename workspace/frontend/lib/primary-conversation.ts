import type { WorkspaceSession } from './types';

export const PAI_PRIMARY_CONVERSATION_ID = 'pai-counselor';

export function defaultWorkspaceConversation(
  sessions: WorkspaceSession[],
): WorkspaceSession | undefined {
  const primary = sessions.find(
    (session) =>
      session.sessionId === PAI_PRIMARY_CONVERSATION_ID &&
      session.status === 'active',
  );
  if (primary) return primary;

  const timestamp = (session: WorkspaceSession) =>
    session.lastEventAt ||
    (session.createdAt ? new Date(session.createdAt).getTime() : 0);
  return [...sessions]
    .filter(
      (session) =>
        session.status === 'active' &&
        !session.sessionId.startsWith('routine:'),
    )
    .sort((a, b) => timestamp(b) - timestamp(a))[0];
}
