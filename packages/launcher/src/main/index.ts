// FIRST import, on purpose — see win-console.ts.
import "./win-console"
import {
  app,
  BrowserWindow,
  dialog,
  Tray,
  Menu,
  ipcMain,
  nativeImage,
  nativeTheme,
  session,
  shell,
} from "electron"
import path from "path"
import fs from "fs"
import os from "os"
import { Store, settingsFilePath } from "./store"
import {
  setupAutoUpdater,
  checkForUpdatesOnStartup,
  getUpdaterState,
  installDownloadedUpdate,
  applyUpdateFeedUrl,
} from "./updater"
import { t, getMainLanguage, setMainLanguage } from "./i18n"
import { asPath } from "./ipc-input"
import {
  setNotificationsWindow,
  pushNotification,
  listNotifications,
  markRead,
  markAllRead,
  clearAll as clearAllNotifications,
  clearOne as clearOneNotification,
  clearBySource as clearNotificationsBySource,
  getPrefs as getNotifPrefs,
  setPrefs as setNotifPrefs,
  setPrefsStorage as setNotifPrefsStorage,
  type NotificationPrefs,
} from "./notifications"
import {
  markUiReached,
  reportStartupError,
  slog,
  STARTUP_LOG,
} from "./bootstrap/startup-log"
import { registerAccountIpc } from "./auth/ipc"
import type { ThemeMode } from "../shared/appearance-bridge"
import {
  registerWorkspaceScheme,
  serveWorkspaceBundle,
} from "./workspace-bundle"
import { normalizeWorkspaceEndpoint } from "./workspace-endpoint"
import { attachRendererLogging, rendererLogPath } from "./renderer-log"
import { applyProxyFromSettings } from "./net-config"
import { hardenWebContents, openExternalSafely } from "./web-security"
import { installApplicationMenu, isReloadShortcut } from "./app-menu"
import {
  applyThemeSource,
  createPlaceholderIcon,
  refreshTitleBarOverlay,
  setChromeDimmed,
  setChromeSkin,
  splashPalette,
  titleBarOverlayColors,
} from "./window-chrome"

app.setName("Placement AI")

// Stop macOS from popping the "<App> wants to use the keychain Safe Storage"
// password prompt. That entry is Chromium's OSCrypt key (shared with Electron's
// safeStorage) used to encrypt cookies/local storage; its keychain ACL is bound
// to the app's code signature, so every unsigned dev run / Electron upgrade
// re-triggers the prompt. We don't keep anything security-critical in Chromium
// storage, so route OSCrypt to an in-memory mock keychain — no prompt, no real
// keychain access. (Our own credential secrets are encrypted separately.)
app.commandLine.appendSwitch("use-mock-keychain")

// Remote-driving hook for tests: PAI_DEVTOOLS_PORT=9222 exposes the Chrome
// DevTools Protocol so Playwright's connectOverCDP (through an SSH tunnel, for
// a remote machine) can attach to the RUNNING app — real clicks, renderer
// console, screenshots. Gated on the env var and pinned to loopback: never on
// by default, never reachable from another host.
const devtoolsPort = process.env.PAI_DEVTOOLS_PORT
if (devtoolsPort && /^\d+$/.test(devtoolsPort)) {
  app.commandLine.appendSwitch("remote-debugging-port", devtoolsPort)
  app.commandLine.appendSwitch("remote-debugging-address", "127.0.0.1")
}

const isHeadless = process.argv.includes("--headless")
if (process.argv.includes("--disable-gpu") || isHeadless) {
  app.disableHardwareAcceleration()
}

/**
 * Whether this profile ran the app before this process started.
 *
 * Read here, ahead of `new Store()` and therefore ahead of every write in the
 * run — anything that asks later is told "yes" by a profile created seconds
 * ago. Captured once, it stays true for the life of the process.
 *
 * Two traces, because neither is enough alone:
 *
 *   settings.json  — written the first time any preference changes.
 *   Local Storage  — Chromium creates it the first time a page stores
 *                    anything, which the theme store does on the first frame.
 *
 * The release-notes dialog is what needs this: getting this answer wrong
 * costs a user their release notes permanently, so it is logged to
 * startup.log, the only way to tell afterwards which way it went.
 */
