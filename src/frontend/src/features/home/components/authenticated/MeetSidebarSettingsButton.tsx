import { SettingsButton } from '@/features/settings'
import { css } from '@/styled-system/css'

/**
 * Settings next to the profile card: a white square boxed like the card,
 * same height, so the footer reads as one row.
 */
export const MeetSidebarSettingsButton = ({
  collapsed = false,
}: {
  collapsed?: boolean
}) => (
  <SettingsButton
    dialogAppearance="app"
    buttonProps={{
      size: 'icon',
      className: css({
        width: collapsed ? '40px' : '48px',
        height: collapsed ? '40px' : '48px',
        minWidth: collapsed ? '40px' : '48px',
        minHeight: collapsed ? '40px' : '48px',
        flexShrink: 0,
        borderColor: 'border!',
        borderRadius: '8px',
        backgroundColor: 'card!',
        '&[data-hovered]': { backgroundColor: 'accent!' },
      }),
    }}
  />
)
