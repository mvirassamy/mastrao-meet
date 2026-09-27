import {
  Switch as RACSwitch,
  SwitchProps as RACSwitchProps,
} from 'react-aria-components'
import { styled } from '@/styled-system/jsx'
import { type StyledVariantProps } from '@/styled-system/types'
import { RiCheckFill, RiCloseFill } from '@remixicon/react'
import { useAppAppearance } from './useAppAppearance'

const StyledSwitch = styled(RACSwitch, {
  base: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.571rem',
    color: 'foreground',
    cursor: 'pointer',
    forcedColorAdjust: 'none',
    '& .indicator': {
      position: 'relative',
      width: '2.6rem',
      height: '1.563rem',
      border: '0.125rem solid',
      borderColor: 'primary',
      borderRadius: '1.143rem',
      transition: 'all 200ms, outline 200ms',
      _before: {
        willChange: 'transform',
        content: '""',
        display: 'block',
        margin: '0.125rem',
        width: '1.063rem',
        height: '1.063rem',
        borderRadius: '1.063rem',
        background: 'primary',
        transition: 'transform 200ms, background-color 200ms',
        transitionDelay: '0ms',
      },
    },
    '& .checkmark, & .cross': {
      position: 'absolute',
      top: 0,
      bottom: 0,
      width: '1.313rem', // knob width + 2 × knob margin
      display: 'grid',
      placeItems: 'center',
      pointerEvents: 'none',
      zIndex: 1,
      '& svg': {
        display: 'block',
        width: '0.875rem',
        height: '0.875rem',
      },
    },
    '& .checkmark': {
      right: 0,
      color: 'primary',
      opacity: 0,
    },
    '& .cross': {
      left: 0,
      color: 'primary-foreground',
      opacity: 1,
      transition: 'opacity 200ms',
      transitionDelay: '0ms',
    },
    '&[data-selected] .indicator': {
      borderColor: 'primary',
      background: 'primary',
      _before: {
        background: 'card',
        transform: 'translateX(100%)',
      },
    },
    '&[data-selected] .checkmark': {
      opacity: 1,
      transition: 'opacity 30ms',
      transitionDelay: '150ms',
    },
    '&[data-selected] .cross': {
      opacity: 0,
      transition: 'opacity 10ms',
      transitionDelay: '0ms',
    },
    '&[data-disabled]': {
      cursor: 'not-allowed',
    },
    '&[data-disabled] .indicator': {
      borderColor: 'accent',
      background: 'transparent',
      _before: {
        background: 'accent',
      },
    },
    '&[data-disabled] .cross': {
      color: 'primary',
    },
    '&[data-focus-visible] .indicator': {
      outline: '2px solid!',
      outlineColor: 'focusRing!',
      outlineOffset: '2px!',
    },
  },
  variants: {},
})

// Mastrao application switch: compact track, white thumb, no glyphs.
const AppStyledSwitch = styled(RACSwitch, {
  base: {
    display: 'flex',
    alignItems: 'center',
    alignSelf: 'center',
    gap: '0.5rem',
    color: 'foreground',
    cursor: 'pointer',
    forcedColorAdjust: 'none',
    '& .indicator': {
      position: 'relative',
      flexShrink: 0,
      boxSizing: 'border-box',
      width: '2rem',
      height: '1.125rem',
      border: '1px solid transparent',
      borderRadius: '999px',
      backgroundColor: 'input',
      boxShadow: '0 1px 2px rgb(0 0 0 / 0.05)',
      transition: 'background-color 150ms',
      _before: {
        content: '""',
        position: 'absolute',
        top: 0,
        left: 0,
        width: '1rem',
        height: '1rem',
        borderRadius: '999px',
        background: 'white',
        boxShadow: '0 1px 2px rgb(0 0 0 / 0.2)',
        transition: 'transform 150ms',
      },
    },
    '&[data-selected] .indicator': {
      backgroundColor: 'primary',
      _before: { transform: 'translateX(0.875rem)' },
    },
    '&[data-disabled]': { cursor: 'not-allowed', opacity: 0.5 },
    '&[data-focus-visible] .indicator': {
      outline: '2px solid token(colors.ring)',
      outlineOffset: '2px',
    },
    _motionReduce: {
      '& .indicator, & .indicator::before': { transition: 'none' },
    },
  },
})

export type SwitchProps = StyledVariantProps<typeof StyledSwitch> &
  RACSwitchProps

/**
 * Styled RAC Switch. Inside the authenticated workspace it takes the Mastrao
 * application look; rooms keep the original switch.
 */
export const Switch = ({ children, ...props }: SwitchProps) =>
  useAppAppearance() ? (
    <AppStyledSwitch {...props}>
      {(renderProps) => (
        <>
          <div className="indicator" />
          {typeof children === 'function' ? children(renderProps) : children}
        </>
      )}
    </AppStyledSwitch>
  ) : (
    <StyledSwitch {...props}>
      {(renderProps) => (
        <>
          <div className="indicator">
            <span className="checkmark" aria-hidden="true">
              <RiCheckFill />
            </span>
            <span className="cross" aria-hidden="true">
              <RiCloseFill />
            </span>
          </div>
          {typeof children === 'function' ? children(renderProps) : children}
        </>
      )}
    </StyledSwitch>
  )
