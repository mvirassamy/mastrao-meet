import { css } from '@/styled-system/css'

/** Row of the device popover: selects then icon buttons, Mastrao density. */
export const devicePanel = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.375rem',
  maxWidth: 'calc(100vw - 2rem)',
})

/** One device select, sized so both selects fit side by side. */
export const deviceSelect = css({
  flex: '0 1 16.5rem',
  width: '16.5rem',
  minWidth: 0,
})
