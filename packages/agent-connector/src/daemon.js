'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
// spawn() here is the WSL bridge from ./wsl: same signature as
// child_process.spawn, and a straight pass-through unless the resolved CLI
// lives on the other side of the Windows/WSL boundary.
const { spawn } = require('./wsl');
const os = require('os');
const { getEnhancedEnv, IS_WINDOWS } = require('./paths');

/**
 * Local daemon lifecycle manager.
 *
 * Tracks configured agent entries and their workspace bindings, writes status
 * to disk, processes commands from daemon.cmd (start/stop/restart/reload),
 * and handles graceful shutdown. It no longer spawns or supervises any
 * coding-CLI process — that per-agent-type dispatch (openclaw/claude/codex/…)
 * was removed along with the adapters; hosting local-capability MCP tools now
 * happens via the standalone `agn mcp-server` command (src/mcp-server.js).
 *
 * Compatible with the Python SDK's daemon — reads the same config files,
 * writes the same status format, and supports the same command protocol.
 */
class Daemon {
  constructor(config) {
    this.config = config;

    // State
    this._processes = {};     // agentName → { proc, state, restarts, startedAt, lastError }
    // agentName → local worker instance. Nothing populates this today — the
    // per-coding-tool adapters that used to run here are gone — but the slot
    // stays as the extension point for a future generic local worker (see
    // src/adapters/base.js), and stopAgent/restartAgent/_ensureAdapterCleared
    // already know how to wait for one to release cleanly.
    this._adapters = {};
    this._stoppedAgents = new Set();
    this._shuttingDown = false;
    this._statusInterval = null;
    this._cmdInterval = null;
    this._reloadInFlight = null;  // serialize concurrent _reload() calls
  }

  // ---------------------------------------------------------------------------
 // Public API
  // ---------------------------------------------------------------------------

  /**
   * Start all configured agents and block until shutdown.
   * Call this from the foreground daemon process.
   */
  async start() {
    const agents = this.config.getAgents();
    for (const agent of agents) {
      this._launchAgent(agent);
    }

    // Install signal handlers
    const shutdown = () => this.stop();
    process.on('SIGTERM', shutdown);
    process.on('SIGINT', shutdown);
    if (!IS_WINDOWS && process.on) {
      try { process.on('SIGHUP', () => this._reload()); } catch {}
    }

    // Crash guards. A rejected fire-and-forget promise, an EBADF from a double
    // fs.closeSync in a child-process callback, a throw inside a stream/'exit'
    // handler — must NEVER take down the whole daemon. Node ≥15 terminates the
    // process on an unhandled rejection by default, so without these the
    // daemon silently dies and the launcher shows "Daemon stopped". Log
    // loudly, keep supervising.
    if (!this._crashGuardsInstalled) {
      this._crashGuardsInstalled = true;
      process.on('unhandledRejection', (reason) => {
        const msg = reason && reason.stack ? reason.stack : String(reason);
        this._log(`UNHANDLED REJECTION (daemon kept alive): ${msg}`);
      });
      process.on('uncaughtException', (err) => {
        const msg = err && err.stack ? err.stack : String(err);
        this._log(`UNCAUGHT EXCEPTION (daemon kept alive): ${msg}`);
      });
    }

    // Write PID file
    this._writePid();

    // Periodic status (heavy: JSON serialize + write) every 5s.
    this._statusInterval = setInterval(() => {
      this._writeStatus();
    }, 5000);

    // Command file poll (cheap: existsSync on a tiny file) every 200ms so
    // start/stop/restart from the launcher feels responsive. With a 5s
    // combined interval, users saw up to 5s before the daemon even noticed
    // a Stop click — this was especially painful on Windows where there's
    // no SIGHUP shortcut and Stop landed near the end of a tick.
    this._cmdInterval = setInterval(() => {
      this._processCommands();
    }, 200);

    // Watch config file for hot-reload
    this._watchConfig();

    this._writeStatus();
    this._cachedAgentNames = new Set(agents.map(a => a.name));
    this._cachedAgentConfigs = {};
    for (const a of agents) this._cachedAgentConfigs[a.name] = this._agentConfigFingerprint(a);
    this._log(`Daemon started with ${agents.length} agent(s)`);

    // Block until shutdown
    await new Promise((resolve) => {
      this._shutdownResolve = resolve;
    });
  }

