import i18n from "i18next"
import { initReactI18next } from "react-i18next"

const enModules = import.meta.glob("./locales/en/*.json", { eager: true })

function buildBundle(modules: Record<string, unknown>): Record<string, unknown> {
  const bundle: Record<string, unknown> = {}
  for (const path in modules) {
    const key = path.split("/").pop()!.replace(/\.json$/, "")
    const module = modules[path] as { default?: unknown }
    bundle[key] = module.default ?? module
  }
  return bundle
}

export const resources = { en: { translation: buildBundle(enModules) } } as const

void i18n.use(initReactI18next).init({
  resources,
  lng: "en",
  fallbackLng: "en",
  supportedLngs: ["en"],
  interpolation: { escapeValue: false },
  returnNull: false,
})

if (typeof document !== "undefined") document.documentElement.lang = "en"

export default i18n
