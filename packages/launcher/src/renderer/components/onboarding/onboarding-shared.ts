/** The spotlight tour's own completion mark. */
export const TOUR_KEY = "guided_tour_completed"

export function shouldShowGuidedTour(): boolean {
  try {
    return localStorage.getItem(TOUR_KEY) !== "true"
  } catch {
    return false
  }
}

/** Stops the tour auto-running again — set whether it was finished or skipped. */
export function markGuidedTourSeen(): void {
  try {
    localStorage.setItem(TOUR_KEY, "true")
  } catch {}
}

/** Clears that mark, so the tour replays (e.g. from the sidebar "guide" button). */
export function resetGuidedTour(): void {
  try {
    localStorage.removeItem(TOUR_KEY)
  } catch {}
}