  /**
   * Gracefully stop all agents and exit.
   */
  async stop() {
    if (this._shuttingDown) return;
    this._shuttingDown = true;
    this._log('Shutting down...');

    if (this._statusInterval) clearInterval(this._statusInterval);
    if (this._cmdInterval) clearInterval(this._cmdInterval);
    if (this._configWatcher) { try { this._configWatcher.close(); } catch {} }

    // Kill all child processes
    const kills = Object.keys(this._processes).map((name) =>
      this._killAgent(name, 5000)
    );
    await Promise.all(kills);

    this._writeStatus();
    this._cleanupPid();
    this._log('Daemon stopped');

    if (this._shutdownResolve) this._shutdownResolve();
  }

  /**
   * Stop a single agent by name.
   */
  async stopAgent(agentName) {
    this._stoppedAgents.add(agentName);
    // Mark state as stopped immediately
    if (this._processes[agentName]) {
      this._processes[agentName].state = 'stopped';
    }
    this._writeStatus();
    // Stop the adapter directly if running
    if (this._adapters && this._adapters[agentName]) {
      this._adapters[agentName].stop();
    }
    await this._killAgent(agentName, 5000);
    // Wait for adapter loop to actually exit
    for (let i = 0; i < 10; i++) {
      if (!this._adapters || !this._adapters[agentName]) break;
      await new Promise(r => setTimeout(r, 500));
    }
    // Force-clear the adapter slot if it's still hanging. Without this,
    // a hung adapter.run() promise prevents the slot from ever being
    // released, and subsequent start/restart commands see "already running".
    if (this._adapters && this._adapters[agentName]) {
      this._log(`WARNING: ${agentName} adapter did not exit after stop — force-releasing slot`);
      try { this._adapters[agentName].stop(); } catch {}
      delete this._adapters[agentName];
    }
    this._writeStatus();
  }

  /**
   * Restart a single agent by name.
   */
  async restartAgent(agentName) {
    // Set state to 'starting' immediately so UI never sees 'stopped' during restart
    if (this._processes[agentName]) {
      this._processes[agentName].state = 'starting';
      this._writeStatus();
    }

    await this.stopAgent(agentName);

    // stopAgent only waits 5s for `_adapters[name]` to disappear, but
    // graceful adapter shutdown can take longer (control-poller cleanup,
    // disconnect, in-flight CLI subprocess kill). If the adapter is still
    // there when we reach _launchAgent, the duplicate-launch guard bails
    // and the agent stays stuck in 'stopped'. Wait up to 20s so the
    // launch sees a clean slate.
    for (let i = 0; i < 40; i++) {
      if (!this._adapters || !this._adapters[agentName]) break;
      await new Promise(r => setTimeout(r, 500));
    }

    this._stoppedAgents.delete(agentName);

    // Reload config in case it changed
    this.config.load();

    const agent = this.config.getAgent(agentName);
    if (agent) {
      this._launchAgent(agent);
      this._writeStatus();
    }
  }

  /**
   * Get current status of all agents.
   */
  getStatus() {
    const result = {};
    for (const [name, info] of Object.entries(this._processes)) {
      result[name] = {
        state: info.state,
        type: info.type || 'unknown',
        network: info.network || '(local)',
        restarts: info.restarts,
        started_at: info.startedAt || null,
        last_error: info.lastError || null,
        error_reason: info.errorReason || null,
      };
    }
    return result;
  }

  // ---------------------------------------------------------------------------
  // Daemonize — launch as background process
  // ---------------------------------------------------------------------------

