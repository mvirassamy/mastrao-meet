import type { ReactNode } from 'react'
import { css } from '@/styled-system/css'

/**
 * Cell drawn above a meeting link overlay so its tooltips can show. It only
 * forwards a mouse click to the meeting link; keyboard and screen reader
 * users reach the same link directly.
 */
export const RowOverlayCell = ({
  onOpen,
  children,
}: {
  onOpen: () => void
  children: ReactNode
}) => (
  // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
  <div
    onClick={onOpen}
    className={css({
      position: 'relative',
      zIndex: 1,
      display: 'flex',
      alignItems: 'center',
      gap: '0.625rem',
      cursor: 'pointer',
    })}
  >
    {children}
  </div>
)
