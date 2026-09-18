import { useTranslation } from "react-i18next"
import { useShallow } from "zustand/react/shallow"
import { DEFAULT_SKIN, getSkin } from "../../../shared/skins"
import { useAppearanceStore } from "@renderer/store/appearance"
import { useThemeStore } from "@renderer/store/theme"
import type { SectionId } from "./section-config"
import type { SettingsValues } from "./use-settings-state"

interface Input { values: SettingsValues; launcherVersion: string }
const join = (...parts: Array<string | false | null | undefined>): string => parts.filter(Boolean).join(" · ")

export function useSectionSummaries({ values, launcherVersion }: Input): Record<SectionId, string> {
  const { t } = useTranslation()
  const mode = useThemeStore((state) => state.mode)
  const { accent, scale, skin } = useAppearanceStore(
    useShallow((state) => ({
      accent: state.accent,
      scale: state.scale,
      skin: state.skin,
    })),
  )
  const skinLocksAccent = getSkin(skin).lockedAccent !== null
  return {
    general: values.minimizeToTray ? t("settings.summary.trayOn") : t("settings.summary.trayOff"),
    appearance: join(
      t(`settings.appearance.modes.${mode}`),
      skinLocksAccent
        ? t(`settings.appearance.skins.${skin}.name`, { defaultValue: skin })
        : join(
            skin !== DEFAULT_SKIN &&
              t(`settings.appearance.skins.${skin}.name`, {
                defaultValue: skin,
              }),
            t("settings.summary.accent", {
              color: t(`settings.appearance.accents.${accent}`),
            }),
          ),
      t("settings.summary.scale", {
        size: t(`settings.appearance.scales.${scale}`),
      }),
    ),
    privacy: t("settings.pages.privacy.short"),
    about: join(t("settings.about.productName"), launcherVersion, values.autoUpdate ? t("settings.summary.autoUpdateOn") : t("settings.summary.autoUpdateOff")),
  }
}
