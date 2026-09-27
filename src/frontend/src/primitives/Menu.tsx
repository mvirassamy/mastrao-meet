import { ReactNode } from 'react'
import { MenuTrigger } from 'react-aria-components'
import { StyledPopover } from './StyledPopover'
import { Box } from './Box'
import { css } from '@/styled-system/css'

export type MenuDensity = 'default' | 'app'

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
          className={
            density === 'app'
              ? css({
                  maxHeight: 'min(24rem, var(--available-height))',
                  overflowY: 'auto',
                  padding: '0.25rem',
                  borderRadius: '8px',
                })
              : undefined
          }
        >
          {menu}
        </Box>
      </StyledPopover>
    </MenuTrigger>
  )
}
