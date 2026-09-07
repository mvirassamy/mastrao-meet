import { css } from '@/styled-system/css'
import type { ConnectionTestStepStatus } from '../types'

/**
 * Panda extracts styles statically, so every status needs its own literal
 * `css()` call: `css({ backgroundColor: someVariable })` would emit nothing.
 */
export const statusSquareClass: Record<ConnectionTestStepStatus, string> = {
  pending: css({
    backgroundColor: 'transparent',
    border: '1px solid {colors.border}',
  }),
  running: css({
    backgroundColor: 'primary',
    animation: 'pulse_background 1.2s ease-in-out infinite',
  }),
  success: css({ backgroundColor: 'success-foreground' }),
  failed: css({ backgroundColor: 'destructive' }),
  skipped: css({ backgroundColor: 'border' }),
}

/** Colour is carried by the square; the label stays near-black except on failure. */
export const statusTextClass: Record<ConnectionTestStepStatus, string> = {
  pending: css({ color: 'muted-foreground' }),
  running: css({ color: 'foreground' }),
  success: css({ color: 'foreground' }),
  failed: css({ color: 'destructive', fontWeight: 'medium' }),
  skipped: css({ color: 'muted-foreground' }),
}
