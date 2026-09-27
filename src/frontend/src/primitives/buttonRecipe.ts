import { type RecipeVariantProps, cva } from '@/styled-system/css'
import type { SystemStyleObject } from '@/styled-system/types'

/*
 * Mastrao button: a one-to-one port of the Platform `Button`
 * (src/components/ui/button.tsx): same variants, sizes and colours. Colours
 * come from the fixed --button-* palette (mastrao-theme.css) so a button looks
 * the same on every screen, room included.
 *
 * Only the call controls are Meet-specific: `shape="circle"`, `round` and
 * the media variants (`hangup`, `warning`, `whiteCircle`, `errorCircle`,
 * `bigSquare`, `permission`). They keep their own dimensions and icon
 * sizes (see `data-call-control` in Button.tsx).
 */

const mix = (color: string, percent: number, base = 'transparent') =>
  `color-mix(in srgb, var(${color}) ${percent}%, ${base})`

const hover = '&[data-hovered]:not([data-disabled])'
const expanded = '&[aria-expanded=true]'
const selected = '&[data-selected]'

// Toggle buttons (side panels, reactions…) need a visible "on" state.
const selectedState = {
  backgroundColor: 'var(--button-accent)',
  borderColor: 'transparent',
  color: 'var(--button-primary)',
} satisfies SystemStyleObject

const defaultStyle = {
  backgroundColor: 'var(--button-primary)',
  color: 'var(--button-primary-foreground)',
  // Darken on hover (a translucent fade would drop white text below 4.5:1).
  [hover]: { backgroundColor: mix('--button-primary', 90, '#08142e') },
  [selected]: {
    backgroundColor: 'var(--button-primary)',
    color: 'var(--button-primary-foreground)',
  },
} satisfies SystemStyleObject

const outlineRest = {
  borderColor: mix('--button-primary', 23, 'var(--button-background)'),
  backgroundColor: 'var(--button-background)',
  color: mix('--button-primary', 55, 'var(--button-ink-soft)'),
} satisfies SystemStyleObject

const outlineActive = {
  borderColor: mix('--button-primary', 40, 'var(--button-background)'),
  backgroundColor: mix('--button-accent', 65, 'var(--button-background)'),
} satisfies SystemStyleObject

const outlineStyle = {
  ...outlineRest,
  [hover]: outlineActive,
  [expanded]: outlineActive,
  [selected]: selectedState,
} satisfies SystemStyleObject

const secondaryStyle = {
  backgroundColor: 'var(--button-secondary)',
  color: 'var(--button-secondary-foreground)',
  [hover]: { backgroundColor: mix('--button-secondary', 80) },
  [expanded]: {
    backgroundColor: 'var(--button-secondary)',
    color: 'var(--button-secondary-foreground)',
  },
  [selected]: selectedState,
} satisfies SystemStyleObject

const ghostActive = {
  backgroundColor: 'var(--button-muted)',
  color: 'var(--button-foreground)',
} satisfies SystemStyleObject

const ghostStyle = {
  [hover]: ghostActive,
  [expanded]: ghostActive,
  [selected]: selectedState,
} satisfies SystemStyleObject

const destructiveStyle = {
  backgroundColor: mix('--button-destructive', 10),
  // Darker ink keeps the text >= 4.5:1 on the 10% and 20% tints (WCAG 1.4.3).
  color: 'var(--button-destructive-foreground)',
  [hover]: { backgroundColor: mix('--button-destructive', 20) },
  '&[data-focus-visible]': {
    borderColor: mix('--button-destructive', 40),
    boxShadow: `0 0 0 3px ${mix('--button-destructive', 20)}`,
  },
} satisfies SystemStyleObject

const invertStyle = {
  backgroundColor: 'var(--button-background)',
  color: 'var(--button-foreground)',
  [hover]: { backgroundColor: mix('--button-background', 90) },
} satisfies SystemStyleObject

const linkStyle = {
  color: 'var(--button-primary)',
  textUnderlineOffset: '4px',
  [hover]: { textDecoration: 'underline' },
} satisfies SystemStyleObject

// Call controls ------------------------------------------------------------

const hangupStyle = {
  backgroundColor: 'destructive',
  color: 'destructive-foreground',
  [hover]: { backgroundColor: 'destructive-hover' },
  '&[data-pressed]:not([data-disabled])': {
    backgroundColor: 'destructive-active',
  },
} satisfies SystemStyleObject

const warningStyle = {
  backgroundColor: 'var(--call-attention)',
  color: 'var(--call-attention-foreground)',
  borderColor: 'var(--call-attention-border)',
  [hover]: { backgroundColor: 'var(--call-attention-hover)' },
  '&[data-disabled]': { opacity: 1 },
  [selected]: {
    backgroundColor: 'var(--call-attention)',
    color: 'var(--call-attention-foreground)',
  },
} satisfies SystemStyleObject

const mediaStyle = {
  backgroundColor: 'media-overlay',
  color: 'media-overlay-foreground',
  borderColor: 'media-overlay-foreground',
  [selected]: {
    backgroundColor: 'media-overlay',
    color: 'media-overlay-foreground',
  },
} satisfies SystemStyleObject

const bigCircle = {
  width: '56px!',
  height: '56px!',
  padding: '0!',
  borderRadius: '100%!',
} satisfies SystemStyleObject

// Icons follow the Platform `[&_svg]:size-*` rule, except in call controls.
const icons = (size: string) => ({
  '&:not([data-call-control]) svg': { width: size, height: size },
})

