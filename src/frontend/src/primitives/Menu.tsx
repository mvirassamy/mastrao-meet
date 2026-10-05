import { ReactNode } from 'react'
import { MenuTrigger } from 'react-aria-components'
import { StyledPopover } from './StyledPopover'
import { Box } from './Box'

export type MenuDensity = 'default' | 'app'

// Compact application menu frame. Passed through the `css` prop so it is
// merged with the Box recipe instead of competing with its radius and padding.
const appMenuFrame = {
  maxHeight: 'min(24rem, var(--available-height))',
  overflowY: 'auto',
  padding: '0.25rem',
  borderRadius: '8px',
} as const

/**
 * a Menu is a tuple of a trigger component (most usually a Button) that toggles menu items in a tooltip around the trigger
 */
export const Menu = ({
  children,
  variant = 'light',
  placement,
  density = 'default',
}: {
  children: [trigger: ReactNode, menu: ReactNode]
  variant?: 'dark' | 'light'
  placement?: 'bottom' | 'top' | 'left' | 'right'
  density?: MenuDensity
}) => {
  const [trigger, menu] = children
  return (
    <MenuTrigger>
      {trigger}
      <StyledPopover
        placement={placement}
        className={
          density === 'app' ? 'authenticated-meet-workspace' : undefined
        }
      >
        <Box
          size="sm"
          type="popover"
          variant={variant}
          css={density === 'app' ? appMenuFrame : undefined}
        >
          {menu}
        </Box>
      </StyledPopover>
    </MenuTrigger>
  )
}
