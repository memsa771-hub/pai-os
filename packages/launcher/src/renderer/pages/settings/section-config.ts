import { Cog, Info, Palette, ShieldCheck, type LucideIcon } from "lucide-react"

export type SectionId = "general" | "appearance" | "privacy" | "about"
export type SectionGroupId = "preferences" | "system"
export interface Section { id: SectionId; icon: LucideIcon }

export const SECTION_GROUPS: Array<{ id: SectionGroupId; sections: Section[] }> = [
  { id: "preferences", sections: [
    { id: "general", icon: Cog },
    { id: "appearance", icon: Palette },
  ] },
  { id: "system", sections: [
    { id: "privacy", icon: ShieldCheck },
    { id: "about", icon: Info },
  ] },
]

export const SECTIONS: Section[] = SECTION_GROUPS.flatMap((group) => group.sections)
export const RELATED: Partial<Record<SectionId, SectionId[]>> = {
  general: ["appearance", "privacy"],
  appearance: ["general", "privacy"],
  privacy: ["general", "about"],
  about: ["privacy", "general"],
}