const HAS_RUN_BEFORE = ((): boolean => {
  const dir = app.getPath("userData")
  const traces: Array<[string, string]> = [
    ["settings.json", settingsFilePath()],
    ["Local Storage", path.join(dir, "Local Storage")],
  ]
  const found = traces.filter(([, p]) => fs.existsSync(p)).map(([name]) => name)
  slog(`profile: userData=${dir} traces=[${found.join(", ")}]`)
  return found.length > 0
})()

const store = new Store()

// Notification prefs live in settings.json like every other preference, so they
// survive a restart and travel with export/import. Wired here rather than
// imported inside ./notifications so that module keeps no dependency on the
// store. Registered at module scope because notifications can fire from the
// updater before any window exists.
setNotifPrefsStorage({
  read: () => store.get("notifications"),
  write: (prefs: NotificationPrefs) => store.set("notifications", prefs),
})

// User-controlled GPU toggle (Settings → General). disableHardwareAcceleration
// must run before app "ready", and this module scope is still pre-ready. Only
// disable when explicitly turned off (default on); the --disable-gpu / headless
// check above forces it off regardless. Takes effect after a restart.
if (store.get("gpuAcceleration") === false) {
  app.disableHardwareAcceleration()
}

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
// Last app-update version we notified about, so re-emitted update-downloaded
// events (electron-updater fires it from cache on every subsequent check)
// don't spam the same "update ready" toast.
let _lastUpdateNotifiedVersion: string | null = null

let _appVersionCache: string | null = null
function getAppVersion(): string {
  if (_appVersionCache) return _appVersionCache
  try {
    _appVersionCache = require("../../package.json").version as string
  } catch {
    _appVersionCache = "0.0.0"
  }
  return _appVersionCache!
}

// Nothing in main is allowed to take the process down quietly. Registered at
// module scope so it covers the window between `require` and `whenReady` too.
process.on("uncaughtException", reportStartupError)
process.on("unhandledRejection", reportStartupError)

