'use strict';

// Before ./index so the daemon and every `agn` subprocess inherit the same
// no-stray-console-window default on Windows. See win-console.js.
require('./win-console').installWindowsHideDefault();

const { AgentConnector, Daemon } = require('./index');

// ---------------------------------------------------------------------------
// Arg parsing
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const args = argv.slice(2);
  const flags = {};
  const allPositional = [];

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      if (eq > 0) {
        flags[a.slice(2, eq)] = a.slice(eq + 1);
      } else if (i + 1 < args.length && !args[i + 1].startsWith('--')) {
        flags[a.slice(2)] = args[i + 1];
        i++;
      } else {
        flags[a.slice(2)] = true;
      }
    } else {
      allPositional.push(a);
    }
  }

  const cmd = allPositional[0] || 'status';
  const positional = allPositional.slice(1);

  return { cmd, flags, positional };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getConnector(flags) {
  const opts = {};
  if (flags.config) opts.configDir = flags.config;
  if (flags.endpoint || process.env.OPENAGENTS_ENDPOINT) {
    opts.workspaceEndpoint = flags.endpoint || process.env.OPENAGENTS_ENDPOINT;
  }
  return new AgentConnector(opts);
}

function print(msg) { process.stdout.write(msg + '\n'); }

// ---------------------------------------------------------------------------
// Banner — the OpenAgents wordmark (figlet "Small Slant", pure ASCII so it
// renders on every console including legacy Windows codepages). Colored with
// a cyan→violet gradient only on a real TTY; pipes and NO_COLOR get plain
// text. 47 columns wide, so it fits any 80-column terminal.
// ---------------------------------------------------------------------------
const OA_LOGO = [
  '  ____                ___                __    ',
  ' / __ \\___  ___ ___  / _ |___ ____ ___  / /____',
  '/ /_/ / _ \\/ -_) _ \\/ __ / _ `/ -_) _ \\/ __(_-<',
  '\\____/ .__/\\__/_//_/_/ |_\\_, /\\__/_//_/\\__/___/',
  '    /_/                 /___/                  ',
];

function printBanner() {
  const useColor = process.stdout.isTTY && !process.env.NO_COLOR;
  // 256-color gradient, one shade per row: cyan → azure → blue → violet.
  const shades = [51, 45, 39, 63, 99];
  let version = '';
  try { version = require('../package.json').version; } catch {}
  print('');
  OA_LOGO.forEach((line, i) => {
    print(useColor ? `\x1b[38;5;${shades[i]}m${line}\x1b[0m` : line);
  });
  const tag = `  v${version} · local workspace connector`;
  print(useColor ? `\x1b[2m${tag}\x1b[0m` : tag);
  print('');
}

