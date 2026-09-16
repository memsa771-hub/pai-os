import { beforeEach, expect, it, vi } from "vitest"
import { useAccountStore } from "./account"

vi.mock("../lib/analytics", () => ({ capture: vi.fn() }))

const account = { email: "person@example.test", displayName: "Person", expiresAt: 9999999999 }

beforeEach(() => {
  localStorage.clear()
  useAccountStore.setState({
    account: null, authMode: "welcome", pendingEmail: null, ready: false,
  })
  window.api = {
    getAccount: vi.fn().mockResolvedValue(account),
    onAccountChanged: vi.fn(),
    onSignInExternal: vi.fn(),
    onSignInFailed: vi.fn(),
    onWorkspaceSignIn: vi.fn(),
  } as unknown as typeof window.api
})

it("reads the account on startup", async () => {
  await useAccountStore.getState().init()
  expect(window.api.getAccount).toHaveBeenCalledOnce()
  expect(useAccountStore.getState()).toMatchObject({ ready: true, account })
})

it("stays usable when the account read fails", async () => {
  vi.mocked(window.api.getAccount).mockRejectedValueOnce(new Error("IPC unavailable"))
  const error = vi.spyOn(console, "error").mockImplementation(() => {})
  await useAccountStore.getState().init()
  expect(useAccountStore.getState()).toMatchObject({ ready: true, account: null, authMode: "welcome" })
  error.mockRestore()
})

it("shows the native sign-in gate when the embedded page's session breaks", async () => {
  await useAccountStore.getState().init()
  vi.mocked(window.api.onWorkspaceSignIn).mock.calls[0][0]()
  expect(useAccountStore.getState().authMode).toBe("sign-in")
})

it("signing out shows the sign-in gate, not Welcome", async () => {
  await useAccountStore.getState().init()
  vi.mocked(window.api.onAccountChanged).mock.calls[0][0](null)
  expect(useAccountStore.getState()).toMatchObject({ account: null, authMode: "sign-in" })
})

it("signed out, Workspace starts at Welcome and sign-in can go back to it", () => {
  useAccountStore.getState().openSignIn()
  expect(useAccountStore.getState().authMode).toBe("sign-in")
  useAccountStore.getState().showWelcome()
  expect(useAccountStore.getState().authMode).toBe("welcome")
})
