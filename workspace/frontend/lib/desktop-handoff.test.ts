import { describe, expect, it } from 'vitest';

import { parseDesktopHandoff } from './desktop-handoff';

describe('parseDesktopHandoff', () => {
  it('reads the loopback target the launcher put in the link', () => {
    expect(parseDesktopHandoff('?port=51234&state=abc')).toEqual({
      port: 51234,
      state: 'abc',
    });
  });

  it('survives the extra parameters the OAuth round trip adds', () => {
    expect(parseDesktopHandoff('?port=51234&state=abc&code=xyz')?.state).toBe('abc');
  });

  it('refuses a port outside the range a loopback listener can hold', () => {
    expect(parseDesktopHandoff('?port=80&state=abc')).toBeNull();
    expect(parseDesktopHandoff('?port=99999&state=abc')).toBeNull();
    expect(parseDesktopHandoff('?port=abc&state=abc')).toBeNull();
  });

  it('refuses a link with no state to check', () => {
    expect(parseDesktopHandoff('?port=51234')).toBeNull();
    expect(parseDesktopHandoff('?port=51234&state=')).toBeNull();
  });
});