function createWindow(): void {
  if (mainWindow) {
    if (process.platform === "darwin" && app.dock) app.dock.show()
    mainWindow.show()
    mainWindow.focus()
    return
  }

  mainWindow = new BrowserWindow({
    minWidth: 1200,
    minHeight: 800,
    width: 1200,
    height: 800,
    title: "Placement AI",
    autoHideMenuBar: true,
    // The app draws its own top edge. The system title bar was a grey plate
    // above a themed app, repeating a name and icon the rail already shows —
    // `hidden` removes the plate but keeps the real window buttons, so Windows
    // 11 Snap Layouts, double-click-to-maximise and the close affordance all
    // still come from the OS rather than from buttons we would have to draw.
    titleBarStyle: "hidden",
    ...(process.platform === "darwin"
      ? {
          // Centred in the reserved strip: (40 - 12) / 2 ≈ 14 from the top,
          // and far enough in from the left to clear the rail's rounded corner.
          trafficLightPosition: { x: 16, y: 14 },
        }
      : { titleBarOverlay: titleBarOverlayColors() }),
    // Paints while the renderer boots, so the window does not flash white
    // before the first frame — the frame used to hide that behind its own
    // chrome. Matches `--background`, like the overlay above.
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#0f1115" : "#f2f2f7",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false,
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"))
  }

  setNotificationsWindow(mainWindow)
  hardenWebContents(mainWindow.webContents)
  // Mirror the renderer console to ~/.pai-desktop/renderer.log — the only
  // trace of renderer errors on machines reached over SSH.
  attachRendererLogging(mainWindow.webContents)

  // Forget any dim state at the START of a load, not at the end of one. A
  // reload can strand main holding a dim whose dialog is long gone, so it does
  // have to be cleared — but the renderer reports its own the moment its entry
  // script runs, which is long before `did-finish-load`. Clearing it there
  // overwrote the report of any dialog opened during startup — the release
  // notes are exactly that — and left the buttons bright over a scrimmed app.
  mainWindow.webContents.on("did-start-loading", () =>
    setChromeDimmed(mainWindow, false),
  )

  // Repaint the overlay once the page is up, for a window created before the
  // stored theme took effect: its buttons wear a colour nothing on screen
  // explains. Whatever the renderer has since said about a dialog is kept.
  mainWindow.webContents.on("did-finish-load", () =>
    refreshTitleBarOverlay(mainWindow),
  )

  // Full screen hides the window buttons on every platform, which leaves the
  // strip the app reserves for them holding nothing. Tell the renderer so it
  // can give the space back — see `--titlebar-h` in globals.css.
  const sendFullScreen = (v: boolean): void =>
    mainWindow?.webContents.send("window:full-screen", v)
  mainWindow.on("enter-full-screen", () => sendFullScreen(true))
  mainWindow.on("leave-full-screen", () => sendFullScreen(false))

  mainWindow.once("ready-to-show", () => {
    if (process.platform === "darwin" && app.dock) app.dock.show()
    mainWindow!.show()
    // On Windows, splash window (`alwaysOnTop: true`) sometimes leaves
    // focus on the desktop after it closes, so the main window appears but
    // doesn't receive clicks until the user clicks the title bar. Force the
    // focus to land on the app so sign-in is immediately interactive.
    if (process.platform === "win32") {
      mainWindow!.focus()
      mainWindow!.moveTop()
    }
    // DevTools — dev only. Production builds (`app.isPackaged === true`) skip
    // this so end users never see the inspector pop up.
    if (!app.isPackaged) {
      mainWindow!.webContents.openDevTools({ mode: "detach" })
    }
  })

  mainWindow.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return

    // Reload throws away everything the renderer is holding — a half-finished
    // sign-in, an unsent message — and nothing in the app asks for it. Dev
    // builds keep it; shipped builds do not.
    if (app.isPackaged && isReloadShortcut(input)) {
      event.preventDefault()
      return
    }

    // DevTools toggle (F12 / Cmd+Opt+I / Ctrl+Shift+I), dev only.
    if (app.isPackaged) return
    const isToggle =
      input.key === "F12" ||
      (input.key.toLowerCase() === "i" &&
        ((process.platform === "darwin" && input.meta && input.alt) ||
          (process.platform !== "darwin" && input.control && input.shift)))
    if (isToggle) {
      event.preventDefault()
      const wc = mainWindow!.webContents
      if (wc.isDevToolsOpened()) wc.closeDevTools()
      else wc.openDevTools({ mode: "detach" })
    }
  })

  mainWindow.on("close", (e) => {
    // Honor the "Minimize to tray" setting (Settings → General, default on).
    // When off, closing the window really quits instead of hiding to the tray.
    const toTray = store.get("minimizeToTray") !== false
    if (
      toTray &&
      !(app as typeof app & { isQuitting?: boolean }).isQuitting
    ) {
      e.preventDefault()
      mainWindow!.hide()
      if (process.platform === "darwin" && app.dock) app.dock.hide()
    }
  })

  mainWindow.on("closed", () => {
    mainWindow = null
  })
}

// Apply the "Launch at login" setting (Settings → General) to the OS.
function applyStartOnBoot(): void {
  try {
    app.setLoginItemSettings({
      openAtLogin: store.get("startOnBoot") === true,
    })
  } catch {}
}

