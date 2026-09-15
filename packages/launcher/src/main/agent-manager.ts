import path from "path"
import fs from "fs"
import { EventEmitter } from "events"
import {
  CONFIG_DIR,
  DAEMON_LOG_FILE,
  GLOBAL_CORE,
  LAUNCHER_SESSIONS_DIR,
  ensureDir,
} from "./agents/paths"
import { loadCore, readCoreVersion } from "./agents/runtime"
import { deriveModelFromEnv } from "../shared/agent-model"
import {
  normalizeEnvForSave,
  normalizeWorkspaceEndpoint,
} from "./agents/env-normalize"
import { clearLogsInRange as clearDaemonLogsInRange } from "./agents/daemon-logs"
import {
  appendDaemonLog,
  getLiveDaemonPid,
  readDaemonState,
  startDaemon,
} from "./agents/daemon-process"
import { ChatService } from "./chat/service"
import type {
  ChatMessage,
  ChatSessionMeta,
  SendMessageInput,
  SendMessageResult,
  WorkspaceConfig,
  WorkspaceChatClient,
} from "./chat/types"

interface LauncherSettingsStore {
  get(key?: string): unknown
}

// The chat types now live with the code that owns them, but the
// IPC layer still imports them from here.
export type {
  ChatAttachment,
  ChatMessage,
  ChatSessionMeta,
  ChatStreamEvent,
  ChatToolCall,
  SendMessageInput,
  SendMessageResult,
} from "./chat/types"
export { extractMentions } from "./chat/messages"

let core: Record<string, unknown> | null = loadCore()

/**
 * Thin wrapper around the `@openagents-org/agent-launcher` connector: local
 * agent instances (add/remove/rename/env), the background daemon that hosts
 * them, workspace membership, and workspace chat. Placement AI does not
 * install, catalog or authenticate third-party coding CLIs — that whole
 * surface (catalog, install/uninstall, per-type health, CLI sign-in, model
 * lists, credential import) was removed together with the matching API on
 * the connector. What remains here is generic: any locally-named agent, run
 * by the daemon, optionally joined to a workspace.
 */
export class AgentManager extends EventEmitter {
  private _store: LauncherSettingsStore
  private _agentsCache: { value: unknown[]; at: number } = { value: [], at: 0 }
  private _statusCache: { value: unknown; at: number } = { value: {}, at: 0 }
  _connector: Record<string, unknown> | null = null

  /** Workspace chat: send, poll, sessions, files. */
  private _chat: ChatService

  constructor(store: LauncherSettingsStore) {
    super()
    this._store = store
    if (!core) core = loadCore()
    if (core) {
      this._connector = this.createConnector()
    }
    ensureDir(LAUNCHER_SESSIONS_DIR)

    this._chat = new ChatService({
      getClient: () => this._getWorkspaceClient(),
      resolveWorkspace: (workspaceId) =>
        this._resolveChatWorkspace(workspaceId),
      emit: (event) => {
        this.emit("chat-event", event)
      },
    })
  }

  private createConnector(): Record<string, unknown> {
    const AgentConnector = (core as Record<string, unknown>)
      .AgentConnector as new (opts: unknown) => Record<string, unknown>
    const workspaceEndpoint = normalizeWorkspaceEndpoint(
      this._store.get("workspaceEndpoint"),
    )
    return new AgentConnector({
      configDir: CONFIG_DIR,
      ...(workspaceEndpoint ? { workspaceEndpoint } : {}),
    })
  }

  private configuredWorkspaceEndpoint(): string | undefined {
    return normalizeWorkspaceEndpoint(this._store.get("workspaceEndpoint"))
  }

  getCoreInfo(): unknown {
    return {
      version: this.coreVersion,
      globalCorePath: GLOBAL_CORE,
      globalCorePresent: fs.existsSync(path.join(GLOBAL_CORE, "package.json")),
    }
  }

  reloadCore(): boolean {
    const cacheKeys = Object.keys(require.cache).filter(
      (k) => k.includes("agent-launcher") || k.includes("agent-connector"),
    )
    for (const k of cacheKeys) delete require.cache[k]
    core = loadCore()
    if (core) {
      this._connector = this.createConnector()
    }
    this._agentsCache = { value: [], at: 0 }
    return !!core
  }

  get coreVersion(): string | null {
    return readCoreVersion()
  }

  private _ensureConnector(): void {
    if (!this._connector) {
      if (!this.reloadCore()) {
        throw new Error("Core library not installed.")
      }
    }
  }

