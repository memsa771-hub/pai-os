'use strict';

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { Daemon } = require('../src/daemon');
const { Config } = require('../src/config');

let tmpDir;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ac-daemon-'));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe('Daemon', () => {
  it('creates with correct initial state', () => {
    const config = new Config(tmpDir);
    const daemon = new Daemon(config);

    assert.deepEqual(daemon.getStatus(), {});
    assert.equal(daemon._shuttingDown, false);
  });

  it('getStatus returns empty when no agents', () => {
    const config = new Config(tmpDir);
    const daemon = new Daemon(config);

    assert.deepEqual(daemon.getStatus(), {});
  });

  it('_writeStatus creates status file', () => {
    const config = new Config(tmpDir);
    const daemon = new Daemon(config);

    daemon._writeStatus();
    assert.ok(fs.existsSync(config.statusFile));

    const status = JSON.parse(fs.readFileSync(config.statusFile, 'utf-8'));
    assert.ok(status.agents);
    assert.equal(status.pid, process.pid);
  });

  it('_launchAgent registers a local-only agent as running', () => {
    const config = new Config(tmpDir);
    config.addAgent({ name: 'local-agent', type: 'agent', role: 'worker' });
    const daemon = new Daemon(config);

    daemon._launchAgent(config.getAgent('local-agent'));

    const status = daemon.getStatus()['local-agent'];
    assert.equal(status.state, 'running');
    assert.equal(status.network, '(local)');
  });

  it('_launchAgent errors when the bound workspace has no saved token', () => {
    const config = new Config(tmpDir);
    config.addNetwork({ id: 'w1', slug: 'ws1', name: 'WS' }); // no token
    config.addAgent({ name: 'bound-agent', type: 'agent', role: 'worker', network: 'ws1' });
    config.setAgentNetwork('bound-agent', 'ws1');
    const daemon = new Daemon(config);

    daemon._launchAgent(config.getAgent('bound-agent'));

    const status = daemon.getStatus()['bound-agent'];
    assert.equal(status.state, 'error');
    assert.match(status.last_error, /workspace credentials missing/);
  });

  it('_processCommands handles stop command', async () => {
    const config = new Config(tmpDir);
    const daemon = new Daemon(config);

    // Create a fake process entry
    daemon._processes['test-agent'] = {
      state: 'running', proc: null, restarts: 0,
      type: 'agent', network: '(local)',
    };

    // Write stop command
    fs.writeFileSync(config.cmdFile, 'stop:test-agent\n', 'utf-8');
    daemon._processCommands();

    assert.ok(daemon._stoppedAgents.has('test-agent'));
  });

  it('_processCommands parses restart command', () => {
    const config = new Config(tmpDir);
    config.addAgent({ name: 'r-agent', type: 'agent', role: 'worker' });
    const daemon = new Daemon(config);

    daemon._processes['r-agent'] = {
      state: 'running', proc: null, restarts: 0,
      type: 'agent', network: '(local)',
    };

    // Stub restartAgent to verify it gets called without spawning
    let restarted = null;
    daemon.restartAgent = async (name) => { restarted = name; };

    fs.writeFileSync(config.cmdFile, 'restart:r-agent\n', 'utf-8');
    daemon._processCommands();

    assert.equal(restarted, 'r-agent');
  });

  it('start command is idempotent — skips restart when already running', () => {
    const config = new Config(tmpDir);
    config.addAgent({ name: 's-agent', type: 'agent', role: 'worker' });
    const daemon = new Daemon(config);

    // Already running with a live local worker — a blind restart here would tear
    // down the joined workspace session and re-join, getting the first session
    // revoked (agent stops after "thinking..."). `start:` must NOT restart it.
    daemon._adapters['s-agent'] = { stop() {} };
    daemon._processes['s-agent'] = { state: 'running', proc: null, restarts: 0 };

    let restarted = null;
    daemon.restartAgent = async (name) => { restarted = name; };

    fs.writeFileSync(config.cmdFile, 'start:s-agent\n', 'utf-8');
    daemon._processCommands();

    assert.equal(restarted, null, 'start: must not restart an already-running agent');
  });

  it('start command launches the agent when it is not running', () => {
    const config = new Config(tmpDir);
    config.addAgent({ name: 's-agent', type: 'agent', role: 'worker' });
    const daemon = new Daemon(config);

    // No live worker and no live process → start: must (re)launch it.
    let restarted = null;
    daemon.restartAgent = async (name) => { restarted = name; };

    fs.writeFileSync(config.cmdFile, 'start:s-agent\n', 'utf-8');
    daemon._processCommands();

    assert.equal(restarted, 's-agent', 'start: must launch an agent that is not running');
  });

  it('readDaemonPid returns null when no pid file', () => {
    assert.equal(Daemon.readDaemonPid(tmpDir), null);
  });

  it('readDaemonPid reads valid pid', () => {
    fs.writeFileSync(path.join(tmpDir, 'daemon.pid'), String(process.pid), 'utf-8');
    assert.equal(Daemon.readDaemonPid(tmpDir), process.pid);
  });

  it('readDaemonPid removes stale pid and status files', () => {
    const pidFile = path.join(tmpDir, 'daemon.pid');
    const statusFile = path.join(tmpDir, 'daemon.status.json');

    fs.writeFileSync(pidFile, '99999999', 'utf-8');
    fs.writeFileSync(statusFile, '{"agents":{}}', 'utf-8');

    assert.equal(Daemon.readDaemonPid(tmpDir), null);
    assert.equal(fs.existsSync(pidFile), false);
    assert.equal(fs.existsSync(statusFile), false);
  });

  it('_reload is serialized (concurrent calls queue)', async () => {
    const config = new Config(tmpDir);
    const daemon = new Daemon(config);

    // Track how many times _reloadUnsafe actually runs concurrently vs. serially.
    const order = [];
    let inFlight = 0;
    let maxConcurrent = 0;
    daemon._reloadUnsafe = async () => {
      inFlight++;
      maxConcurrent = Math.max(maxConcurrent, inFlight);
      order.push('start');
      await new Promise((r) => setTimeout(r, 30));
      order.push('end');
      inFlight--;
    };

    // Fire 3 reloads concurrently; they should all run (each sees the config
    // might have changed) but never overlap.
    await Promise.all([daemon._reload(), daemon._reload(), daemon._reload()]);

    assert.equal(maxConcurrent, 1, '_reloadUnsafe must never run concurrently');
    // 3 start/end pairs, always alternating
    assert.equal(order.length, 6);
    for (let i = 0; i < order.length; i += 2) {
      assert.equal(order[i], 'start');
      assert.equal(order[i + 1], 'end');
    }
  });

  it('_ensureAdapterCleared force-releases stuck adapter', async () => {
    const config = new Config(tmpDir);
    const daemon = new Daemon(config);

    let stopped = false;
    daemon._adapters['stuck'] = {
      stop: () => { stopped = true; },
    };
    // Override _sleep to make the test fast (returns immediately)
    daemon._sleep = () => Promise.resolve();

    await daemon._ensureAdapterCleared('stuck');

    assert.equal(stopped, true, 'adapter.stop() must be called when slot is stuck');
    assert.equal(daemon._adapters['stuck'], undefined, 'stuck adapter slot must be cleared');
  });

  it('_ensureAdapterCleared returns quickly when slot is already free', async () => {
    const config = new Config(tmpDir);
    const daemon = new Daemon(config);

    // No adapter in the slot
    const t0 = Date.now();
    await daemon._ensureAdapterCleared('nonexistent');
    const elapsed = Date.now() - t0;

    assert.ok(elapsed < 100, `should return immediately, took ${elapsed}ms`);
  });
});

