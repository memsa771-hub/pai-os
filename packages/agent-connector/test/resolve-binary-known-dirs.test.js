const { test, describe } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { resolveBinaryInKnownDirs, IS_WINDOWS } = require('../src/paths');

/**
 * The launcher resolves agent binaries through installer.which(), which was a
 * PATH lookup and nothing else. On Windows the Cursor/Amp/Hermes installers
 * edit the *registry* PATH, which an already-running process never inherits —
 * so `where cursor-agent` comes back empty for a perfectly good install, the
 * launcher calls it missing, and its terminal fallback then runs a bare
 * `cursor-agent login` that dies with "is not recognized". Every adapter had
 * grown its own filesystem search to cope; the launcher had none.
 */
describe('resolveBinaryInKnownDirs', () => {
  test('finds a binary in the agent\'s isolated runtime bin dir', () => {
    const dir = path.join(os.homedir(), '.openagents', 'runtimes', '__probe__', 'node_modules', '.bin');
    const name = `oa-test-${process.pid}`;
    const file = path.join(dir, IS_WINDOWS ? `${name}.cmd` : name);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(file, '');
    try {
      assert.strictEqual(resolveBinaryInKnownDirs([name], '__probe__'), file);
    } finally {
      fs.rmSync(file, { force: true });
    }
  });

  test('tries every alias, not just the first', () => {
    const dir = path.join(os.homedir(), '.openagents', 'runtimes', '__probe__', 'node_modules', '.bin');
    const name = `oa-alias-${process.pid}`;
    const file = path.join(dir, IS_WINDOWS ? `${name}.cmd` : name);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(file, '');
    try {
      // Mirrors cursor: install.binary is "cursor-agent", the alias is "agent",
      // and on some layouts only the alias exists on disk.
      assert.strictEqual(resolveBinaryInKnownDirs(['oa-missing-name', name], '__probe__'), file);
    } finally {
      fs.rmSync(file, { force: true });
    }
  });

  test('returns null rather than a path that is not there', () => {
    assert.strictEqual(resolveBinaryInKnownDirs([`oa-absent-${process.pid}`], '__probe__'), null);
  });

  test('is not fooled by a directory sharing the binary name', () => {
    const dir = path.join(os.homedir(), '.openagents', 'runtimes', '__probe__', 'node_modules', '.bin');
    const name = `oa-dir-${process.pid}`;
    const asDir = path.join(dir, name);
    fs.mkdirSync(asDir, { recursive: true });
    try {
      assert.strictEqual(resolveBinaryInKnownDirs([name], '__probe__'), null);
    } finally {
      fs.rmSync(asDir, { recursive: true, force: true });
    }
  });

  test('handles empty and missing input without throwing', () => {
    assert.strictEqual(resolveBinaryInKnownDirs([], 'cursor'), null);
    assert.strictEqual(resolveBinaryInKnownDirs(null, 'cursor'), null);
    // A name nobody could have installed: the search now covers dirs that are
    // also on PATH, so a one-letter placeholder like "x" matches /opt/X11/bin/x
    // on a machine that has XQuartz — a real hit, and nothing to do with the
    // "no agentType given" case this asserts.
    assert.strictEqual(resolveBinaryInKnownDirs([`oa-absent-${process.pid}`], undefined), null);
  });
});