function table(rows, headers) {
  if (rows.length === 0) return;
  const widths = headers.map((h, i) =>
    Math.max(h.length, ...rows.map((r) => String(r[i] || '').length))
  );
  print(headers.map((h, i) => h.padEnd(widths[i])).join('  '));
  print(widths.map((w) => '-'.repeat(w)).join('  '));
  for (const row of rows) {
    print(row.map((c, i) => String(c || '').padEnd(widths[i])).join('  '));
  }
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

async function cmdUp(connector, flags) {
  if (flags.foreground) {
    // Run in foreground. daemonize() already guards its own child, but the
    // launcher spawns `up --foreground` DIRECTLY — bypassing that guard — so a
    // stale/empty pid file used to let it pile up dozens of daemons that each
    // join the workspace as the same agents (session wars, duplicate replies).
    // Enforce the singleton here using process-liveness from the pid OR status
    // file, so a clobbered pid file can't defeat it.
    const daemon = connector.createDaemon();
    const other = Daemon.runningDaemonPid(daemon.config.configDir);
    if (other) {
      print(`Daemon already running (PID ${other}); not starting another.`);
      return;
    }
    await daemon.start();
  } else {
    const pid = connector.getDaemonPid();
    if (pid) {
      print(`Daemon already running (PID ${pid})`);
      return;
    }
    // Daemonize
    const foregroundArgs = [process.argv[1], 'up', '--foreground'];
    if (flags.config) foregroundArgs.push('--config', flags.config);
    connector.startDaemon(foregroundArgs);
  }
}

async function cmdDown(connector) {
  const stopped = connector.stopDaemon();
  if (stopped) {
    print('Daemon stopped');
  } else {
    print('Daemon is not running');
  }
}

async function cmdRestart(connector, flags) {
  // Stop then start. stopDaemon() already SIGTERM/SIGKILLs and waits for the
  // process to die, but poll getDaemonPid() (which checks liveness) until the
  // pid clears so the singleton guard in cmdUp can't trip on a not-yet-gone
  // daemon and refuse to start.
  const stopped = connector.stopDaemon();
  print(stopped ? 'Daemon stopped' : 'Daemon was not running');
  for (let i = 0; i < 25 && connector.getDaemonPid(); i++) {
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  await cmdUp(connector, flags);
}

async function cmdStatus(connector) {
  const pid = connector.getDaemonPid();
  if (!pid) {
    print('Daemon is not running');
  } else {
    print(`Daemon running (PID ${pid})`);
  }

  const agents = connector.listAgents();
  if (agents.length === 0) {
    print('\nNo agents configured. Run: agn create <name> --type <type>');
    return;
  }

  const status = connector.getDaemonStatus();
  const rows = agents.map((a) => {
    const s = status[a.name] || {};
    const state = s.state || (pid ? 'stopped' : '-');
    const restarts = s.restarts || 0;
    return [a.name, a.type, state, a.network || '(local)', restarts > 0 ? `${restarts}` : ''];
  });
  print('');
  table(rows, ['NAME', 'TYPE', 'STATE', 'NETWORK', 'RESTARTS']);
}

async function cmdCreate(connector, flags, positional) {
  const name = positional[0];
  if (!name) { print('Usage: agn create <name> [--type <type>]'); return; }
  const type = flags.type || 'agent';
  const role = flags.role || 'worker';

  try {
    connector.addAgent({ name, type, role, path: flags.path || process.cwd() });

    // Signal daemon to pick up the new agent
    try { connector.sendDaemonCommand('reload'); } catch {}

    // Newly created agents are local-only until connected to a workspace.
    // Without a workspace connection they will not appear in the Workspace Dashboard.
    const created = connector.config.getAgent(name);
    if (created && !created.network) {
      print(`Created local agent: ${name} (type: ${type})`);
      print('');
      print('This agent is local-only and will not appear in Workspace Dashboard yet.');
      print('');
      print('To connect it to a Workspace, run:');
      print(`  agn connect ${name} <workspace-token>`);
    } else {
      print(`Agent '${name}' created (type: ${type})`);
    }
  } catch (e) {
    print(`Error: ${e.message}`);
    process.exitCode = 1;
  }
}

async function cmdRemove(connector, _flags, positional) {
  const name = positional[0];
  if (!name) { print('Usage: agn remove <name>'); return; }
  connector.removeAgent(name);
  try { connector.sendDaemonCommand('reload'); } catch {}
  print(`Agent '${name}' removed`);
}

async function cmdStart(connector, _flags, positional) {
  const name = positional[0];
  if (!name) { print('Usage: agn start <name>'); return; }
  // `start` must be idempotent — it ensures the agent is running, it does NOT
  // forcibly restart one that already is. Send `start:` (which the daemon
  // guards: relaunch only when not already running) rather than `restart:`.
  // A blind `restart:` tears down the workspace session the daemon already
  // joined on launch and re-joins as the same agent; the server revokes the
  // first session, so the agent stops the moment it next touches the workspace
  // (first user message → "thinking..." then silence). The launcher already
  // sends `start:`; this aligns the CLI with it.
  connector.sendDaemonCommand(`start:${name}`);
  print(`Sent start command for '${name}'`);
}

async function cmdStop(connector, _flags, positional) {
  const name = positional[0];
  if (!name) { print('Usage: agn stop <name>'); return; }
  connector.sendDaemonCommand(`stop:${name}`);
  print(`Sent stop command for '${name}'`);
}

async function cmdList(connector) {
  const agents = connector.listAgents();
  if (agents.length === 0) {
    print('No agents configured');
    return;
  }
  const rows = agents.map((a) => [
    a.name, a.type, a.role, a.network || '(local)',
  ]);
  table(rows, ['NAME', 'TYPE', 'ROLE', 'NETWORK']);
}

async function cmdConnect(connector, flags, positional) {
  const name = positional[0];
  // Token resolution order: positional arg / --token flag, then env vars.
  // OPENAGENTS_WORKSPACE_TOKEN is preferred; OA_WORKSPACE_TOKEN is supported
  // for compatibility with the existing mcp-server env var.
  const token = positional[1]
    || flags.token
    || process.env.OPENAGENTS_WORKSPACE_TOKEN
    || process.env.OA_WORKSPACE_TOKEN;

  if (!name) {
    print('Usage: agn connect <agent-name> <token>');
    print('       agn connect <agent-name> --workspace <slug>   (registered workspace)');
    process.exitCode = 1;
    return;
  }

  // Bind to an already registered workspace without another server round-trip.
  if (flags.workspace && typeof flags.workspace === 'string') {
    connectKnownWorkspace(connector, name, flags.workspace);
    return;
  }

  if (!token) {
    // No token supplied and none in the environment. Never prompt — keep
    // CI / non-interactive environments from hanging. Print a helpful error
    // explaining why the agent stays invisible and how to fix it.
    print('Workspace token is required.');
    print('Local-only agents do not appear in Workspace Dashboard until connected.');
    print('Run:');
    print(`  agn connect ${name} <workspace-token>`);
    process.exitCode = 1;
    return;
  }

  print(`Resolving workspace token...`);
  try {
    const info = await connector.resolveToken(token);
    const slug = info.slug || info.workspace_id;
    const wsName = info.name || slug;

    // Save network
    connector.config.addNetwork({
      id: info.workspace_id,
      slug,
      name: wsName,
      endpoint: info.endpoint || connector.workspace.endpoint,
      token,
    });

    // Connect agent
    connector.connectWorkspace(name, slug);
    print(`'${name}' connected to workspace '${wsName}'`);
    // Signal daemon reload
    const pid = connector.getDaemonPid();
    if (pid) {
      connector.sendDaemonCommand(`restart:${name}`);
      print('Daemon notified');
    } else {
      print('Daemon is not running. Run `agn up` to bring this agent online.');
    }
  } catch (e) {
    print(`Error: ${e.message}`);
    process.exitCode = 1;
  }
}

/**
 * Bind an agent to a workspace already known to this device, by slug or id.
 * Uses the registered networks in daemon.yaml and never touches the server.
 */
function connectKnownWorkspace(connector, name, ref) {
  const networks = connector.config.getNetworks() || [];
  const net = networks.find((n) => n.slug === ref || n.id === ref);

  if (!net) {
    const known = networks.map((n) => n.slug || n.id).filter(Boolean);
    print(`No workspace '${ref}' is known on this device.`);
    if (known.length) print(`Known workspaces: ${known.join(', ')}`);
    print('Register it first with `agn connect <agent-name> <workspace-token>`.');
    process.exitCode = 1;
    return;
  }

  const slug = net.slug || ref;
  connector.connectWorkspace(name, slug);
  print(`'${name}' connected to workspace '${net.name || slug}'`);

  const pid = connector.getDaemonPid();
  if (pid) {
    connector.sendDaemonCommand(`restart:${name}`);
    print('Daemon notified');
  } else {
    print('Daemon is not running. Run `agn up` to bring this agent online.');
  }
}

async function cmdDisconnect(connector, _flags, positional) {
  const name = positional[0];
  if (!name) { print('Usage: agn disconnect <agent-name>'); return; }
  connector.disconnectWorkspace(name);
  print(`'${name}' disconnected from workspace`);

  const pid = connector.getDaemonPid();
  if (pid) {
    connector.sendDaemonCommand(`restart:${name}`);
  }
}

async function cmdLogs(connector, flags, positional) {
  const agent = positional[0] || flags.agent;
  const lines = parseInt(flags.lines || flags.n || '50', 10);
  const logLines = connector.getLogs(agent, lines);
  for (const line of logLines) {
    if (line) print(line);
  }
}

async function cmdAutostart(connector, flags) {
  const autostart = require('./autostart');
  if (flags.disable) {
    autostart.disable();
    print('Autostart disabled.');
  } else {
    const result = autostart.enable(connector._config ? connector._config.configDir : require('path').join(require('os').homedir(), '.openagents'));
    print(`Autostart enabled.${result.path ? ` Config: ${result.path}` : ''}`);
  }
}

async function cmdWorkspace(connector, flags, positional) {
  const sub = positional[0] || 'list';
  const subArgs = positional.slice(1);

  switch (sub) {
    case 'create': {
      const name = subArgs[0] || flags.name || 'My Workspace';
      print(`Creating workspace '${name}'...`);
      try {
        const result = await connector.createWorkspace({ name });
        print(`Workspace created: ${result.name}`);
        print(`  Slug:  ${result.slug}`);
        print(`  Token: ${result.token}`);
        print(`  URL:   ${result.url}`);
      } catch (e) {
        print(`Error: ${e.message}`);
        process.exitCode = 1;
      }
      break;
    }

    case 'join': {
      const token = subArgs[0] || flags.token;
      if (!token) { print('Usage: agn workspace join <token>'); return; }
      try {
        const info = await connector.resolveToken(token);
        connector.config.addNetwork({
          id: info.workspace_id,
          slug: info.slug || info.workspace_id,
          name: info.name || info.slug,
          endpoint: info.endpoint || connector.workspace.endpoint,
          token,
        });
        print(`Joined workspace '${info.name || info.slug}'`);
      } catch (e) {
        print(`Error: ${e.message}`);
        process.exitCode = 1;
      }
      break;
    }

    case 'list':
    default: {
      const workspaces = connector.listWorkspaces();
      if (workspaces.length === 0) {
        print('No workspaces configured');
        return;
      }
      const rows = workspaces.map((w) => [w.slug, w.name, w.endpoint || '-']);
      table(rows, ['SLUG', 'NAME', 'ENDPOINT']);
      break;
    }
  }
}

async function cmdEnv(connector, flags, positional) {
  const type = positional[0];
  if (!type) { print('Usage: agn env <type> [--set KEY=VALUE]'); return; }

  const setVal = flags.set;
  if (setVal) {
    const eq = setVal.indexOf('=');
    if (eq < 1) { print('Usage: --set KEY=VALUE'); return; }
    const key = setVal.slice(0, eq);
    const val = setVal.slice(eq + 1);
    connector.saveAgentEnv(type, { [key]: val });
    try { connector.sendDaemonCommand('reload'); } catch {}
    print(`Saved ${key} for ${type}`);
    return;
  }

  // Show current env
  const env = connector.getAgentEnv(type);
  const entries = Object.entries(env);
  if (entries.length === 0) {
    print(`No env vars configured for ${type}`);
  } else {
    for (const [k, v] of entries) {
      print(`  ${k}: ${v}`);
    }
  }
}

async function cmdVersion() {
  const pkg = require('../package.json');
  print(`${pkg.name} v${pkg.version}`);
}

async function cmdUpdate(connector) {
  const { checkForUpdate, runUpdate, currentVersion } = require('./update-check');
  const info = await checkForUpdate();
  if (!info) {
    print('Could not reach the npm registry. Check your network.');
    process.exitCode = 1;
    return;
  }
  if (!info.isNewer) {
    print(`Already on the latest version (${currentVersion()}).`);
    return;
  }
  print(`Updating ${info.current} → ${info.latest}...`);
  const ok = runUpdate();
  if (!ok) {
    print('Update failed.');
    process.exitCode = 1;
    return;
  }
  print(`Updated to ${info.latest}.`);

  // Recycle a running daemon so the new build takes effect immediately. Without
  // this, a long-lived daemon keeps executing the OLD in-memory code (and misses
  // new features) until the next manual restart or reboot — the failure mode
  // that left a connected node showing offline after its launcher was upgraded.
  try {
    if (connector && connector.getDaemonPid()) {
      print('Restarting daemon to apply the update...');
      connector.stopDaemon();
      for (let i = 0; i < 25 && connector.getDaemonPid(); i++) {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      connector.startDaemon();
    }
  } catch (e) {
    print(`Note: could not restart the daemon automatically (${e.message}). Run 'agn restart'.`);
  }
}

async function cmdHelp() {
  printBanner();
  print(`Usage: agn <command> [options]

Commands:
  up [--foreground]           Start daemon (background by default)
  down                        Stop daemon
  restart [--foreground]      Restart daemon (down + up)
  status                      Show agent status
  list                        List configured agents
  create <name> [--type T]    Create a new agent
  remove <name>               Remove an agent
  start <name>                Start a single agent
  stop <name>                 Stop a single agent
  connect <agent> <token>     Connect agent to workspace (or --workspace <slug> for a paired one)
  disconnect <agent>          Disconnect agent from workspace
  env <type> [--set K=V]      View/set env vars for agent type
  autostart [--disable]       Enable/disable auto-start on login
  logs [agent] [--lines N]    View daemon logs
  workspace create [name]     Create a new workspace
  workspace join <token>      Join workspace with token
  workspace list              List configured workspaces
  mcp-server                  Start MCP server (stdio) for local-capability workspace tools
  update                      Upgrade to the latest npm release
  version                     Show version
  help                        Show this help

Options:
  --config <dir>              Config directory (default: ~/.openagents)
`);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const { cmd, flags, positional } = parseArgs(process.argv);

  if (cmd === 'help' || flags.help) { await cmdHelp(); return; }
  if (cmd === 'version' || flags.version) { await cmdVersion(); return; }

  // Check for a newer version and offer to install it. Skip for:
  //   - mcp-server: JSON-RPC subprocess spawned by a local tool
  //   - up --foreground: the backgrounded daemon child
  //   - update: already updating; avoid recursion
  const skipUpdateCheck =
    cmd === 'mcp-server' ||
    (cmd === 'up' && flags.foreground) ||
    cmd === 'update' ||
    flags['no-update-check'] ||
    process.env.OPENAGENTS_SKIP_UPDATE_CHECK === '1' ||
    (cmd === 'status' && process.argv.length <= 2 && process.stdin.isTTY);
  if (!skipUpdateCheck) {
    try {
      const { notifyAndMaybeUpdate } = require('./update-check');
      await notifyAndMaybeUpdate();
    } catch {}
  }

  const connector = getConnector(flags);

  const commands = {
    up: () => cmdUp(connector, flags),
    down: () => cmdDown(connector),
    restart: () => cmdRestart(connector, flags),
    status: () => cmdStatus(connector),
    list: () => cmdList(connector),
    create: () => cmdCreate(connector, flags, positional),
    remove: () => cmdRemove(connector, flags, positional),
    start: () => cmdStart(connector, flags, positional),
    stop: () => cmdStop(connector, flags, positional),
    connect: () => cmdConnect(connector, flags, positional),
    disconnect: () => cmdDisconnect(connector, flags, positional),
    logs: () => cmdLogs(connector, flags, positional),
    autostart: () => cmdAutostart(connector, flags),
    workspace: () => cmdWorkspace(connector, flags, positional),
    env: () => cmdEnv(connector, flags, positional),
    update: () => cmdUpdate(connector),
    'mcp-server': () => {
      const { runMcpServer } = require('./mcp-server');
      const workspaceId = flags['workspace-id'] || process.env.OPENAGENTS_WORKSPACE_ID;
      const channelName = flags['channel-name'] || process.env.OPENAGENTS_CHANNEL_NAME || 'general';
      const agentName = flags['agent-name'] || process.env.OPENAGENTS_AGENT_NAME || 'agent';
      const endpoint = flags.endpoint || process.env.OPENAGENTS_ENDPOINT || 'https://workspace-endpoint.openagents.org';
      const token = process.env.OA_WORKSPACE_TOKEN || '';
      if (!workspaceId || !token) {
        print('Error: --workspace-id required and OA_WORKSPACE_TOKEN env var must be set');
        process.exitCode = 1;
        return;
      }
      const disabledModules = new Set();
      if (flags['disable-files']) disabledModules.add('files');
      if (flags['disable-browser']) disabledModules.add('browser');
      // Without this the server still registers the knowledge tools, and in
      // execute mode --dangerously-skip-permissions makes the allowlist a
      // suggestion, not a boundary.
      if (flags['disable-knowledge']) disabledModules.add('knowledge');
      runMcpServer({ workspaceId, channelName, agentName, endpoint, token, disabledModules });
    },
  };

  const handler = commands[cmd];
  if (!handler) {
    print(`Unknown command: ${cmd}`);
    print('Run: agn help');
    process.exitCode = 1;
    return;
  }

  try {
    await handler();
  } catch (e) {
    print(`Error: ${e.message}`);
    process.exitCode = 1;
  }
}

main();
