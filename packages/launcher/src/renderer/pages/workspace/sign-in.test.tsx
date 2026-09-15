import { beforeEach, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { WorkspaceSignIn } from "./sign-in"
import { useAccountStore } from "../../store/account"
import i18n from "../../i18n"

vi.mock("../../lib/analytics", () => ({ capture: vi.fn() }))

const SUCCESS_ACCOUNT = { email: "person@example.test", displayName: "person", expiresAt: 9999999999 }

beforeEach(async () => {
  await i18n.changeLanguage("en")
  useAccountStore.setState({
    account: null, mode: "workspace", authMode: "sign-in", error: null,
    signingIn: false, pendingEmail: null,
  })
  window.api = {
    signUpWithPassword: vi.fn().mockResolvedValue({ account: SUCCESS_ACCOUNT, needsEmailConfirmation: false }),
    signInWithPassword: vi.fn(),
    signInWithUsername: vi.fn(),
  } as unknown as typeof window.api
})

function fill(id: string, value: string): void {
  fireEvent.change(document.getElementById(id)!, { target: { value } })
}

function openRegistration(): void {
  render(<WorkspaceSignIn />)
  fireEvent.click(screen.getByRole("button", { name: "Create an account" }))
}

it("offers registration beside sign-in and preserves the identifier when switching back", () => {
  render(<WorkspaceSignIn />)
  fill("sign-in-identifier", "person@example.test")
  fill("sign-in-password", "ExistingPassword1!")
  fireEvent.click(screen.getByRole("button", { name: "Create an account" }))
  expect(screen.getByRole("heading", { name: "Create your OpenAgents account" })).toBeVisible()
  expect(document.getElementById("sign-in-identifier")).toHaveValue("person@example.test")
  expect(document.getElementById("sign-in-password")).toHaveValue("")
  expect(document.getElementById("sign-in-password")).toHaveAttribute("autocomplete", "new-password")
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }))
  expect(screen.getByRole("heading", { name: "Sign in to OpenAgents" })).toBeVisible()
  expect(document.getElementById("sign-in-identifier")).toHaveValue("person@example.test")
})

it("checks confirmation before creating the account and opens the returned session", async () => {
  openRegistration()
  fill("sign-up-username", "person")
  fill("sign-in-identifier", "person@example.test")
  fill("sign-in-password", "NewAccount1!")
  fill("sign-up-confirm", "Different1!")
  fireEvent.click(screen.getByRole("button", { name: "Create Account" }))
  expect(screen.getByText("The passwords do not match.")).toBeVisible()
  expect(window.api.signUpWithPassword).not.toHaveBeenCalled()
  fill("sign-up-confirm", "NewAccount1!")
  fireEvent.click(screen.getByRole("button", { name: "Create Account" }))
  await waitFor(() => expect(useAccountStore.getState().account?.email).toBe("person@example.test"))
  expect(window.api.signUpWithPassword).toHaveBeenCalledExactlyOnceWith("person@example.test", "NewAccount1!", "person")
})

it("shows a check-your-email state when Supabase requires confirming the address first", async () => {
  vi.mocked(window.api.signUpWithPassword).mockResolvedValueOnce({ account: null, needsEmailConfirmation: true })
  openRegistration()
  fill("sign-up-username", "person")
  fill("sign-in-identifier", "person@example.test")
  fill("sign-in-password", "NewAccount1!")
  fill("sign-up-confirm", "NewAccount1!")
  fireEvent.click(screen.getByRole("button", { name: "Create Account" }))
  expect(await screen.findByRole("heading", { name: "Check your email" })).toBeVisible()
  expect(screen.getByText(/person@example\.test/)).toBeVisible()
  expect(useAccountStore.getState().account).toBeNull()
})

it("explains a duplicate email and keeps the sign-in switch available", async () => {
  vi.mocked(window.api.signUpWithPassword).mockRejectedValueOnce(new Error("SIGN_UP_EMAIL_EXISTS"))
  openRegistration()
  fill("sign-up-username", "person")
  fill("sign-in-identifier", "person@example.test")
  fill("sign-in-password", "NewAccount1!")
  fill("sign-up-confirm", "NewAccount1!")
  fireEvent.click(screen.getByRole("button", { name: "Create Account" }))
  expect(await screen.findByText("This email is already registered. Sign in with your existing account.")).toBeVisible()
  expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled()
})

it("signs in with a username (no @) through workspace/backend instead of Supabase directly", async () => {
  vi.mocked(window.api.signInWithUsername).mockResolvedValueOnce(SUCCESS_ACCOUNT)
  render(<WorkspaceSignIn />)
  fill("sign-in-identifier", "person")
  fill("sign-in-password", "ExistingPassword1!")
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }))
  await waitFor(() => expect(useAccountStore.getState().account?.email).toBe("person@example.test"))
  expect(window.api.signInWithUsername).toHaveBeenCalledExactlyOnceWith("person", "ExistingPassword1!")
  expect(window.api.signInWithPassword).not.toHaveBeenCalled()
})
