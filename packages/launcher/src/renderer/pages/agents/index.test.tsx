import React from "react"
import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import Agents from "./index"
import { useAgentsStore } from "../../store/agents"
import { useUiStore } from "../../store/ui"
import type { Agent } from "../../types"

// Analytics fires network calls (posthog) — stub it out for the jsdom run.
vi.mock("../../lib/analytics", () => ({ capture: vi.fn(), group: vi.fn() }))

type Api = Record<string, ReturnType<typeof vi.fn>>

// A minimal window.api that resolves everything the Agents page touches.
// Individual tests override the pieces they care about.
function installApi(overrides: Partial<Api> = {}): Api {
  const api: Api = {
    listAgents: vi.fn().mockResolvedValue([]),
    agentStatus: vi.fn().mockResolvedValue({}),
    startAgent: vi.fn().mockResolvedValue(undefined),
    stopAgent: vi.fn().mockResolvedValue(undefined),
    removeAgent: vi.fn().mockResolvedValue(undefined),
    addAgent: vi.fn().mockResolvedValue(undefined),
    setAgentWorkingDir: vi.fn().mockResolvedValue({ success: true }),
    getAgentEnv: vi.fn().mockResolvedValue({}),
    getAgentInstanceEnv: vi.fn().mockResolvedValue({}),
    saveAgentInstanceEnv: vi.fn().mockResolvedValue(undefined),
    saveAgentEnv: vi.fn().mockResolvedValue(undefined),
    listWorkspaces: vi.fn().mockResolvedValue([]),
    connectWorkspace: vi.fn().mockResolvedValue(undefined),
    disconnectWorkspace: vi.fn().mockResolvedValue(undefined),
    signalReload: vi.fn().mockResolvedValue(undefined),
    openExternal: vi.fn(),
    // ManageAgentDialog prefills the working folder from the OS home dir and
    // lets the user browse for one.
    listPaths: vi.fn().mockResolvedValue({ home: "/home/test" }),
    selectDirectory: vi.fn().mockResolvedValue(null),
    ...overrides,
  }
  ;(window as unknown as { api: Api }).api = api
  return api
}

function makeAgent(partial: Partial<Agent>): Agent {
  return {
    name: "agent-1",
    type: "openclaw",
    state: "stopped",
    network: null,
    ...partial,
  }
}

const showToast = vi.fn()

beforeEach(() => {
  // Zustand store is module-global; reset it so tests don't leak agents.
  useAgentsStore.setState({ agents: [], pendingAgentActions: new Set() })
  showToast.mockClear()
})

// Placement AI has no agent-type catalog any more — creating an agent is just
// a name and a working folder.
async function createAndReachConfigure(user: ReturnType<typeof userEvent.setup>, name = "my-new-agent"): Promise<void> {
  await user.click(screen.getByTestId("new-agent-open"))
  const nameInput = await screen.findByLabelText(/agent name/i)
  await user.clear(nameInput)
  await user.type(nameInput, name)
  const create = screen.getByRole("button", { name: /^create$/i })
  await waitFor(() => expect(create).toBeEnabled())
  await user.click(create)
}

describe("Agents page — new agent connect flow", () => {
  it("opens the Connect Workspace dialog after a new agent is created", async () => {
    installApi()
    const user = userEvent.setup()
    render(<Agents showToast={showToast} />)

    await createAndReachConfigure(user, "my-new-agent")

    // The connect dialog for this specific agent should now be visible.
    await waitFor(() =>
      expect(
        screen.getByText(/add 'my-new-agent' to a workspace/i),
      ).toBeInTheDocument(),
    )
  })

  it("lets the user skip the connect step", async () => {
    installApi()
    const user = userEvent.setup()
    render(<Agents showToast={showToast} />)

    await createAndReachConfigure(user)
    await screen.findByRole("dialog")

    // Cancel out of the connect dialog — no connection attempted.
    await user.click(screen.getByRole("button", { name: /^cancel$/i }))

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    )
    expect(
      (window as unknown as { api: Api }).api.connectWorkspace,
    ).not.toHaveBeenCalled()
  })
})