export const buttonRecipe = cva({
  base: {
    display: 'inline-flex',
    flexShrink: 0,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '10px',
    border: '1px solid transparent',
    backgroundClip: 'padding-box',
    fontSize: '0.875rem',
    lineHeight: '1.25rem',
    fontWeight: 500,
    whiteSpace: 'nowrap',
    transition: 'all 150ms cubic-bezier(0.4, 0, 0.2, 1)',
    outline: 'none',
    userSelect: 'none',
    cursor: 'pointer',
    textDecoration: 'none',
    '&[data-focus-visible]': {
      borderColor: 'var(--button-ring)',
      boxShadow: `0 0 0 3px ${mix('--button-ring', 50)}`,
    },
    '&[data-pressed]:not([aria-haspopup])': {
      transform: 'translateY(1px)',
    },
    '&[data-disabled]': {
      pointerEvents: 'none',
      opacity: 0.5,
    },
    '&[aria-invalid=true]': {
      borderColor: 'var(--button-destructive)',
      boxShadow: `0 0 0 3px ${mix('--button-destructive', 20)}`,
    },
    '& svg': {
      pointerEvents: 'none',
      flexShrink: 0,
    },
  },
  variants: {
    variant: {
      default: defaultStyle,
      outline: outlineStyle,
      secondary: secondaryStyle,
      ghost: ghostStyle,
      destructive: destructiveStyle,
      invert: invertStyle,
      link: linkStyle,
      // Call controls only.
      hangup: hangupStyle,
      warning: warningStyle,
      whiteCircle: { ...mediaStyle, ...bigCircle },
      errorCircle: { ...warningStyle, ...bigCircle },
      bigSquare: {
        ...outlineStyle,
        width: '56px!',
        height: '56px!',
        padding: '0!',
        flexShrink: 0,
        [selected]: {
          boxShadow:
            '0 0 0 3px var(--button-ring) inset, 0 0 0 5px var(--button-background) inset',
        },
      },
      permission: {
        position: 'relative',
        borderRadius: '100%!',
        color: 'var(--call-attention-badge)',
        backgroundColor: 'transparent',
        // 24px hit area around the 22px badge; reset the size's min-height
        // so the badge never covers the control underneath.
        display: 'grid!',
        placeItems: 'center',
        width: '24px!',
        height: '24px!',
        minWidth: '0!',
        minHeight: '0!',
        padding: '0!',
        margin: '0!',
      },
    },
    size: {
      default: {
        minHeight: '38px',
        gap: '0.5rem',
        paddingX: '0.75rem',
        ...icons('16px'),
      },
      xs: {
        minHeight: '24px',
        gap: '0.25rem',
        borderRadius: '8px',
        paddingX: '0.5rem',
        fontSize: '0.75rem',
        lineHeight: '1rem',
        ...icons('12px'),
      },
      sm: {
        minHeight: '28px',
        gap: '0.25rem',
        borderRadius: '8px',
        paddingX: '0.625rem',
        fontSize: '0.8rem',
        ...icons('14px'),
      },
      lg: {
        minHeight: '36px',
        gap: '0.375rem',
        paddingX: '0.625rem',
        ...icons('16px'),
      },
      icon: { width: '32px', height: '32px', padding: 0, ...icons('16px') },
      'icon-xs': {
        width: '24px',
        height: '24px',
        padding: 0,
        borderRadius: '8px',
        ...icons('12px'),
      },
      'icon-sm': {
        width: '28px',
        height: '28px',
        padding: 0,
        borderRadius: '8px',
        ...icons('16px'),
      },
      'icon-lg': {
        width: '36px',
        height: '36px',
        padding: 0,
        ...icons('16px'),
      },
    },
    // Call controls: round buttons sized by --call-control-size.
    shape: {
      circle: {
        borderRadius: '50%!',
        width: 'var(--call-control-size)!',
        height: 'var(--call-control-size)!',
        minWidth: 'var(--call-control-size)',
        padding: '0!',
        flexShrink: 0,
        // Every call control icon has the same 22px optical size.
        '& svg': { width: '22px!', height: '22px!' },
      },
    },
    // Reaction buttons: round icon buttons.
    round: {
      // Square 36px hit area, so the 50% radius draws a circle.
      true: {
        borderRadius: '50%!',
        width: '36px!',
        height: '36px!',
        minWidth: '36px',
        padding: '0!',
      },
    },
    fullWidth: {
      true: { width: 'full' },
    },
    loading: {
      true: {},
    },
    // Toggles whose content already shows the state keep the resting style.
    shySelected: {
      true: {},
    },
    description: {
      true: {
        flexDirection: 'column',
        gap: '0.5rem',
        // Captions wrap inside their tile instead of overflowing it.
        whiteSpace: 'normal',
        textAlign: 'center',
        height: 'auto!',
        paddingY: '0.5rem',
        '& span': {
          fontSize: '13px',
          textAlign: 'center',
          maxWidth: '100%',
          overflowWrap: 'anywhere',
        },
      },
    },
    // Buttons grouped side by side share their inner edges.
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
        width: 'auto!',
        maxWidth: '100%',
        height: 'auto!',
        borderRadius: '10px!',
        padding: '0.625rem!',
      },
    },
    {
      variant: 'outline',
      shySelected: true,
      css: { [selected]: outlineRest },
    },
  ],
  defaultVariants: {
    size: 'default',
    variant: 'default',
  },
})

export type ButtonRecipe = typeof buttonRecipe

export type ButtonRecipeProps = RecipeVariantProps<ButtonRecipe>
