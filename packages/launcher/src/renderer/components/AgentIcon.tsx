import React from "react"
import { cn } from "../lib/utils"

// Placement AI does not install or brand third-party coding CLIs, so this is
// just a generic local-agent icon plus the one built-in agent type — not a
// tool catalog.
const BUNDLED_SLUGS = new Set(["default", "openclaw"])

interface AgentIconProps {
  type: string
  size?: number
  className?: string
}

export default function AgentIcon({
  type,
  size = 24,
  className,
}: AgentIconProps): React.JSX.Element {
  const slug = (type || "").toLowerCase().replace(/[^a-z0-9-]/g, "")
  const iconSlug = BUNDLED_SLUGS.has(slug) ? slug : "default"
  return (
    <img
      src={`icons/${iconSlug}.svg`}
      width={size}
      height={size}
      alt={type}
      className={cn("rounded-md shrink-0", className)}
    />
  )
}
