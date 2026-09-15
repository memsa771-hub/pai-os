import { beforeEach, expect, it, vi } from "vitest"

const fakes = vi.hoisted(() => ({
  handlers: new Map<string, (...args: any[]) => any>(),
  listeners: new Map<string, (...args: any[]) => any>(),
  accountDeps: null as null | { onChange: (info: unknown) => void },
  bearer: vi.fn(),
  signOut: vi.fn(),
  show: vi.fn(),
  hide: vi.fn(),
  hostSignOut: vi.fn(),
  whenCleared: vi.fn(),
  sendSession: vi.fn(),
  sendNotice: vi.fn(),
  send: vi.fn(),
  isWorkspaceSender: vi.fn(),
}))
vi.mock("electron", () => ({
  ipcMain: {
    handle: (name: string, handler: (...args: any[]) => any) => fakes.handlers.set(name, handler),
    on: (name: string, handler: (...args: any[]) => any) => fakes.listeners.set(name, handler),
  },
}))
vi.mock("../web-security", () => ({ openExternalSafely: vi.fn() }))
vi.mock("./account", () => ({
  AccountManager: class {
    constructor(deps: { onChange: (info: unknown) => void }) { fakes.accountDeps = deps }
    getAccount() { return { email: "person@example.test" } }
    embeddedSession() { return { token: "renewed", email: "person@example.test", displayName: null, expiresAt: 1 } }
    bearer = fakes.bearer
    signOut = fakes.signOut
  },
}))
vi.mock("../workspace-host", () => ({
  WorkspaceHost: class {
    show = fakes.show; hide = fakes.hide; isWorkspaceSender = fakes.isWorkspaceSender
    signOut = fakes.hostSignOut; whenCleared = fakes.whenCleared
    sendSession = fakes.sendSession; sendNotice = fakes.sendNotice
  },
}))
import { registerAccountIpc } from "./ipc"

beforeEach(() => {
  vi.clearAllMocks()
  fakes.handlers.clear()
  fakes.listeners.clear()
  fakes.bearer.mockResolvedValue("token")
  fakes.whenCleared.mockResolvedValue(undefined)
  fakes.isWorkspaceSender.mockReturnValue(true)
  registerAccountIpc({
    appearance: () => ({ theme: "system", language: "en" }),
    setAppearance: vi.fn(), endpoint: () => undefined,
    getWindow: () => ({ webContents: { send: fakes.send } }) as never,
  })
})

const bounds = { x: 0, y: 40, width: 1000, height: 700 }

it("does not cover local tools when an earlier workspace token refresh finishes", async () => {
  let finish!: () => void
  fakes.bearer.mockReturnValueOnce(new Promise<void>(resolve => { finish = resolve }))
  const opening = fakes.handlers.get("workspace-view:show")!({}, null, bounds)
  fakes.handlers.get("workspace-view:hide")!()
  finish()
  await opening
  expect(fakes.hide).toHaveBeenCalledOnce()
  expect(fakes.show).not.toHaveBeenCalled()
})

it("shows the latest workspace request even if an earlier refresh is slower", async () => {
  let finish!: () => void
  fakes.bearer.mockReturnValueOnce(new Promise<void>(resolve => { finish = resolve }))
  const earlier = fakes.handlers.get("workspace-view:show")!({}, "earlier", bounds)
  await fakes.handlers.get("workspace-view:show")!({}, "latest", bounds)
  finish()
  await earlier
  expect(fakes.show).toHaveBeenCalledExactlyOnceWith("latest", bounds, null)
})

it("ends the page and wipes its storage however the account ends", () => {
  fakes.accountDeps!.onChange(null)
  expect(fakes.hostSignOut).toHaveBeenCalledOnce()
  expect(fakes.send).toHaveBeenCalledWith("account:changed", null)
})

it("does not show a page for an account that ended while its token was refreshing", async () => {
  let finish!: () => void
  fakes.bearer.mockReturnValueOnce(new Promise<void>(resolve => { finish = resolve }))
  const opening = fakes.handlers.get("workspace-view:show")!({}, null, bounds)
  fakes.accountDeps!.onChange(null)
  finish()
  await opening
  expect(fakes.show).not.toHaveBeenCalled()
})

it("hands a renewed session to the loaded page", () => {
  fakes.accountDeps!.onChange({ email: "person@example.test" })
  expect(fakes.sendSession).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ token: "renewed" }))
  expect(fakes.hostSignOut).not.toHaveBeenCalled()
})

it("creates no view until the last sign-out has finished wiping storage", async () => {
  let wiped!: () => void
  fakes.whenCleared.mockReturnValueOnce(new Promise<void>(resolve => { wiped = resolve }))
  const opening = fakes.handlers.get("workspace-view:show")!({}, null, bounds)
  await Promise.resolve(); await Promise.resolve()
  expect(fakes.show).not.toHaveBeenCalled()
  wiped()
  await opening
  expect(fakes.show).toHaveBeenCalledOnce()
})

it("resolves a sign-out only once the page's storage is gone", async () => {
  let wiped!: () => void
  fakes.whenCleared.mockReturnValueOnce(new Promise<void>(resolve => { wiped = resolve }))
  let done = false
  const signingOut = fakes.handlers.get("account:sign-out")!().then(() => { done = true })
  expect(fakes.signOut).toHaveBeenCalledOnce()
  await Promise.resolve()
  expect(done).toBe(false)
  wiped()
  await signingOut
  expect(done).toBe(true)
})

it("ends the account when the owned page asks to sign out, and ignores anyone else", () => {
  fakes.isWorkspaceSender.mockReturnValueOnce(false)
  fakes.listeners.get("workspace-view:sign-out")!({ sender: {} })
  expect(fakes.signOut).not.toHaveBeenCalled()
  fakes.listeners.get("workspace-view:sign-out")!({ sender: {} })
  expect(fakes.signOut).toHaveBeenCalledOnce()
})

it("never takes a session reported by the page", () => {
  expect(fakes.listeners.has("workspace-view:session-changed")).toBe(false)
})

it("repeats only well-formed notices inside the page", () => {
  const notice = fakes.handlers.get("workspace-view:notice")!
  notice({}, { message: "Opened in your browser", type: "script" })
  notice({}, { message: "", type: "info" })
  notice({}, null)
  expect(fakes.sendNotice).not.toHaveBeenCalled()
  notice({}, { message: "Opened in your browser", type: "info" })
  expect(fakes.sendNotice).toHaveBeenCalledExactlyOnceWith({ message: "Opened in your browser", type: "info" })
})
