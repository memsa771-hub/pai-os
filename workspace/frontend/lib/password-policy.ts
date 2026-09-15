// Duplicated verbatim from packages/launcher/src/shared/account-registration.ts
// (no shared package yet — see the auth architecture plan). Keep the two in
// sync; this is the sign-up password-strength rule for both web and desktop.
export function registrationPasswordError(password: string): string | null {
  const groups = [/[A-Z]/, /[a-z]/, /[0-9]/, /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/]
  if (password.length < 8 || groups.filter((pattern) => pattern.test(password)).length < 3)
    return "SIGN_UP_WEAK_PASSWORD"
  if (["12345678", "password", "qwerty123", "abc12345", "11111111", "00000000"].includes(password.toLowerCase()))
    return "SIGN_UP_WEAK_PASSWORD"
  return null
}
