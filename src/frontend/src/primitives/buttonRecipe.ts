import { type RecipeVariantProps, cva } from '@/styled-system/css'
import type { SystemStyleObject } from '@/styled-system/types'

const primaryStyle = {
  backgroundColor: 'primary',
  color: 'primary-foreground',
  fontWeight: 'medium !important',
  borderColor: 'transparent',
  '&[data-hovered]:not([data-disabled])': {
    backgroundColor: 'primary.hover',
  },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'primary.active',
  },
  '&[data-selected]': {
    backgroundColor: 'primary',
    color: 'primary-foreground',
  },
} satisfies SystemStyleObject

const secondaryStyle = {
  backgroundColor: 'secondary',
  color: 'secondary-foreground',
  fontWeight: 'medium !important',
  borderColor: 'transparent',
  '&[data-hovered]:not([data-disabled])': {
    backgroundColor: 'accent',
  },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'accent',
  },
  '&[data-selected]': {
    backgroundColor: 'selected',
    color: 'selected-foreground',
  },
} satisfies SystemStyleObject

const outlineStyle = {
  backgroundColor: 'card',
  color: 'card-foreground',
  fontWeight: 'medium !important',
  borderColor: 'input',
  '&[data-hovered]:not([data-disabled])': {
    color: 'primary',
    borderColor: 'primary',
  },
  '&[data-pressed]:not([data-disabled])': {
    color: 'primary.active',
    borderColor: 'primary.active',
  },
  '&[data-selected]': {
    backgroundColor: 'selected',
    color: 'selected-foreground',
  },
} satisfies SystemStyleObject

const ghostStyle = {
  backgroundColor: 'transparent',
  color: 'foreground',
  fontWeight: 'medium !important',
  borderColor: 'transparent',
  '&[data-hovered]:not([data-disabled])': {
    backgroundColor: 'accent',
  },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'accent',
  },
  '&[data-selected]': {
    backgroundColor: 'selected',
    color: 'selected-foreground',
  },
} satisfies SystemStyleObject

const destructiveStyle = {
  backgroundColor: 'destructive',
  color: 'destructive-foreground',
  fontWeight: 'medium !important',
  borderColor: 'transparent',
  '&[data-hovered]:not([data-disabled])': {
    backgroundColor: 'destructive-hover',
  },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'destructive-active',
  },
  '&[data-selected]': {
    backgroundColor: 'destructive',
    color: 'destructive-foreground',
  },
} satisfies SystemStyleObject

const mediaStyle = {
  backgroundColor: 'media-overlay',
  color: 'media-overlay-foreground',
  fontWeight: 'medium !important',
  borderColor: 'media-overlay-foreground',
  '&[data-hovered]:not([data-disabled])': {
    backgroundColor: 'media-overlay',
  },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'media-overlay',
  },
  '&[data-selected]': {
    backgroundColor: 'media-overlay',
    color: 'media-overlay-foreground',
  },
} satisfies SystemStyleObject

const successStyle = {
  backgroundColor: 'success',
  color: 'success-foreground',
  fontWeight: 'medium !important',
  borderColor: 'transparent',
  '&[data-hovered]:not([data-disabled])': {
    backgroundColor: 'success',
  },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'success',
  },
  '&[data-selected]': {
    backgroundColor: 'success',
    color: 'success-foreground',
  },
} satisfies SystemStyleObject

const warningStyle = {
  '&[data-disabled]': { opacity: 1 },
  backgroundColor: 'warning',
  color: 'warning-foreground',
  fontWeight: 'medium !important',
  borderColor: 'transparent',
  '&[data-hovered]:not([data-disabled])': {
    backgroundColor: 'warning',
  },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'warning',
  },
  '&[data-selected]': {
    backgroundColor: 'warning',
    color: 'warning-foreground',
  },
} satisfies SystemStyleObject

// Legacy names remain adapters; new consumers choose the semantic variants.

