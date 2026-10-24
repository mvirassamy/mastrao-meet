import { css } from '@/styled-system/css'

/**
 * On the meeting page the note takes the rest of the screen (see
 * useFillScrollArea) and scrolls inside itself, never carrying the page.
 */
export const notePageScroll = css({
  overflowY: 'auto',
  overscrollBehavior: 'contain',
})