  getAgents(): unknown[] {
    const now = Date.now()
    if (
      this._agentsCache.value.length > 0 &&
      now - this._agentsCache.at < 1500
    ) {
      return this._agentsCache.value
    }
    if (!this._connector) {
      try {
        this._ensureConnector()
      } catch {
        return []
      }
    }
    const listAgents = this._connector!.listAgents as () => unknown[]
    const agents = listAgents.call(this._connector)
    const status = this.getAllStatus() as Record<
      string,
      { state?: string; restarts?: number; last_error?: string }
    >

    // One read per type, not per agent: `getAgentEnv` hits the disk, and a
    // device with five agents of one type would otherwise read the same file
    // five times per list refresh.
    const typeEnvCache = new Map<string, Record<string, string>>()
    const typeEnv = (type: string): Record<string, string> => {
      const hit = typeEnvCache.get(type)
      if (hit) return hit
      let env: Record<string, string> = {}
      try {
        env = (this.getAgentEnv(type) as Record<string, string>) || {}
      } catch {
        env = {}
      }
      typeEnvCache.set(type, env)
      return env
    }
    const value = (agents as Array<Record<string, unknown>>).map((a) => {
      const type = (a.type as string) || "openclaw"
      const statusEntry = status[a.name as string]
      return {
        ...a,
        state: statusEntry?.state || "stopped",
        restarts: statusEntry?.restarts || 0,
        lastError: statusEntry?.last_error || null,
        // The model this agent runs on, resolved here because only the main
        // process holds both halves of the answer: the type-level env and
        // the per-instance env.
        model: deriveModelFromEnv({
          ...typeEnv(type),
          ...((a.env as Record<string, string>) || {}),
        }),
      }
    })
    this._agentsCache = { value, at: now }
    return value
  }

  async addAgent(agentConfig: {
    name: string
    type?: string
    path?: string
    env?: Record<string, string>
  }): Promise<unknown> {
    const name = agentConfig.name
    const type = agentConfig.type || "openclaw"

    const addAgent = this._connector!.addAgent as (opts: unknown) => void
    addAgent.call(this._connector, {
      name,
      type,
      role: "worker",
      path: agentConfig.path,
      env: agentConfig.env,
    })
    // Bust the 1.5s agents cache so the renderer's immediate post-mutation
    // refresh() returns fresh data instead of the stale pre-add list.
    this._agentsCache = { value: [], at: 0 }
    return { success: true, agent: agentConfig }
  }

  /**
   * Remove an agent, optionally dropping its workspace membership too.
   *
   * Order matters: the workspace call goes FIRST, so a network failure leaves
   * the agent intact locally and the user can retry. Removing locally first
   * would strand a member row nobody can reach any more — the very state this
   * option exists to prevent.
   *
   * `fromWorkspace` on a core too old to offer removeAgentFromWorkspace is
   * reported rather than silently skipped, so nobody is told the workspace was
   * cleaned up when it was not.
   */
  async removeAgent(
    name: string,
    opts?: { fromWorkspace?: boolean },
  ): Promise<unknown> {
    if (opts?.fromWorkspace) {
      const drop = this._connector!.removeAgentFromWorkspace as
        | ((n: string) => Promise<unknown>)
        | undefined
      if (typeof drop !== "function") {
        throw new Error(
          "This version of the agent core cannot remove a workspace membership. Update the core, or remove the agent from the workspace directly.",
        )
      }
      await drop.call(this._connector, name)
    }
    try {
      await this.stopAgent(name)
    } catch {}
    const removeAgent = this._connector!.removeAgent as (name: string) => void
    removeAgent.call(this._connector, name)
    // See addAgent: bust the cache so the deleted agent doesn't linger.
    this._agentsCache = { value: [], at: 0 }
    return { success: true }
  }

  /**
   * Set an agent's display label, which is pushed to its workspace when it has
   * one (the core does that half — see setAgentDisplayName). The agent's
   * `name` is its identity and is never touched.
   */
  async renameAgent(name: string, displayName: string): Promise<unknown> {
    const rename = this._connector!.setAgentDisplayName as
      | ((n: string, d: string) => Promise<unknown>)
      | undefined
    if (typeof rename !== "function") {
      throw new Error(
        "This version of the agent core cannot rename agents. Update the core to use this.",
      )
    }
    const result = await rename.call(this._connector, name, displayName)
    this._agentsCache = { value: [], at: 0 }
    return result ?? { success: true }
  }