export const buttonRecipe = cva({
  base: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    transition: 'background 200ms, outline 200ms, border-color 200ms',
    cursor: 'pointer',
    border: '1px solid transparent',
    '&[data-disabled]': {
      cursor: 'default',
      opacity: 0.55,
    },
    '&[data-focus-visible]': {
      outline: '2px solid',
      outlineColor: 'ring',
      outlineOffset: '2px',
    },
    gap: '0.5rem',
  },
  variants: {
    shape: {
      circle: {
        borderRadius: '50%!',
        width: 'var(--call-control-size)',
        height: 'var(--call-control-size)',
        minWidth: 'var(--call-control-size)',
        padding: '0!',
        flexShrink: 0,
      },
    },
    size: {
      default: {
        borderRadius: 'control',
        paddingX: '1',
        paddingY: '0.625',
        '--square-padding': '{spacing.0.625}',
      },
      sm: {
        borderRadius: 'control',
        paddingX: '0.5',
        paddingY: '0.25',
        '--square-padding': '{spacing.0.25}',
      },
      xs: {
        borderRadius: 'control',
        '--square-padding': '0',
      },
      compact: {
        borderRadius: 'control',
        paddingX: '0.5',
        paddingY: '0.625',
        '--square-padding': '{spacing.0.625}',
      },
    },
    square: {
      true: {
        paddingX: 'var(--square-padding)',
        paddingY: 'var(--square-padding)',
      },
    },
    round: {
      true: {
        borderRadius: '50%',
        paddingX: 'var(--square-padding)',
        paddingY: 'var(--square-padding)',
      },
    },
    variant: {
      primary: primaryStyle,
      default: primaryStyle,
      secondary: secondaryStyle,
      outline: outlineStyle,
      ghost: ghostStyle,
      destructive: destructiveStyle,
      link: { ...ghostStyle, color: 'primary', textDecoration: 'underline' },
      secondaryText: ghostStyle,
      tertiary: secondaryStyle,
      tertiaryText: ghostStyle,
      primaryDark: outlineStyle,
      secondaryDark: outlineStyle,
      primaryTextDark: ghostStyle,
      quaternaryText: ghostStyle,
      greyscale: ghostStyle,
      danger: destructiveStyle,
      error2: warningStyle,
      success: successStyle,
      text: { ...ghostStyle, color: 'primary' },
      whiteCircle: {
        ...mediaStyle,
        width: '56px',
        height: '56px',
        borderRadius: '100%',
      },
      errorCircle: {
        ...warningStyle,
        width: '56px',
        height: '56px',
        borderRadius: '100%',
      },
      bigSquare: {
        ...outlineStyle,
        width: '56px',
        height: '56px',
        borderRadius: 'control',
        padding: '0',
        flexShrink: 0,
        '&[data-selected]': {
          boxShadow:
            '0 0 0 3px token(colors.ring) inset, 0 0 0 5px token(colors.card) inset',
        },
      },
      permission: {
        position: 'relative',
        borderRadius: '100%',
        color: 'warning-foreground',
        backgroundColor: 'warning',
        width: 'fit-content',
        height: 'fit-content',
        padding: '0 !important',
        margin: '0 !important',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      },
    },
    invisible: {
      true: {
        borderColor: 'transparent!',
        backgroundColor: 'transparent!',
        '&[data-hovered]': {
          backgroundColor: 'transparent!',
          borderColor: 'colorPalette.active!',
        },
        '&[data-pressed]': {
          borderColor: 'currentcolor',
        },
        '&[data-disabled]': {
          color: 'disabled-foreground',
        },
      },
    },
    fullWidth: {
      true: {
        width: 'full',
      },
    },
    loading: {
      true: {},
    },
    // some toggle buttons make more sense without a "pushed button" style when selected because their content changes to mark the state
    shySelected: {
      true: {},
    },
    description: {
      true: {
        flexDirection: 'column',
        gap: '0.5rem',
        '& span': {
          fontSize: '13px',
          textAlign: 'center',
        },
      },
    },
    // if the button is next to other ones to make a "button group", tell where the button is to handle radius
    groupPosition: {
      left: {
        borderTopRightRadius: 0,
        borderBottomRightRadius: 0,
      },
      right: {
        borderTopLeftRadius: 0,
        borderBottomLeftRadius: 0,
        borderLeft: 0,
      },
      center: {
        borderRadius: 0,
      },
    },
  },
  compoundVariants: [
    {
      shape: 'circle',
      description: true,
      css: {
        width: 'auto',
        height: 'auto',
        borderRadius: 'control!',
        padding: '0.625rem!',
      },
    },
    {
      variant: 'primaryDark',
      shySelected: true,
      css: {
        '&[data-selected]': {
          backgroundColor: 'card',
          color: 'card-foreground',
        },
      },
    },
  ],
  defaultVariants: {
    size: 'default',
    variant: 'primary',
  },
})

export type ButtonRecipe = typeof buttonRecipe

export type ButtonRecipeProps = RecipeVariantProps<ButtonRecipe>
