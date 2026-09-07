import { sva } from '@/styled-system/css'

export const menuRecipe = sva({
  slots: ['root', 'item'],
  base: {
    root: {},
    item: {
      paddingY: 0.125,
      paddingX: 0.5,
      textAlign: 'left',
      width: 'full',
      borderRadius: 'control',
      cursor: 'pointer',
      color: 'box.text',
      border: '1px solid transparent',
      position: 'relative',
      '&[data-selected]': {
        '&::before': {
          content: '"✓"',
          position: 'absolute',
          top: '2px',
          left: '6px',
        },
      },
      '&[data-focused]': {
        color: 'accent-foreground',
        backgroundColor: 'accent',
        outline: 'none!',
      },
      '&[data-hovered]': {
        color: 'accent-foreground',
        backgroundColor: 'accent',
        outline: 'none!',
      },
    },
  },
  variants: {
    variant: {
      light: {
        item: {
          '&[data-focused]': {
            backgroundColor: 'accent',
          },
          '&[data-hovered]': {
            backgroundColor: 'accent',
          },
        },
      },
      dark: {
        item: {
          color: 'popover-foreground',
        },
      },
    },
    extraPadding: {
      true: {
        item: {
          paddingLeft: 1.5,
        },
      },
    },
    icon: {
      true: {
        item: {
          display: 'flex',
          alignItems: 'center',
          gap: '1rem',
          paddingY: '0.4rem',
        },
      },
    },
  },
  defaultVariants: {
    variant: 'light',
  },
})