  async updateAgent(
    name: string,
    updates: { env?: Record<string, string> },
  ): Promise<unknown> {
    if (updates.env) {
      const saveEnv = this._connector!.saveAgentInstanceEnv as (
        name: string,
        env: unknown,
      ) => void
      saveEnv.call(this._connector, name, normalizeEnvForSave(updates.env))
    }
    this._agentsCache = { value: [], at: 0 }
    return { success: true }
  }

  /**
   * Change an existing agent's working directory (its spawn cwd, stored as the
   * `path` field in daemon.yaml). The folder is created up-front — a missing
   * cwd makes the agent subprocess fail to spawn. The new cwd takes effect the
   * next time the agent starts; a running agent is asked to reload so the
   * daemon re-reads the config.
   *
   * The connector's published `config.updateAgent` is used directly here: the
   * top-level connector exposes no agent-path setter, and reimplementing the
   * daemon.yaml read-modify-write launcher-side would risk diverging from the
   * core's serializer.
   */
  async setAgentWorkingDir(name: string, dirPath: string): Promise<unknown> {
    const p = (dirPath || "").trim()
    if (!p) throw new Error("A working directory is required.")
    try {
      fs.mkdirSync(p, { recursive: true })
    } catch (e) {
      throw new Error(
        `Could not create the agent folder '${p}': ${(e as Error).message}`,
      )
    }
    const config = (this._connector as { config?: unknown } | null)?.config as
      { updateAgent?: (name: string, updates: unknown) => unknown } | undefined
    if (!config?.updateAgent) {
      throw new Error(
        "Updating the working directory isn't supported by the installed core. Please update, then try again.",
      )
    }
    config.updateAgent(name, { path: p })
    this._agentsCache = { value: [], at: 0 }
    // Nudge the daemon to re-read daemon.yaml so a running agent picks up the
    // new cwd on its next (re)start. Best-effort: a stopped daemon just means
    // the change applies whenever it next starts the agent.
    try {
      const sendCmd = this._connector!.sendDaemonCommand as (c: string) => void
      sendCmd?.call(this._connector, "reload")
    } catch {}
    return { success: true, path: p }
  }

  getAgentEnv(agentType: string): unknown {
    const getAgentEnv = this._connector!.getAgentEnv as (
      type: string,
    ) => unknown
    return getAgentEnv.call(this._connector, agentType)
  }

  getAgentInstanceEnv(agentName: string): unknown {
    const getInstanceEnv = this._connector!.getAgentInstanceEnv as (
      name: string,
    ) => unknown
    return getInstanceEnv.call(this._connector, agentName)
  }

  deleteAgentEnv(agentType: string): unknown {
    const deleteEnv = this._connector!.deleteAgentEnv as (
      type: string,
    ) => unknown
    return deleteEnv.call(this._connector, agentType)
  }

  saveAgentEnv(agentType: string, env: Record<string, string>): unknown {
    env = normalizeEnvForSave(env)
    const saveEnv = this._connector!.saveAgentEnv as (
      type: string,
      env: unknown,
    ) => void
    saveEnv.call(this._connector, agentType, env)

    // The agents list now reports each agent's model out of this file, so a
    // stale cache would keep showing the previous one for up to its lifetime.
    this._agentsCache = { value: [], at: 0 }
    this.signalReload()
    return { success: true }
  }

  saveAgentInstanceEnv(
    agentName: string,
    env: Record<string, string>,
  ): unknown {
    env = normalizeEnvForSave(env)
    const saveEnv = this._connector!.saveAgentInstanceEnv as (
      name: string,
      env: unknown,
    ) => void
    saveEnv.call(this._connector, agentName, env)
    this._agentsCache = { value: [], at: 0 }
    this.signalReload()
    return { success: true }
  }

  /** Daemon PID, or null when no daemon runs or the core isn't loaded yet. */
  getDaemonPid(): number | null {
    if (!this._connector) return null
    try {
      const getDaemonPid = this._connector.getDaemonPid as () => number | null
      return getDaemonPid.call(this._connector) || null
    } catch {
      return null
    }
  }

  signalReload(): void {
    const getDaemonPid = this._connector!.getDaemonPid as () => number | null
    const pid = getDaemonPid.call(this._connector)
    if (!pid) return

    if (process.platform === "win32") {
      const sendCmd = this._connector!.sendDaemonCommand as (
        cmd: string,
      ) => void
      sendCmd.call(this._connector, "reload")
    } else {
      try {
        process.kill(pid, "SIGHUP")
      } catch {}
    }
  }

