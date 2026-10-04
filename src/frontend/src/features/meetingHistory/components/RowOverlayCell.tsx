import type { MouseEvent, ReactNode, RefObject } from 'react'
import { css } from '@/styled-system/css'

const opensNewTab = (event: MouseEvent) =>
  event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1

/**
 * Cell drawn above a meeting link overlay so its tooltips can show. A mouse
 * click opens the meeting link, in a new tab with Cmd/Ctrl/Shift or the
 * middle button like a native link; keyboard and screen reader users reach
 * the same link directly.
 */
export const RowOverlayCell = ({
  linkRef,
  children,
}: {
  linkRef: RefObject<HTMLAnchorElement>
  children: ReactNode
}) => {
  const openLink = (event: MouseEvent) => {
    const link = linkRef.current
    if (!link) return
    if (opensNewTab(event)) window.open(link.href, '_blank', 'noopener')
    else link.click()
  }

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div
      onClick={openLink}
      onAuxClick={(event) => {
        if (event.button === 1) openLink(event)
      }}
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
}