// Regression guard for the orphaned-daemon bug: a live daemon whose pid only
// survives in the status file (stale/clobbered pid file) must still be seen by
// `agn status` and killed by `agn down`, instead of being reported "stopped"
// and left running to block every subsequent `agn up`.
describe('Daemon pid resolution (dual-source)', () => {
  const { spawn } = require('node:child_process');

  // A child we can control: it ignores nothing, so SIGTERM terminates it.
  async function spawnDummy() {
    const proc = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1e9)'], { stdio: 'ignore' });
    // On Windows, taskkill can terminate a child before Node has delivered its
    // asynchronous `spawn` event. In that race the later `exit` listener may
    // never fire, leaving the regression test waiting forever even though the
    // process is already gone. Wait until the ChildProcess handle is live
    // before exercising the daemon's external-kill path.
    await new Promise((resolve, reject) => {
      proc.once('spawn', resolve);
      proc.once('error', reject);
    });
    return proc;
  }
  async function waitForDeath(pid, timeoutMs = 5000) {
    const deadline = Date.now() + timeoutMs;
    while (Daemon._isAlive(pid) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    assert.equal(Daemon._isAlive(pid), false, `process ${pid} should have exited`);
  }
  // Kill a child and wait until it is fully reaped, so process.kill(pid, 0)
  // reports ESRCH (a reaped pid is genuinely dead, not a zombie).
  function killAndReap(proc) {
    return new Promise((resolve) => {
      proc.once('exit', resolve);
      try { proc.kill('SIGKILL'); } catch { resolve(); }
    });
  }

  it('stopDaemon kills the live daemon from the status file when the pid file is stale', async () => {
    const dead = await spawnDummy();
    const deadPid = dead.pid;
    await killAndReap(dead); // deadPid is now a genuinely dead pid

    const live = await spawnDummy();
    const livePid = live.pid;
    fs.writeFileSync(path.join(tmpDir, 'daemon.pid'), String(deadPid), 'utf-8');
    fs.writeFileSync(
      path.join(tmpDir, 'daemon.status.json'),
      JSON.stringify({ pid: livePid, agents: {} }),
      'utf-8',
    );

    try {
      const result = Daemon.stopDaemon(tmpDir);
      // An externally issued Windows taskkill can race Node's ChildProcess
      // event bookkeeping. The OS liveness check is the behavior under test;
      // waiting solely for `exit` can hang after the process is already dead.
      live.unref();
      await waitForDeath(livePid);
      assert.equal(result, true);
      assert.equal(Daemon._isAlive(livePid), false, 'live daemon should have been killed');
      assert.ok(!fs.existsSync(path.join(tmpDir, 'daemon.pid')), 'pid file should be cleaned');
      assert.ok(!fs.existsSync(path.join(tmpDir, 'daemon.status.json')), 'status file should be cleaned');
    } finally {
      if (Daemon._isAlive(livePid)) await killAndReap(live);
    }
  });

  it('readDaemonPid falls back to the status file when the pid file is stale', async () => {
    const dead = await spawnDummy();
    const deadPid = dead.pid;
    await killAndReap(dead);

    const live = await spawnDummy();
    const livePid = live.pid;

    fs.writeFileSync(path.join(tmpDir, 'daemon.pid'), String(deadPid), 'utf-8');
    fs.writeFileSync(
      path.join(tmpDir, 'daemon.status.json'),
      JSON.stringify({ pid: livePid, agents: {} }),
      'utf-8',
    );

    try {
      assert.equal(Daemon.readDaemonPid(tmpDir), livePid);
    } finally {
      await killAndReap(live);
    }
  });

  it('readDaemonPid returns null and cleans up when nothing is alive', async () => {
    const dead = await spawnDummy();
    const deadPid = dead.pid;
    await killAndReap(dead);

    fs.writeFileSync(path.join(tmpDir, 'daemon.pid'), String(deadPid), 'utf-8');
    assert.equal(Daemon.readDaemonPid(tmpDir), null);
    assert.ok(!fs.existsSync(path.join(tmpDir, 'daemon.pid')), 'stale pid file should be cleaned');
  });
});
