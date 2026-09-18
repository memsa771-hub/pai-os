import { describe, expect, it } from 'vitest';
import { isPaiHostname } from './openagents-auth-context';

// Returning false here is not cosmetic: the app then renders the third-party
// "add a token to the URL" screen with no login button, so a student on that
// host cannot sign in at all. www.placement-ai.com used to land there.
describe('isPaiHostname', () => {
  it.each([
    'placement-ai.com',
    'www.placement-ai.com',
    'WWW.Placement-AI.com',
    'localhost',
    '127.0.0.1',
    '[::1]',
    'workspace',
  ])('recognises %s as our own deployment', (host) => {
    expect(isPaiHostname(host)).toBe(true);
  });

  it.each([
    // Placement AI is its own product: no OpenAgents host is a PAI auth origin.
    'workspace.openagents.org',
    'openagents.org',
    'evil.com',
    'placement-ai.com.evil.com',
    'notplacement-ai.com',
    'wwwplacement-ai.com',
    '',
  ])('treats %s as third-party', (host) => {
    expect(isPaiHostname(host)).toBe(false);
  });
});