describe("Agents page — Join workspace vs Open Workspace gating", () => {
  it("an agent with no workspace shows Join workspace, not Open Workspace", async () => {
    installApi({
      listAgents: vi
        .fn()
        .mockResolvedValue([makeAgent({ name: "lonely", network: null })]),
    })
    render(<Agents showToast={showToast} />)

    await screen.findByText("lonely")
    expect(screen.getByRole("button", { name: /^join workspace$/i })).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /open workspace/i }),
    ).not.toBeInTheDocument()
  })

  it("an agent in a workspace shows Open Workspace, not Join workspace", async () => {
    installApi({
      listAgents: vi
        .fn()
        .mockResolvedValue([makeAgent({ name: "joined", network: "team-x" })]),
    })
    render(<Agents showToast={showToast} />)

    await screen.findByText("joined")
    expect(
      screen.getByRole("button", { name: /open workspace/i }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /^join workspace$/i }),
    ).not.toBeInTheDocument()
  })
})

describe("ConnectWorkspaceDialog — picking a workspace", () => {
  async function openConnectDialog(api: Api): Promise<ReturnType<typeof userEvent.setup>> {
    const user = userEvent.setup()
    render(<Agents showToast={showToast} />)
    await screen.findByText("lonely")
    await user.click(screen.getByRole("button", { name: /^join workspace$/i }))
    await screen.findByText(/add 'lonely' to a workspace/i)
    return user
  }

  it("connects to an existing workspace from the list", async () => {
    const api = installApi({
      listAgents: vi
        .fn()
        .mockResolvedValue([makeAgent({ name: "lonely", network: null })]),
      listWorkspaces: vi.fn().mockResolvedValue([
        { id: "id-1", slug: "team-a", name: "Team A", endpoint: "", token: "t" },
      ]),
    })
    const user = await openConnectDialog(api)

    await user.click(await screen.findByRole("button", { name: /team a/i }))

    await waitFor(() =>
      expect(api.connectWorkspace).toHaveBeenCalledWith("lonely", "team-a"),
    )
  })

  // Joining is the Workspaces page's job, so with nothing to pick from this
  // dialog hands the user over to it instead of growing a second connect form.
  it("sends you to the Workspaces page when this device is in no workspace", async () => {
    const api = installApi({
      listAgents: vi
        .fn()
        .mockResolvedValue([makeAgent({ name: "lonely", network: null })]),
    })
    const user = await openConnectDialog(api)

    await screen.findByText(/isn't in any workspace yet/i)
    await user.click(screen.getByRole("button", { name: /go to workspaces/i }))

    expect(useUiStore.getState().pendingCreate).toBe("workspace")
  })

  // No manual-token or connect form lives here: this dialog only ever picks
  // from the workspaces `listWorkspaces()` already reports for this device.
  it("offers no connect form of its own", async () => {
    const api = installApi({
      listAgents: vi
        .fn()
        .mockResolvedValue([makeAgent({ name: "lonely", network: null })]),
      listWorkspaces: vi.fn().mockResolvedValue([
        { id: "id-1", slug: "team-a", name: "Team A", endpoint: "", token: "t" },
      ]),
    })
    await openConnectDialog(api)
    await screen.findByRole("button", { name: /team a/i })

    expect(screen.queryByText(/connect manually/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/pairing code/i)).not.toBeInTheDocument()
  })
})

describe("Agents page — configure an existing agent's working directory", () => {
  async function openConfigureMenu(
    user: ReturnType<typeof userEvent.setup>,
  ): Promise<void> {
    await user.click(screen.getByRole("button", { name: /more actions/i }))
    await user.click(await screen.findByRole("menuitem", { name: /configure/i }))
  }

  it("saves a new working directory for the agent", async () => {
    const api = installApi({
      listAgents: vi
        .fn()
        .mockResolvedValue([makeAgent({ name: "plain-1", path: "/old/path" })]),
    })
    const user = userEvent.setup()
    render(<Agents showToast={showToast} />)
    await screen.findByText("plain-1")
    await openConfigureMenu(user)

    const folderInput = await screen.findByLabelText(/working directory/i)
    await user.clear(folderInput)
    await user.type(folderInput, "/new/path")
    await user.click(screen.getByRole("button", { name: /^save folder$/i }))

    await waitFor(() =>
      expect(api.setAgentWorkingDir).toHaveBeenCalledWith("plain-1", "/new/path"),
    )
  })
})