  getNetworks(): unknown[] {
    const listWorkspaces = this._connector!.listWorkspaces as () => unknown[]
    return listWorkspaces.call(this._connector)
  }

  /** Bind an agent to an already registered workspace by slug or id. */
  async connectWorkspace(agentName: string, input: string): Promise<unknown> {
    const ref = (input || "").trim()
    if (!ref) throw new Error("Missing workspace")
    const known = (this.getNetworks() as Array<{ id?: string; slug?: string }>).find(
      (network) => network.slug === ref || network.id === ref,
    )
    if (!known) throw new Error(`WORKSPACE_NOT_REGISTERED: '${ref}' is not registered`)
    const connect = this._connector!.connectWorkspace as (name: string, slug: string) => void
    connect.call(this._connector, agentName, (known.slug || known.id) as string)
    this.signalReload()
    return { success: true }
  }

  /** Leave the remote workspace membership, then clear the local binding. */
  async disconnectWorkspace(agentName: string): Promise<unknown> {
    const leave = this._connector!.leaveWorkspace as
      | ((n: string) => Promise<unknown>)
      | undefined
    if (typeof leave === "function") {
      await leave.call(this._connector, agentName)
    } else {
      const disconnectWorkspace = this._connector!.disconnectWorkspace as (
        name: string,
      ) => void
      disconnectWorkspace.call(this._connector, agentName)
    }
    this._agentsCache = { value: [], at: 0 }
    this.signalReload()
    return { success: true }
  }

  /** Remove a registered workspace without any device-level association. */
  async removeWorkspace(
    slug: string,
    opts: { deleteRemote?: boolean } = {},
  ): Promise<{ success: boolean; unpaired: boolean; deleted: boolean; warning: string | null }> {
    this._ensureConnector()
    const networks = this.getNetworks() as Array<Record<string, unknown>>
    const network = networks.find((entry) => entry.slug === slug || entry.id === slug)
    if (!network) throw new Error("WORKSPACE_NOT_FOUND: no such workspace on this launcher")
    if (opts.deleteRemote) {
      const remove = this._connector!.removeWorkspace as (ref: string) => Promise<unknown>
      await remove.call(this._connector, slug)
    } else {
      this._removeNetworkAndUnbind(
        typeof network.slug === "string" ? network.slug : null,
        typeof network.id === "string" ? network.id : null,
      )
      this.signalReload()
    }
    this._statusCache = { value: {}, at: 0 }
    this._agentsCache = { value: [], at: 0 }
    return { success: true, unpaired: false, deleted: !!opts.deleteRemote, warning: null }
  }

  private _removeNetworkAndUnbind(
    slug: string | null,
    id: string | null,
  ): boolean {
    const key = slug || id
    if (!key) return false
    const config = this._connector!.config as {
      removeNetwork: (slug: string) => boolean
      load: () => { agents?: Array<Record<string, unknown>> }
      save: (cfg: unknown) => void
    }
    const removed = config.removeNetwork.call(config, key)
    const keys = new Set([slug, id].filter((k): k is string => !!k))
    try {
      const cfg = config.load()
      let dirty = false
      for (const agent of cfg.agents || []) {
        if (typeof agent.network === "string" && keys.has(agent.network)) {
          delete agent.network
          dirty = true
        }
      }
      if (dirty) config.save(cfg)
    } catch {
      // The entry itself is gone either way; a binding left behind surfaces as
      // "workspace credentials missing" rather than a silent join.
    }
    return removed
  }

