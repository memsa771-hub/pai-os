export type MainLanguage = "en"

const STRINGS: Record<string, string> = {
  appName: "Placement AI",
  updateReadyTitle: "Update ready",
  updateReadyBody: "Placement AI v{{version}} is downloaded. Click ‘Restart & install’ to apply it.",
  updateAvailableTitle: "Update available",
  updateAvailableBody: "Placement AI v{{version}} is available. Open Settings → About & Updates to download it.",
  trayRestartToUpdate: "Restart to update (v{{version}})",
  startupFailedTitle: "Placement AI could not start",
  startupFailedBody: "{{message}}\n\nThe full log is at:\n{{log}}\n\nPlease send it to support if this keeps happening.",
  trayTooltip: "Placement AI",
  trayOpenDashboard: "Open Placement AI",
  trayNoAgents: "No agents configured",
  trayQuit: "Quit Placement AI",
  quitTitle: "Quit Placement AI",
  quitMessage: "Quit Placement AI?",
  quitDetail: "The background service will stop and all connected agents will go offline.",
  quitConfirm: "Quit",
  cancel: "Cancel",
}

export function setMainLanguage(_language: unknown): void {}
export function getMainLanguage(): MainLanguage { return "en" }

export function t(key: string, vars: Record<string, string | number> = {}): string {
  const template = STRINGS[key] ?? key
  return template.replace(/\{\{(\w+)\}\}/g, (_match, name: string) =>
    name in vars ? String(vars[name]) : `{{${name}}}`,
  )
}
