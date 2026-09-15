import { describe, it, expect, beforeEach } from "vitest"

import {
  markGuidedTourSeen,
  resetGuidedTour,
  shouldShowGuidedTour,
} from "./onboarding-shared"

describe("guided tour gating", () => {
  beforeEach(() => localStorage.clear())

  it("runs for a fresh install", () => {
    expect(shouldShowGuidedTour()).toBe(true)
  })

  it("stays away once seen", () => {
    markGuidedTourSeen()
    expect(shouldShowGuidedTour()).toBe(false)
  })

  it("comes back once reset", () => {
    markGuidedTourSeen()
    expect(shouldShowGuidedTour()).toBe(false)

    resetGuidedTour()
    expect(shouldShowGuidedTour()).toBe(true)
  })
})