  /**
   * Launch the daemon as a background process.
   * The parent process prints info and exits; the child runs `start()`.
   * @param {string[]} foregroundArgs - CLI args for the foreground child process
   */
  static daemonize(configDir, foregroundArgs, execPath) {
    const logFile = path.join(configDir, 'daemon.log');
    const pidFile = path.join(configDir, 'daemon.pid');
    const bin = execPath || process.execPath;

    fs.mkdirSync(configDir, { recursive: true });

    // Refuse to start if an existing daemon is already running.
    // Without this check, repeated `agn up` invocations would spawn
    // multiple daemons that each process the same message → duplicate
    // bot replies. Check BOTH the pid file and the status file (a live daemon
    // rewrites the latter every 5s with its own pid): a clobbered/stale pid
    // file must not let a duplicate slip past this guard.
    const existingPid = Daemon.runningDaemonPid(configDir);
    if (existingPid) {
      console.error(`Daemon already running (PID ${existingPid}).`);
      console.error(`Run 'agn down' first, or 'agn status' to check.`);
      process.exit(1);
    }
    // Stale pid file — clean up before spawning fresh
    if (Daemon._readPid(pidFile)) {
      try { fs.unlinkSync(pidFile); } catch {}
    }

    const logFd = fs.openSync(logFile, 'a');

    // Build env with enhanced PATH (ensures node/npm are findable)
    const env = getEnhancedEnv();
    // Ensure the directory containing the node binary is on PATH
    const nodeBinDir = path.dirname(bin);
    if (env.PATH && !env.PATH.includes(nodeBinDir)) {
      env.PATH = nodeBinDir + path.delimiter + env.PATH;
    }

    const opts = {
      detached: true,
      stdio: ['ignore', logFd, logFd],
      env,
      cwd: configDir,
    };
    if (IS_WINDOWS) opts.windowsHide = true;

    const proc = spawn(bin, foregroundArgs, opts);
    proc.unref();
    fs.writeFileSync(pidFile, String(proc.pid), 'utf-8');

    // Give child a moment to start before closing the log fd
    setTimeout(() => {
      try { fs.closeSync(logFd); } catch {}
    }, 1000);

    console.log(`Daemon started (PID ${proc.pid})`);
    console.log(`Logs: ${logFile}`);
    console.log('Stop: agn down');
  }

  /**
   * Stop a running daemon by reading PID file and sending signal.
   * @returns {boolean} true if stopped
   */
  static stopDaemon(configDir) {
    const pidFile = path.join(configDir, 'daemon.pid');
    const statusFile = path.join(configDir, 'daemon.status.json');

    // Resolve the REAL running daemon(s) from BOTH the pid file and the status
    // file. A live daemon rewrites the status file every 5s with its own pid,
    // so when the pid file is stale/clobbered the status file is the only
    // record of the actual process. The old code trusted only the pid file:
    // `agn down` would signal a dead pid, delete the files, report "Daemon
    // stopped", and leave the real daemon orphaned — which then blocked every
    // subsequent `agn up` ("already running") and kept its agents wedged.
    const pids = Daemon._liveDaemonPids(configDir);

    // SIGTERM every distinct live pid.
    for (const pid of pids) {
      try {
        if (IS_WINDOWS) {
          // Node maps the supported termination signals to TerminateProcess on
          // Windows. Avoid taskkill here: spawning it through cmd.exe can hang
          // or be denied in restricted/non-interactive launcher sessions.
          process.kill(pid, 'SIGKILL');
        } else {
          process.kill(pid, 'SIGTERM');
        }
      } catch {}
    }

    // Wait briefly, then SIGKILL any survivor that ignored SIGTERM (this is
    // what today's zombie required — a foreground daemon that didn't exit on
    // SIGTERM).
    for (const pid of pids) {
      let alive = Daemon._isAlive(pid);
      for (let i = 0; alive && i < 5; i++) {
        // Synchronous API, so use an in-process bounded wait instead of
        // launching ping/sleep subprocesses (which are brittle in GUI and
        // sandboxed environments).
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 500);
        alive = Daemon._isAlive(pid);
      }
      if (alive) {
        try {
          process.kill(pid, 'SIGKILL');
        } catch {}
      }
    }

    // Always clear BOTH sources of truth so a fresh `agn up` starts clean.
    try { fs.unlinkSync(pidFile); } catch {}
    try { fs.unlinkSync(statusFile); } catch {}

