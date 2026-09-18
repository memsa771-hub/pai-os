import { useState } from "react"
import { useTranslation } from "react-i18next"
import type { ToastType } from "@renderer/hooks/useToast"
import { formatBytes } from "@renderer/lib/format"
import { resetLocalPreferences } from "@renderer/lib/local-data"

interface SettingsIO {
  clearingCache: boolean
  clearCache: () => Promise<void>
  localResetOpen: boolean
  openLocalReset: () => void
  closeLocalReset: () => void
  performLocalReset: () => void
}

export function useSettingsIO(showToast: (msg: string, type?: ToastType) => void): SettingsIO {
  const { t } = useTranslation()
  const [clearingCache, setClearingCache] = useState(false)
  const [localResetOpen, setLocalResetOpen] = useState(false)

  const clearCache = async (): Promise<void> => {
    setClearingCache(true)
    try {
      const result = await window.api.clearAppCache()
      if (result.ok) {
        showToast(result.freed ? t("settings.toasts.cacheCleared", { size: formatBytes(result.freed) }) : t("settings.toasts.cacheAlreadyEmpty"), "success")
      } else {
        showToast(t("settings.toasts.cacheClearFailed", { error: result.error || "" }), "error")
      }
    } finally {
      setClearingCache(false)
    }
  }

  const performLocalReset = (): void => {
    resetLocalPreferences()
    setLocalResetOpen(false)
    showToast(t("settings.toasts.localDataReset"), "success")
  }

  return {
    clearingCache,
    clearCache,
    localResetOpen,
    openLocalReset: () => setLocalResetOpen(true),
    closeLocalReset: () => setLocalResetOpen(false),
    performLocalReset,
  }
}
