import { css } from '@/styled-system/css'

/**
 * On the meeting page the note grows with its text up to one screen, then
 * scrolls inside itself: the app header, the note toolbar and its footer
 * stay in view.
 */
export const notePageScroll = css({
  maxHeight: 'calc(100dvh - 12rem)',
  overflowY: 'auto',
  overscrollBehavior: 'contain',
})