function createTray(): void {
  // macOS: a white glyph, inset to 18pt. The menu-bar canvas is 22pt and AppKit
  // draws the image at that size, while other menu-bar extras keep their glyph
  // around 18pt inside it — full-bleed 22pt art reads as oversized next to
  // them. Electron auto-loads the matching @2x file as the Retina rep.
  //
  // Windows: the app icon, not a glyph. The notification area follows the
  // "Windows mode" setting independently of the app's own theme, so it can be
  // light or dark and a monochrome glyph is invisible against one of them.
  //
  // Linux: panels are conventionally dark, so the white glyph stands.
  //
  // Path: in dev, assets/ sits two levels above out/main. In packaged builds
  // that directory is NOT inside app.asar — it is `directories.buildResources`,
  // which electron-builder never bundles — so it is copied to
  // Contents/Resources/assets via `extraResources` instead.
  const assetsDir = app.isPackaged
    ? path.join(process.resourcesPath, "assets")
    : path.join(__dirname, "../../assets")
  const trayIconFile =
    process.platform === "darwin"
      ? "tray-icon-mac.png"
      : process.platform === "win32"
        ? "icon.ico"
        : "tray-icon-light.png"
  let trayIcon = nativeImage.createFromPath(
    path.join(assetsDir, trayIconFile),
  )
  if (!trayIcon || trayIcon.isEmpty()) trayIcon = createPlaceholderIcon()

  tray = new Tray(trayIcon)
  tray.setToolTip(t("trayTooltip"))
  updateTrayMenu()
  tray.on("click", () => createWindow())
}

function updateTrayMenu(): void {
  if (!tray) return

  // App self-update: once a background download has landed, offer an
  // immediate "restart to update" instead of waiting for the next quit.
  const appUpdate = getUpdaterState()
  // Hidden once the handoff for this version is known to fail: the tray item
  // would offer a restart that has already proven to install nothing, and
  // unlike the banner the tray has nowhere to explain that.
  const appUpdateItems: Electron.MenuItemConstructorOptions[] =
    appUpdate.status === "downloaded" &&
    appUpdate.installFailedVersion !== appUpdate.latestVersion
      ? [
          { type: "separator" },
          {
            label: t("trayRestartToUpdate", {
              version: appUpdate.latestVersion ?? "?",
            }),
            click: () => {
              installDownloadedUpdate()
            },
          },
        ]
      : []

  const menu = Menu.buildFromTemplate([
    { label: t("trayOpenDashboard"), click: () => createWindow() },
    ...appUpdateItems,
    { type: "separator" },
    {
      label: t("trayQuit"),
      click: async () => {
        const result = await dialog.showMessageBox({
          type: "question",
          buttons: [t("quitConfirm"), t("cancel")],
          defaultId: 1,
          title: t("quitTitle"),
          message: t("quitMessage"),
          detail: t("quitDetail"),
        })
        if (result.response === 0) {
          ;(app as typeof app & { isQuitting: boolean }).isQuitting = true
          app.quit()
        }
      },
    },
  ])

  tray.setContextMenu(menu)
  tray.setToolTip(t("trayTooltip"))
}

