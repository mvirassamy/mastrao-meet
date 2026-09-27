import { type ReactNode } from 'react'
import { css } from '@/styled-system/css'

/**
 * Action row of a dialog, as in the Mastrao application: right-aligned on
 * desktop, stacked with the primary action first on mobile. Put secondary
 * actions before the primary one.
 */
export const DialogActions = ({ children }: { children: ReactNode }) => (
  <div
    className={css({
      display: 'flex',
      flexDirection: { base: 'column-reverse', sm: 'row' },
      justifyContent: 'flex-end',
      gap: '0.5rem',
      marginTop: '1.25rem',
    })}
  >
    {children}
  </div>
)