    return pids.length > 0;
  }

  /**
   * Read daemon PID, returning null if not running.
   */
  static readDaemonPid(configDir) {
    const pidFile = path.join(configDir, 'daemon.pid');
    const statusFile = path.join(configDir, 'daemon.status.json');
    const pid = Daemon._readPid(pidFile);
    if (pid && Daemon._isAlive(pid)) return pid;

    // pid file missing or stale — fall back to the status file so `agn status`
    // reports the SAME live daemon the `agn up` singleton guard detects.
    // Otherwise status says "not running" (dead pid file) while up refuses
    // ("already running", from the status file) — the exact contradiction that
    // hid today's orphaned daemon.
    const live = Daemon.runningDaemonPid(configDir);
    if (live) return live;

    Daemon._cleanupStaleDaemonFiles(pidFile, statusFile);
    return null;
  }

  // ---------------------------------------------------------------------------
  // Internal — agent launch
  // ---------------------------------------------------------------------------

  /** Resolve an agent's saved generic workspace credential. */
  _resolveAgentNetwork(ref) {
    const network = this.config.getNetworks().find((entry) => entry.slug === ref || entry.id === ref);
    if (!network) return null;
    return {
      id: network.id || null,
      slug: network.slug || ref,
      name: network.name || ref,
      endpoint: network.endpoint || null,
      token: network.token || null,
    };
  }


  /**
   * Register a configured agent. There is no per-coding-tool process to spawn
   * any more (see the adapters removal in this package's generic-ization
   * cleanup) — an agent entry is now just a named local record, optionally
   * bound to a workspace network, that the daemon reports status for. Actually
   * joining/heartbeating a workspace on its behalf is future work for a real
   * generic local worker (the extension point BaseAdapter/`this._adapters`
   * still reserve); this method deliberately does not invent one.
   */
  _launchAgent(agentCfg) {
    const name = agentCfg.name;
    const type = agentCfg.type || 'agent';

    this._stoppedAgents.delete(name);

    const info = {
      type,
      network: agentCfg.network || '(local)',
      state: 'starting',
      restarts: 0,
      startedAt: null,
      lastError: null,
      // Structured failure reason (health-status REASON) paired with lastError,
      // so downstream consumers can classify "why" without parsing the message.
      errorReason: null,
      proc: null,
    };
    this._processes[name] = info;

    const network = agentCfg.network
      ? this._resolveAgentNetwork(agentCfg.network)
      : null;

    if (network && !network.token) {
      // A registered workspace without its generic credential cannot join.
      info.state = 'error';
      info.network = agentCfg.network;
      info.lastError =
        'workspace credentials missing — register the workspace token again';
      this._writeStatus();
      this._log(
        `${name} cannot join '${agentCfg.network}': no saved workspace token`
      );
      return;
    }

    info.networkRef = agentCfg.network || null;
    info.credentialToken = network ? network.token : null;
    info.state = 'running';
    info.network = network ? network.slug : '(local)';
    info.startedAt = new Date().toISOString();
    this._writeStatus();
    this._log(`${name} registered${network ? ` (workspace: ${network.slug})` : ' (local only)'}`);
  }

  _agentConfigFingerprint(agentCfg) {
    return JSON.stringify({
      network: agentCfg.network || '',
      env: agentCfg.env || {},
    });
  }

  // ---------------------------------------------------------------------------
  // Internal — agent kill
  // ---------------------------------------------------------------------------

  async _killAgent(name, timeoutMs) {
    const info = this._processes[name];
    if (!info || !info.proc) {
      if (info) info.state = 'stopped';
      return;
    }

    const proc = info.proc;
    info.proc = null;

    // Try graceful termination
    try {
      if (IS_WINDOWS) {
        execSync(`taskkill /PID ${proc.pid}`, { stdio: 'ignore', timeout: 5000 });
      } else {
        proc.kill('SIGTERM');
      }
    } catch {}

    // Wait for exit
    const died = await Promise.race([
      new Promise((resolve) => proc.on('exit', () => resolve(true))),
      this._sleep(timeoutMs).then(() => false),
    ]);

    if (!died) {
      try {
        if (IS_WINDOWS) {
          execSync(`taskkill /F /PID ${proc.pid}`, { stdio: 'ignore', timeout: 5000 });
        } else {
          proc.kill('SIGKILL');
        }
      } catch {}
    }

    info.state = 'stopped';
  }

  // ---------------------------------------------------------------------------
  // Internal — status, commands, PID
  // ---------------------------------------------------------------------------

  _writeStatus() {
    try {
      const status = { agents: this.getStatus(), pid: process.pid };
      fs.writeFileSync(this.config.statusFile, JSON.stringify(status, null, 2), 'utf-8');
    } catch {}
  }

  _processCommands() {
    const cmdFile = this.config.cmdFile;
    try {
      if (!fs.existsSync(cmdFile)) return;
      const raw = fs.readFileSync(cmdFile, 'utf-8').trim();
      fs.unlinkSync(cmdFile);
      if (!raw) return;

      for (const line of raw.split('\n')) {
        const cmd = line.trim();
        if (cmd.startsWith('stop:')) {
          const agentName = cmd.slice(5).trim();
          this._log(`Command: stop ${agentName}`);
          this.stopAgent(agentName);
        } else if (cmd.startsWith('start:')) {
          const agentName = cmd.slice(6).trim();
          // 'start' must be idempotent. The launcher sends start:<name> right
          // after (re)spawning the daemon, but the daemon's own start() already
          // launched every configured agent. A blind restart here tears down
          // the just-joined workspace session and re-joins as the same agent;
          // the server revokes the first session and the agent then stops the
          // moment it next touches the workspace (e.g. the first user message →
          // "thinking..." status). Only (re)launch when it isn't running.
          const running =
            (this._adapters && this._adapters[agentName]) ||
            ['running', 'starting'].includes(
              this._processes[agentName] && this._processes[agentName].state
            );
          if (running) {
            this._log(`Command: start ${agentName} — already running, skipping`);
          } else {
            this._log(`Command: start ${agentName}`);
            this.restartAgent(agentName);
          }
        } else if (cmd.startsWith('restart:')) {
          const agentName = cmd.slice(8).trim();
          this._log(`Command: restart ${agentName}`);
          this.restartAgent(agentName);
        } else if (cmd === 'reload') {
          this._log('Command: reload');
          this._reload();
        }
      }
    } catch {}
  }

  _watchConfig() {
    try {
      let debounce = null;
      this._configWatcher = fs.watch(this.config.configFile, () => {
        if (debounce) clearTimeout(debounce);
        debounce = setTimeout(() => this._reload(), 1000);
      });
      this._configWatcher.on('error', () => {});
    } catch {}
  }

  async _reload() {
    // Serialize reloads. fs.watch, the 'reload' command, and SIGHUP can
    // all fire concurrently. Without a mutex, two _reload() calls in flight
    // may both observe the same stale `this._adapters[name]` entry between
    // stopAgent() and _launchAgent(), leaving a ghost adapter running
    // alongside the new one → duplicate bot replies per message.
    if (this._reloadInFlight) {
      // Wait for the in-flight reload to finish, then run once more
      // (the config may have changed again since it started).
      this._reloadInFlight = this._reloadInFlight.then(
        () => this._reloadUnsafe(),
        () => this._reloadUnsafe(),
      );
      return this._reloadInFlight;
    }
    this._reloadInFlight = this._reloadUnsafe().finally(() => {
      this._reloadInFlight = null;
    });
    return this._reloadInFlight;
  }

  async _reloadUnsafe() {
    this._log('Reloading config...');
    const oldNames = this._cachedAgentNames || new Set();
    const oldConfigs = this._cachedAgentConfigs || {};
    // Re-read config from disk
    this.config.load();
    const newAgents = this.config.getAgents();
    const newNames = new Set(newAgents.map(a => a.name));
    const newConfigs = {};
    for (const a of newAgents) newConfigs[a.name] = this._agentConfigFingerprint(a);

    // Stop removed agents
    for (const name of oldNames) {
      if (!newNames.has(name)) {
        await this.stopAgent(name);
        this._log(`Reload: stopped removed agent '${name}'`);
      }
    }

    // Start new agents or restart agents whose network changed
    for (const agent of newAgents) {
      if (!oldNames.has(agent.name)) {
        await this._ensureAdapterCleared(agent.name);
        this._launchAgent(agent);
        this._log(`Reload: started new agent '${agent.name}'`);
      } else if ((oldConfigs[agent.name] || '') !== newConfigs[agent.name]) {
        // Network or env config changed — restart agent
        await this.stopAgent(agent.name);
        this._stoppedAgents.delete(agent.name);
        await this._ensureAdapterCleared(agent.name);
        this._launchAgent(agent);
        this._log(`Reload: restarted '${agent.name}' (config changed)`);
      }
    }

    this._cachedAgentNames = newNames;
    this._cachedAgentConfigs = newConfigs;
    this._writeStatus();
  }

  /**
   * Wait until the old adapter (if any) has fully released its slot in
   * this._adapters before relaunching. stopAgent already waits up to 5s,
   * but on slow shutdowns that can be too short — and _launchAgent's
   * duplicate-check would then silently skip the relaunch, leaving the
   * OLD adapter running instead of starting the new one.
   */
  async _ensureAdapterCleared(name) {
    for (let i = 0; i < 20; i++) {
      if (!this._adapters || !this._adapters[name]) return;
      await this._sleep(500);
    }
    // Last resort: force-clear the slot so the new adapter can start.
    // The old adapter will exit on its next poll iteration since its
    // entry in _stoppedAgents triggers adapter.stop() via checkStop.
    if (this._adapters && this._adapters[name]) {
      this._log(`WARNING: adapter '${name}' did not clear after 10s — force-releasing slot to avoid duplicate`);
      try { this._adapters[name].stop(); } catch {}
      delete this._adapters[name];
    }
  }

  _writePid() {
    try {
      fs.writeFileSync(this.config.pidFile, String(process.pid), 'utf-8');
    } catch {}
  }

  _cleanupPid() {
    try { fs.unlinkSync(this.config.pidFile); } catch {}
    try { fs.unlinkSync(this.config.statusFile); } catch {}
  }

  _log(msg) {
    const ts = new Date().toISOString();
    const line = `${ts} INFO daemon: ${msg}`;
    try {
      fs.appendFileSync(this.config.logFile, line + '\n', 'utf-8');
      this._maybeRotateLog();
    } catch {}
    // Only log to console if stdout is a TTY (not redirected to log file)
    // to avoid duplicate lines when daemonized
    if (!this._shuttingDown && process.stdout.isTTY) {
      console.log(line);
    }
  }

  _maybeRotateLog() {
    // Rotate at 10MB, keep 1 backup
    const MAX_SIZE = 10 * 1024 * 1024;
    try {
      const stat = fs.statSync(this.config.logFile);
      if (stat.size > MAX_SIZE) {
        const backup = this.config.logFile + '.1';
        try { fs.unlinkSync(backup); } catch {}
        fs.renameSync(this.config.logFile, backup);
      }
    } catch {}
  }

  _sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // ---------------------------------------------------------------------------
  // Static helpers
  // ---------------------------------------------------------------------------

  static _readPid(pidFile) {
    try {
      if (!fs.existsSync(pidFile)) return null;
      const pid = parseInt(fs.readFileSync(pidFile, 'utf-8').trim(), 10);
      return isNaN(pid) ? null : pid;
    } catch {
      return null;
    }
  }

  static _cleanupStaleDaemonFiles(pidFile, statusFile) {
    Daemon._unlinkIfExists(pidFile);
    Daemon._unlinkIfExists(statusFile);
  }

  static _unlinkIfExists(filePath) {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  }

  static _isAlive(pid) {
    try {
      process.kill(pid, 0);
      return true;
    } catch (e) {
      // EPERM = process exists but cross-session on Windows
      if (e.code === 'EPERM') return true;
      return false;
    }
  }

  /**
   * Return the PID of another live daemon for this configDir, or null.
   *
   * Used to enforce the singleton even on the `up --foreground` path, which the
   * launcher spawns directly (bypassing daemonize's guard). Trusts BOTH the pid
   * file and the status file (a live daemon rewrites the latter every 5s with
   * its own pid), because the pid file gets emptied/clobbered under races. Uses
   * real process-liveness — not just file age — so a daemon that was just
   * stopped for a legitimate restart doesn't block the replacement.
   */
  static runningDaemonPid(configDir) {
    return Daemon._liveDaemonPids(configDir)[0] || null;
  }

  /**
   * Return all distinct live daemon PIDs for this configDir (never including
   * the calling process), gathered from BOTH the pid file and the status file.
   * The pid file is considered first so its PID sorts ahead of the status
   * file's. Used by the singleton guard, `agn status`, and `agn down` so every
   * command agrees on which process(es) are the daemon — the divergence that
   * let a stale pid file orphan a running daemon.
   */
  static _liveDaemonPids(configDir) {
    const self = process.pid;
    const pids = [];
    const consider = (pid) => {
      if (pid && pid !== self && Daemon._isAlive(pid) && !pids.includes(pid)) {
        pids.push(pid);
      }
    };

    consider(Daemon._readPid(path.join(configDir, 'daemon.pid')));

    try {
      const statusFile = path.join(configDir, 'daemon.status.json');
      const age = Date.now() - fs.statSync(statusFile).mtimeMs;
      // Bound the age so a long-dead daemon whose pid got reused by an
      // unrelated process can't masquerade as a live daemon.
      if (age < 30000) {
        const raw = JSON.parse(fs.readFileSync(statusFile, 'utf-8'));
        consider(raw && raw.pid);
      }
    } catch {}

    return pids;
  }
}

module.exports = { Daemon };