function setupIPC(): void {
  // ── Window / theme chrome ──
  ipcMain.handle(
    "window:is-full-screen",
    () => mainWindow?.isFullScreen() ?? false,
  )
  ipcMain.handle("theme:set-source", (_e, mode: unknown) => {
    applyThemeSource(mode)
    store.set("themeMode", nativeTheme.themeSource)
  })
  // Dialogs scrim the page, but the window buttons are drawn by the OS on top
  // of it — the renderer says when one is open so the overlay can be repainted
  // to match. See setChromeDimmed.
  ipcMain.handle("window:chrome-dim", (_e, dim: unknown) => {
    setChromeDimmed(mainWindow, dim === true)
  })

  // ── Settings ──
  ipcMain.handle("settings:get", (_e, key) => store.get(key))
  ipcMain.handle("settings:set", (_e, key, value) => {
    store.set(key, value)
    if (key === "startOnBoot") applyStartOnBoot()
    if (key === "skin") setChromeSkin(mainWindow, value)
    // Keep main's notification/tray strings on the language the user picked in
    // Settings — main can't read the renderer's localStorage-backed i18next.
    if (key === "language") {
      setMainLanguage(value)
      updateTrayMenu()
    }
    if (key === "httpProxy" || key === "httpsProxy" || key === "noProxy") {
      applyProxyFromSettings(store)
    }
    if (key === "updateFeedUrl") applyUpdateFeedUrl(value)
  })
  ipcMain.handle("settings:get-all", () => store.get())
  ipcMain.handle("settings:export", () => JSON.stringify(store.get(), null, 2))
  // Writes through a native Save dialog so the user picks the destination and
  // a cancel is reported as such.
  ipcMain.handle("settings:export-to-file", async () => {
    const win = BrowserWindow.getFocusedWindow() || mainWindow
    const stamp = new Date().toISOString().slice(0, 10)
    const opts = {
      defaultPath: `pai-desktop-settings-${stamp}.json`,
      filters: [{ name: "JSON", extensions: ["json"] }],
    }
    const result = win
      ? await dialog.showSaveDialog(win, opts)
      : await dialog.showSaveDialog(opts)
    if (result.canceled || !result.filePath) return { ok: false, canceled: true }
    try {
      fs.writeFileSync(
        result.filePath,
        JSON.stringify(store.get(), null, 2),
        "utf-8",
      )
      return { ok: true, path: result.filePath }
    } catch (e) {
      return { ok: false, error: (e as Error).message }
    }
  })
  ipcMain.handle("settings:import", (_e, json: string) => {
    try {
      const parsed = JSON.parse(json)
      if (!parsed || typeof parsed !== "object") {
        return { ok: false, error: "Expected an object" }
      }
      for (const [k, v] of Object.entries(parsed)) {
        store.set(k, v)
      }
      return { ok: true }
    } catch (e) {
      return { ok: false, error: (e as Error).message }
    }
  })
  ipcMain.handle("settings:reset", () => {
    const all = store.get() as Record<string, unknown>
    for (const k of Object.keys(all)) store.delete(k)
    return true
  })

  // ── Paths / system info (About, Data) ──
  ipcMain.handle("paths:list", () => ({
    userData: app.getPath("userData"),
    logs: app.getPath("logs"),
    downloads: app.getPath("downloads"),
    home: app.getPath("home"),
    cache: app.getPath("sessionData"),
  }))
  ipcMain.handle("paths:show", (_e, p: string) => {
    try {
      shell.showItemInFolder(asPath(p, "path"))
      return true
    } catch {
      return false
    }
  })
  ipcMain.handle("system:info", () => {
    let diskFree: number | null = null
    let diskTotal: number | null = null
    try {
      const statfs = (fs as unknown as { statfsSync?: (p: string) => { bsize: number; blocks: number; bavail: number } }).statfsSync
      if (statfs) {
        const st = statfs(app.getPath("userData"))
        diskFree = st.bsize * st.bavail
        diskTotal = st.bsize * st.blocks
      }
    } catch {}

    let appMemory = 0
    let appCpu = 0
    try {
      for (const m of app.getAppMetrics()) {
        appMemory += (m.memory?.workingSetSize || 0) * 1024
        appCpu += m.cpu?.percentCPUUsage || 0
      }
    } catch {}

    return {
      platform: process.platform,
      osRelease: os.release(),
      arch: process.arch,
      cpuModel: os.cpus()[0]?.model || null,
      cpuCount: os.cpus().length,
      totalMemory: os.totalmem(),
      freeMemory: os.freemem(),
      diskFree,
      diskTotal,
      appMemory,
      appCpu,
      uptime: process.uptime(),
      electronVersion: process.versions.electron,
      chromeVersion: process.versions.chrome,
      appVersion: getAppVersion(),
      locale: app.getLocale(),
      packaged: app.isPackaged,
    }
  })

  // The running app's own version.
  ipcMain.handle("app:version", () => getAppVersion())
  ipcMain.handle("app:has-run-before", () => HAS_RUN_BEFORE)

  // Settings → Data → "Clear cache". Chromium's HTTP/image cache only.
  ipcMain.handle("app:clear-cache", async () => {
    try {
      const s = session.defaultSession
      const freed = await s.getCacheSize().catch(() => 0)
      await s.clearCache()
      return { ok: true, freed }
    } catch (e) {
      return { ok: false, error: (e as Error).message }
    }
  })

  // GPU acceleration is a launch-time Chromium switch, so the toggle in
  // Settings → General only takes effect on a fresh process.
  ipcMain.handle("app:relaunch", () => {
    app.relaunch()
    app.quit()
    return true
  })

  // "Test connection" behind Settings → Network. Any HTTP answer proves the
  // address resolves and something is listening.
  ipcMain.handle("workspace:test-endpoint", async (_e, url: string) => {
    let origin: string
    try {
      const parsed = new URL(String(url || "").trim())
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return { ok: false, error: "invalid-url" }
      }
      origin = parsed.origin
    } catch {
      return { ok: false, error: "invalid-url" }
    }

    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 8000)
    try {
      const res = await fetch(origin, { signal: ctrl.signal })
      return { ok: true, status: res.status }
    } catch (e) {
      const aborted = (e as Error)?.name === "AbortError"
      return { ok: false, error: aborted ? "timeout" : "unreachable" }
    } finally {
      clearTimeout(timer)
    }
  })

  // Only ever hand web URLs to the OS.
  ipcMain.handle("shell:open-external", (_e, url) => openExternalSafely(url))

  // ── Notifications ──
  ipcMain.handle("notifications:list", () => listNotifications())
  ipcMain.handle("notifications:push", (_e, input) => pushNotification(input))
  ipcMain.handle("notifications:mark-read", (_e, id: string) => {
    markRead(id)
    return true
  })
  ipcMain.handle("notifications:mark-all-read", () => {
    markAllRead()
    return true
  })
  ipcMain.handle("notifications:clear", (_e, id?: string) => {
    if (id) clearOneNotification(id)
    else clearAllNotifications()
    return true
  })
  ipcMain.handle("notifications:get-prefs", () => getNotifPrefs())
  ipcMain.handle("notifications:set-prefs", (_e, prefs) => setNotifPrefs(prefs))

  // Account + the embedded workspace view. Registered last and kept in its own
  // module: signing in gates the workspace half of the app and nothing else,
  // so none of the handlers above may depend on it.
  registerAccountIpc({
    endpoint: () => normalizeWorkspaceEndpoint(store.get("workspaceEndpoint")),
    getWindow: () => mainWindow,
    // The app's own look and feel, which the hosted workspace shares.
    appearance: () => ({
      theme: nativeTheme.themeSource as ThemeMode,
      language: getMainLanguage(),
    }),
    setAppearance: ({ theme, language }) => {
      // Told BY the workspace. The renderer owns both settings, so this is
      // relayed rather than applied here; it stores and re-broadcasts them.
      if (theme) nativeTheme.themeSource = theme
      if (language) setMainLanguage(language)
      mainWindow?.webContents.send("appearance:changed", { theme, language })
    },
  })
}

