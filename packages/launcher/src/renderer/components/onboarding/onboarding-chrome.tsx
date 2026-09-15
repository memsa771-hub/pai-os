import React from "react"

import { cn } from "@renderer/lib/utils"

/** Monospaced section marker with a rule running to the edge of the column. */
export function SectionLabel({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}): React.JSX.Element {
  return (
    <div className={cn("mb-4 flex items-center gap-3", className)}>
      <span className="font-mono text-2xs font-medium tracking-widest text-(--text-tertiary) uppercase">
        {children}
      </span>
      <span className="h-px flex-1 bg-(--border)" />
    </div>
  )
}