  /** Rename the workspace on the server. */
  async renameWorkspace(
    workspaceId: string,
    name: string,
  ): Promise<{ id: string; slug: string; name: string }> {
    const trimmed = (name || "").trim()
    if (!trimmed) throw new Error("WORKSPACE_NAME_EMPTY: enter a name")
    this._ensureConnector()

    const ws = this._resolveChatWorkspace(workspaceId)
    if (!ws) throw new Error("WORKSPACE_NOT_FOUND: no such workspace locally")
    // The workspace token IS the credential the API checks. A network saved
    // without one (slug-only link) can be shown, but not renamed.
    if (!ws.token)
      throw new Error(
        "WORKSPACE_NO_TOKEN: this workspace has no saved token, so it can only be renamed on the web",
      )
    const client = this._getWorkspaceClient() as unknown as {
      endpoint?: string
    } | null
    const endpoint =
      ws.endpoint || client?.endpoint || this.configuredWorkspaceEndpoint()
    if (!endpoint)
      throw new Error("WORKSPACE_NO_ENDPOINT: unknown API endpoint")

    const res = await fetch(`${endpoint}/v1/workspaces/${ws.id}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-Workspace-Token": ws.token,
      },
      body: JSON.stringify({ name: trimmed }),
    })
    const body = (await res.json().catch(() => null)) as {
      message?: string
      data?: { name?: string }
    } | null
    if (!res.ok) throw new Error(body?.message || `HTTP ${res.status}`)
    const saved = body?.data?.name || trimmed

    // Mirror it into the local network record, or the list would keep showing
    // the old name until something else re-registered the workspace.
    // addNetwork is insert-only, so this is a read-modify-write.
    try {
      const config = this._connector!.config as {
        load: () => { networks?: Array<Record<string, unknown>> }
        save: (cfg: unknown) => void
      }
      const cfg = config.load()
      const entry = (cfg.networks || []).find(
        (n) => n.id === ws.id || n.slug === ws.slug,
      )
      if (entry) {
        entry.name = saved
        config.save(cfg)
        this.signalReload()
      }
    } catch {
      // The server is the source of truth; a stale local label is cosmetic.
    }

    return { id: ws.id, slug: ws.slug, name: saved }
  }

  async startAgent(name: string): Promise<unknown> {
    const ready = await this._ensureDaemon()
    if (!ready)
      throw new Error(
        "Daemon failed to start. Check the Logs page for details.",
      )
    const sendCmd = this._connector!.sendDaemonCommand as (cmd: string) => void
    sendCmd.call(this._connector, `start:${name}`)
    // Bust the 1s status cache so the next poll from the renderer sees the
    // daemon's freshly written 'starting' state instead of stale 'stopped'.
    this._statusCache = { value: {}, at: 0 }
    return { success: true, message: `Start command sent for ${name}` }
  }

  async stopAgent(name: string): Promise<unknown> {
    const pid = this._getLiveDaemonPid()
    if (!pid) return { success: true, message: "Daemon not running" }
    const sendCmd = this._connector!.sendDaemonCommand as (cmd: string) => void
    sendCmd.call(this._connector, `stop:${name}`)
    this._statusCache = { value: {}, at: 0 }
    return { success: true, message: `Stop command sent for ${name}` }
  }

  async startAll(): Promise<unknown> {
    const ready = await this._ensureDaemon()
    if (!ready)
      throw new Error(
        "Daemon failed to start. Check the Logs page for details.",
      )
    const sendCmd = this._connector!.sendDaemonCommand as (cmd: string) => void
    sendCmd.call(this._connector, "reload")
    return { success: true, message: "Start all command sent" }
  }

  async stopAll(): Promise<unknown> {
    const stopDaemon = this._connector!.stopDaemon as () => boolean
    const stopped = stopDaemon.call(this._connector)
    return {
      success: stopped,
      message: stopped ? "Daemon stopped" : "Daemon not running",
    }
  }

  async _ensureDaemon(): Promise<boolean> {
    const pid = this._getLiveDaemonPid()
    if (pid) return true

    const result = await this._startDaemon()
    if (!result.success) appendDaemonLog(result.message)
    return !!(result.success && result.pid)
  }

  getAllStatus(): unknown {
    const now = Date.now()
    if (this._statusCache.value && now - this._statusCache.at < 1000) {
      return this._statusCache.value
    }
    let value: unknown = {}
    if (this._getLiveDaemonPid()) {
      const getDaemonStatus = this._connector!.getDaemonStatus as () => unknown
      try {
        value = getDaemonStatus.call(this._connector)
      } catch {
        value = {}
      }
    }
    this._statusCache = { value, at: now }
    return value
  }

  getLogs(name: string, lines = 200): unknown {
    const getLogs = this._connector!.getLogs as (
      name: string,
      lines: number,
    ) => string[]
    const logLines = getLogs.call(this._connector, name, lines)
    return { lines: logLines }
  }

  tailLogs(name: string, lines = 200, offset = 0): unknown {
    const config = this._connector!.config as Record<string, unknown>
    const tailLogs = config.tailLogs as (opts: unknown) => unknown
    return tailLogs.call(config, { agent: name || undefined, lines, offset })
  }

  clearLogsInRange(
    start: string | number | Date,
    end: string | number | Date,
  ): unknown {
    return clearDaemonLogsInRange(DAEMON_LOG_FILE, start, end)
  }

  /**
   * Daemon liveness from the launcher's perspective, independent of whether
   * any agents are configured. Used by the sidebar status dot — relying on
   * agent state means "no agents" looks identical to "daemon dead", which
   * makes the launcher feel broken on first run / after every install
   * failure.
   */
  getDaemonState(): {
    state: "online" | "starting" | "offline"
    pid: number | null
  } {
    return readDaemonState(this._getLiveDaemonPid())
  }

  private _getLiveDaemonPid(): number | null {
    return getLiveDaemonPid(this._connector, () => {
      this._statusCache = { value: {}, at: 0 }
    })
  }

  private _startDaemon(): { success: boolean; pid?: number; message: string } {
    return startDaemon(this._connector)
  }

  // ─────────────────────────────────────────────────────────
  // Workspace chat (send / get / poll messages)
  // ─────────────────────────────────────────────────────────

  private _getWorkspaceClient(): WorkspaceChatClient | null {
    if (!this._connector) return null
    const ws = this._connector.workspace as Record<string, unknown> | undefined
    if (!ws) return null
    return ws as unknown as WorkspaceChatClient
  }

  private _resolveChatWorkspace(workspaceId: string): WorkspaceConfig | null {
    const list = this.getNetworks() as Array<Record<string, unknown>>
    const match = list.find(
      (w) => w.id === workspaceId || w.slug === workspaceId,
    )
    if (!match) return null
    return {
      id: (match.id as string) || (match.slug as string),
      slug: (match.slug as string) || (match.id as string),
      name: match.name as string | undefined,
      endpoint: match.endpoint as string | undefined,
      token: (match.token as string) || "",
    }
  }

  async sendChatMessage(input: SendMessageInput): Promise<SendMessageResult> {
    return this._chat.sendMessage(input)
  }

  async getChatMessages(
    workspaceId: string,
    channelName?: string,
    limit = 100,
  ): Promise<ChatMessage[]> {
    return this._chat.getMessages(workspaceId, channelName, limit)
  }

  async getWorkspaceMessages(
    workspaceId: string,
    limit = 200,
  ): Promise<ChatMessage[]> {
    return this._chat.getWorkspaceMessages(workspaceId, limit)
  }

  async listChatParticipants(
    workspaceId: string,
  ): Promise<Array<{ agentName: string; role: string; status: string }>> {
    return this._chat.listParticipants(workspaceId)
  }

  startChatPolling(
    workspaceId: string,
    channelName?: string,
  ): { key: string } | null {
    return this._chat.startPolling(workspaceId, channelName)
  }

  stopChatPolling(workspaceId: string, channelName?: string): void {
    this._chat.stopPolling(workspaceId, channelName)
  }

  setChatForeground(foreground: boolean): void {
    this._chat.setForeground(foreground)
  }

  stopAllChatPolling(): void {
    this._chat.stopAllPolling()
  }

  listChatSessions(workspaceId?: string): ChatSessionMeta[] {
    return this._chat.listSessions(workspaceId)
  }

  loadChatSession(
    workspaceId: string,
    channelName: string,
  ): ChatSessionMeta | null {
    return this._chat.loadSession(workspaceId, channelName)
  }

  deleteChatSession(workspaceId: string, channelName: string): boolean {
    return this._chat.deleteSession(workspaceId, channelName)
  }

  clearChatSessions(workspaceId?: string): number {
    return this._chat.clearSessions(workspaceId)
  }

  createChatSession(workspaceId: string): ChatSessionMeta {
    return this._chat.createSession(workspaceId)
  }

  async uploadChatFile(
    workspaceId: string,
    filename: string,
    contentBase64: string,
    opts: { contentType?: string; channelName?: string } = {},
  ): Promise<{
    success: boolean
    fileId?: string
    url?: string
    filename?: string
    error?: string
  }> {
    return this._chat.uploadFile(workspaceId, filename, contentBase64, opts)
  }

  async listChatFiles(
    workspaceId: string,
    opts: { limit?: number; offset?: number } = {},
  ): Promise<unknown> {
    return this._chat.listFiles(workspaceId, opts)
  }

  async readChatFile(
    workspaceId: string,
    fileId: string,
  ): Promise<{ success: boolean; contentBase64?: string; error?: string }> {
    return this._chat.readFile(workspaceId, fileId)
  }

  async deleteChatFile(
    workspaceId: string,
    fileId: string,
  ): Promise<{ success: boolean; error?: string }> {
    return this._chat.deleteFile(workspaceId, fileId)
  }
}
