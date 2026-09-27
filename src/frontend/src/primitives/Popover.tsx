import { ReactNode } from 'react'
import {
  DialogProps,
  DialogTrigger,
  Dialog,
  OverlayArrow,
} from 'react-aria-components'
import { styled } from '@/styled-system/jsx'
import { css } from '@/styled-system/css'
import { Box } from './Box'
import { StyledPopover } from './StyledPopover'

const StyledOverlayArrow = styled(OverlayArrow, {
  base: {
    display: 'block',
    fill: 'box.bg',
    stroke: 'box.border',
    strokeWidth: 1,
    '&[data-placement="bottom"] svg': {
      transform: 'rotate(180deg) translateY(-1px)',
    },
  },
  variants: {
    variant: {
      light: {},
      dark: {
        fill: 'popover',
        stroke: 'border',
      },
    },
  },
  defaultVariants: {
    variant: 'light',
  },
})

// Mastrao application density: compact frame with the workspace palette.
const appBox = css({
  padding: '0.375rem!',
  borderRadius: '12px!',
  border: '1px solid token(colors.border)!',
  boxShadow:
    '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)!',
})

/**
 * a Popover is a tuple of a trigger component (most usually a Button) that toggles some content in a tooltip around the trigger
 *
 * Note: to show a list of actionable items, like a dropdown menu, prefer using a <Menu> or <Select>.
 * This is here when needing to show unrestricted content in a box.
 */
export const Popover = ({
  children,
  variant = 'light',
  withArrow = true,
  density = 'default',
  ...dialogProps
}: {
  children: [
    trigger: ReactNode,
    popoverContent:
      | (({ close }: { close: () => void }) => ReactNode)
      | ReactNode,
  ]
  variant?: 'dark' | 'light'
  withArrow?: boolean
  density?: 'default' | 'app'
} & Omit<DialogProps, 'children'>) => {
  const [trigger, popoverContent] = children
  const isApp = density === 'app'
  return (
    <DialogTrigger>
      {trigger}
      <StyledPopover
        className={isApp ? 'authenticated-meet-workspace' : undefined}
      >
        {withArrow && (
          <StyledOverlayArrow variant={variant}>
            <svg width={12} height={12} viewBox="0 0 12 12">
              <path d="M0 0 L6 6 L12 0" />
            </svg>
          </StyledOverlayArrow>
        )}
        <Dialog {...dialogProps}>
          {({ close }) => (
            <Box
              size="sm"
              type="popover"
              variant={variant}
              className={isApp ? appBox : undefined}
            >
              {typeof popoverContent === 'function'
                ? popoverContent({ close })
                : popoverContent}
            </Box>
          )}
        </Dialog>
      </StyledPopover>
    </DialogTrigger>
  )
}
