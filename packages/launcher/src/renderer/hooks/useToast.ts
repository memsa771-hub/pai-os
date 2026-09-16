import { useCallback } from 'react'
import { toast } from 'sonner'

export type ToastType = 'info' | 'success' | 'error' | 'warning'

// Errors and warnings tend to carry detail worth reading (a wrapped npm
// failure, a reason a connection was refused); four seconds is not enough to
// finish one. Successes are acknowledgements and can leave quickly.
const DURATION_MS: Record<ToastType, number> = {
  success: 4000,
  info: 4000,
  warning: 8000,
  error: 10000,
}

function fireToast(message: string, type: ToastType): void {
  toast[type](message, { duration: DURATION_MS[type] })
  // While the Workspace is on screen its native view covers this toast. Main
  // repeats it inside the view, and ignores it when no view is showing.
  window.api?.showWorkspaceNotice?.({ message, type })?.catch(() => {})
}

export function showGlobalToast(message: string, type: ToastType = 'info'): void {
  fireToast(message, type)
}

export function useToasts(): { showToast: (message: string, type?: ToastType) => void } {
  const showToast = useCallback((message: string, type: ToastType = 'info') => {
    fireToast(message, type)
  }, [])

  return { showToast }
}
