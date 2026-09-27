import { css } from '@/styled-system/css'

/** Body class for a settings AppDialog: the tabs handle their own scroll. */
export const settingsDialogBodyClass = css({
  display: 'flex',
  overflowY: 'hidden',
  paddingBottom: 0,
})