// Before anything waits on `app.whenReady()`: Chromium builds its scheme
// registry as it starts, and a privileged scheme registered after that is
// treated as opaque no matter what serves it. See workspace-bundle.ts.
registerWorkspaceScheme()

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on("second-instance", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.show()
      mainWindow.focus()
      return
    }
    createWindow()
  })
}

app.whenReady().then(async () => {
  // The bundled workspace app, served off disk over that scheme.
  serveWorkspaceBundle()

  installApplicationMenu()

  // The window frame is drawn by the OS, so the OS has to be told which way the
  // app is themed — otherwise a dark app keeps a light Windows title bar.
  applyThemeSource(store.get("themeMode"))
  setChromeSkin(null, store.get("skin"))

  nativeTheme.on("updated", () => refreshTitleBarOverlay(mainWindow))

  applyStartOnBoot()
  applyProxyFromSettings(store)

  // Restore the UI language before the tray is built or any startup
  // notification fires, so main's strings match the renderer from the first
  // frame instead of falling back to the OS locale until the renderer syncs.
  setMainLanguage(store.get("language"))

  setupIPC()
  setupAutoUpdater({
    getWindow: () => mainWindow,
    log: slog,
    isAutoUpdateEnabled: () => store.get("autoUpdate") !== false,
    feedUrlOverride: store.get("updateFeedUrl"),
    beforeInstall: async () => {},
    onDownloaded: (version) => {
      // A background auto-download finished. Make it discoverable: notify the
      // user and refresh the tray so "Restart to update" appears. The install
      // itself happens on the next quit, or immediately if the user restarts.
      updateTrayMenu()
      if (_lastUpdateNotifiedVersion === version) return
      _lastUpdateNotifiedVersion = version
      slog(`[updater] auto-update v${version} downloaded — ready to install`)
      try {
        // Only the newest package is installable, so retire the previous
        // version's prompt rather than stacking a second unread badge for an
        // update the user can no longer choose.
        clearNotificationsBySource("launcher-update")
        pushNotification({
          kind: "update_available",
          title: t("updateReadyTitle"),
          body: t("updateReadyBody", { version }),
          source: "launcher-update",
          // Clicking the toast (or the entry in the notification centre) has to
          // lead somewhere that can actually install: the renderer re-shows the
          // update banner and opens Settings → Updates off this payload.
          payload: { settingsSection: "updates" },
        })
      } catch {}
    },
  })
  createTray()

  if (!isHeadless) createWindow()
  markUiReached()

  setInterval(() => updateTrayMenu(), 5000)

  // App self-update: check shortly after launch and every half hour
  // thereafter. Surfaces a banner in the renderer; whether the download starts
  // by itself depends on the "Automatic updates" setting.
  const THIRTY_MIN = 30 * 60 * 1000
  let _lastUpdateCheck = 0
  const updateCheck = (minGapMs = 0): void => {
    const now = Date.now()
    if (minGapMs > 0 && now - _lastUpdateCheck < minGapMs) return
    _lastUpdateCheck = now
    void checkForUpdatesOnStartup().then((ok) => {
      if (!ok) _lastUpdateCheck = 0
    })
  }

  // The first check retries with a backoff instead of firing once and giving
  // up — a VPN brought up right after install shouldn't cost the next half
  // hour's worth of checks.
  const STARTUP_CHECK_DELAYS = [20_000, 60_000, 180_000, 600_000]
  const runStartupCheck = async (attempt = 0): Promise<void> => {
    const ok = await checkForUpdatesOnStartup().catch(() => false)
    if (ok) {
      _lastUpdateCheck = Date.now()
      return
    }
    const next = attempt + 1
    if (next < STARTUP_CHECK_DELAYS.length) {
      setTimeout(() => void runStartupCheck(next), STARTUP_CHECK_DELAYS[next])
    }
  }
  setTimeout(() => void runStartupCheck(), STARTUP_CHECK_DELAYS[0])
  setInterval(() => updateCheck(), THIRTY_MIN)
  // Also check whenever the user brings the window back to the foreground, so a
  // release published while they had it in the tray is discovered the moment
  // they look. Throttled to at most once per 10 min.
  app.on("browser-window-focus", () => updateCheck(10 * 60 * 1000))
}).catch(reportStartupError)

app.on("window-all-closed", () => {
  /* keep running in tray */
})

app.on("activate", () => {
  if (!isHeadless) createWindow()
})

app.on("before-quit", () => {
  ;(app as typeof app & { isQuitting: boolean }).isQuitting = true
})
