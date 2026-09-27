import {
  type RadioProps as RACRadioProps,
  Radio as RACRadio,
} from 'react-aria-components'
import { styled } from '@/styled-system/jsx'
import { type StyledVariantProps } from '@/styled-system/types'
import { useAppAppearance } from './useAppAppearance'

// styled taken from example at https://react-spectrum.adobe.com/react-aria/Checkbox.html and changed for round radios
const StyledRadio = styled(RACRadio, {
  base: {
    display: 'flex',
    alignItems: 'center',
    gap: 0.375,
    forcedColorAdjust: 'none',
    width: 'fit-content',
    '& .mt-Radio': {
      borderRadius: 'full',
      flexShrink: 0,
      width: '1.125rem',
      height: '1.125rem',
      border: '1px solid {colors.control.border}',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      transition: 'all 200ms',
    },
    '& .mt-Radio-check': {
      width: '0.5rem',
      height: '0.5rem',
      borderRadius: 'full',
      backgroundColor: 'transparent',
      transition: 'all 200ms',
    },
    '&[data-pressed] .mt-Radio': {
      borderColor: 'primary.active',
    },
    '&[data-focus-visible] .mt-Radio': {
      outline: '2px solid!',
      outlineColor: 'focusRing!',
      outlineOffset: '2px!',
    },
    '&[data-selected] .mt-Radio': {
      borderColor: 'primary',
    },
    '&[data-selected] .mt-Radio-check': {
      backgroundColor: 'primary',
    },
    '&[data-selected][data-pressed] .mt-Radio-check': {
      backgroundColor: 'primary.active',
    },
  },
  variants: {
    size: {
      sm: {
        base: {},
        '& .radio': {
          width: '1.125rem',
          height: '1.125rem',
        },
        '& svg': {
          width: '0.625rem',
          height: '0.625rem',
        },
      },
    },
    alignment: {
      top: {
        alignItems: 'start',
        '& .mt-Radio': {
          marginTop: '3px',
        },
      },
    },
  },
})

export type RadioProps = StyledVariantProps<typeof StyledRadio> & RACRadioProps

// Mastrao application radio: 16px ring, blue border and dot when selected.
const AppStyledRadio = styled(RACRadio, {
  base: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    width: 'fit-content',
    color: 'foreground',
    fontSize: '0.875rem',
    lineHeight: '1.25rem',
    cursor: 'pointer',
    forcedColorAdjust: 'none',
    '& .mt-Radio': {
      display: 'grid',
      placeItems: 'center',
      flexShrink: 0,
      width: '1rem',
      height: '1rem',
      border: '1px solid token(colors.input)',
      borderRadius: 'full',
      backgroundColor: 'card',
      boxShadow: '0 1px 2px rgb(0 0 0 / 0.05)',
      transition: 'border-color 150ms',
    },
    '& .mt-Radio-check': {
      width: '0.5rem',
      height: '0.5rem',
      borderRadius: 'full',
      backgroundColor: 'transparent',
      transition: 'background-color 150ms',
    },
    '&[data-hovered] .mt-Radio': { borderColor: 'primary' },
    '&[data-selected] .mt-Radio': { borderColor: 'primary' },
    '&[data-selected] .mt-Radio-check': { backgroundColor: 'primary' },
    '&[data-focus-visible] .mt-Radio': {
      outline: '2px solid token(colors.ring)',
      outlineOffset: '2px',
    },
    '&[data-disabled]': { cursor: 'not-allowed', opacity: 0.5 },
  },
  variants: {
    alignment: {
      top: {
        alignItems: 'start',
        '& .mt-Radio': { marginTop: '2px' },
      },
    },
  },
})

/**
 * Styled radio button.
 *
 * Used internally by RadioGroups in Fields.
 */
export const Radio = ({ children, ...props }: RadioProps) => {
  const isApp = useAppAppearance()
  if (isApp) {
    const { size: _size, ...appProps } = props
    void _size
    return (
      <AppStyledRadio {...appProps}>
        {(renderProps) => (
          <>
            <div className="mt-Radio" aria-hidden="true">
              <div className="mt-Radio-check" />
            </div>
            {typeof children === 'function' ? children(renderProps) : children}
          </>
        )}
      </AppStyledRadio>
    )
  }
  return (
    <StyledRadio {...props}>
      {(renderProps) => (
        <>
          <div className="mt-Radio" aria-hidden="true">
            <div className="mt-Radio-check" />
          </div>
          {typeof children === 'function' ? children(renderProps) : children}
        </>
      )}
    </StyledRadio>
  )
}
