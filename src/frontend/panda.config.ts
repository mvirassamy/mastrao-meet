import pandaPreset from '@pandacss/preset-panda'
import {
  Config,
  Tokens,
  defineConfig,
  defineSemanticTokens,
  defineTextStyles,
  defineTokens,
} from '@pandacss/dev'

const spacing: Tokens['spacing'] = {
  0: { value: '0rem' },
  0.125: { value: '0.125rem' },
  0.25: { value: '0.25rem' },
  0.375: { value: '0.375rem' },
  0.5: { value: '0.5rem' },
  0.625: { value: '0.625rem' },
  0.75: { value: '0.75rem' },
  1: { value: '1rem' },
  1.25: { value: '1.25rem' },
  1.5: { value: '1.5rem' },
  1.75: { value: '1.75rem' },
  2: { value: '2rem' },
  2.25: { value: '2.25rem' },
  2.5: { value: '2.5rem' },
  2.75: { value: '2.75rem' },
  3: { value: '3rem' },
  3.5: { value: '3.5rem' },
  4: { value: '4rem' },
}

const config: Config = {
  preflight: true,
  include: ['./src/**/*.{js,jsx,ts,tsx}'],
  exclude: [],
  jsxFramework: 'react',
  outdir: 'src/styled-system',
  globalFontface: {},
  theme: {
    ...pandaPreset.theme,
    // media queries are defined in em so that zooming with text-only mode triggers breakpoints
    breakpoints: {
      xs: '22.6em', // 360px (we assume less than that are old/entry level mobile phones)
      xsm: '31.25em', // 500px,
      sm: '40em', // 640px
      md: '48em', // 768px
      lg: '64em', // 1024px
      xl: '80em', // 1280px
      '2xl': '96em', // 1536px
    },
    keyframes: {
      slide: {
        from: {
          transform: 'var(--origin)',
          opacity: 0,
        },
        to: {
          transform: 'translateY(0)',
          opacity: 1,
        },
      },
      fade: { from: { opacity: 0 }, to: { opacity: 1 } },
      pulse: {
        '0%': { boxShadow: '0 0 0 0 rgba(255, 255, 255, 0.7)' },
        '75%': { boxShadow: '0 0 0 30px rgba(255, 255, 255, 0)' },
        '100%': { boxShadow: '0 0 0 0 rgba(255, 255, 255, 0)' },
      },
      active_speaker: {
        '0%': { height: '25%' },
        '25%': { height: '45%' },
        '50%': { height: '20%' },
        '100%': { height: '55%' },
      },
      active_speaker_small: {
        '0%': { height: '20%' },
        '25%': { height: '25%' },
        '50%': { height: '18%' },
        '100%': { height: '25%' },
      },
      wave_hand: {
        '0%': { transform: 'rotate(0deg)' },
        '20%': { transform: 'rotate(-20deg)' },
        '80%': { transform: 'rotate(20deg)' },
        '100%': { transform: 'rotate(0)' },
      },
      pulse_background: {
        '0%': { opacity: '1' },
        '50%': { opacity: '0.65' },
        '100%': { opacity: '1' },
      },
      rotate: {
        '0%': {
          transform: 'rotate(0deg)',
        },
        '100%': {
          transform: 'rotate(360deg)',
        },
      },
      prixClipFix: {
        '0%': {
          clipPath: 'polygon(50% 50%, 0 0, 0 0, 0 0, 0 0, 0 0)',
        },
        '25%': {
          clipPath: 'polygon(50% 50%, 0 0, 100% 0, 100% 0, 100% 0, 100% 0)',
        },
        '50%': {
          clipPath:
            'polygon(50% 50%, 0 0, 100% 0, 100% 100%, 100% 100%, 100% 100%)',
        },
        '75%': {
          clipPath: 'polygon(50% 50%, 0 0, 100% 0, 100% 100%, 0 100%, 0 100%)',
        },
        '100%': {
          clipPath: 'polygon(50% 50%, 0 0, 100% 0, 100% 100%, 0 100%, 0 0)',
        },
      },
      overlayIn: {
        from: { opacity: 0 },
        to: { opacity: 0.6 },
      },
    },
    tokens: defineTokens({
      /* we take a few things from the panda preset but for now we clear out some stuff.
       * This way we'll only add the things we need step by step and prevent using lots of differents things.
       */
      ...pandaPreset.theme.tokens,
      colors: defineTokens.colors({
        ...pandaPreset.theme.tokens.colors,
      }),
      animations: {},
      blurs: {},
      /* just directly use values as tokens. This allows us to follow a specific design scale,
       * without having to remember what 'sm' or '2xl' actually means.
       *
       * see semanticTokens for tokens targeting specific usages
       */
      fonts: {
        sans: { value: 'var(--font-ui)' },
        serif: { value: 'var(--font-ui)' },
        mono: {
          value: [
            'Source Code Pro',
            'ui-monospace',
            'SFMono-Regular',
            'Menlo',
            'Monaco',
            'Consolas',
            '"Liberation Mono"',
            '"Courier New"',
            'monospace',
          ],
        },
      },
      fontSizes: {
        10: { value: '0.625rem' },
        12: { value: '0.75rem' },
        14: { value: '0.875rem' },
        16: { value: '1rem' },
        20: { value: '1.25rem' },
        24: { value: '1.5rem' },
        28: { value: '1.75rem' },
        32: { value: '2rem' },
        40: { value: '2.375rem' },
        48: { value: '3rem' },
        64: { value: '4rem' },
      },
      letterSpacings: {},
      shadows: {
        sm: {
          value: [
            '0 1px 3px 0 rgb(0 0 0 / 0.1)',
            '0 1px 2px -1px rgb(0 0 0 / 0.1)',
          ],
        },
      },
      lineHeights: {
        1: { value: '1' },
        1.25: { value: '1.25' },
        1.375: { value: '1.375' },
        1.5: { value: '1.5' },
        1.625: { value: '1.625' },
        2: { value: '2' },
      },
      radii: {
        control: { value: 'var(--radius-control)' },
        surface: { value: 'var(--radius-surface)' },
        dialog: { value: 'var(--radius-dialog)' },
        4: { value: '0.25rem' },
        6: { value: '0.375rem' },
        8: { value: '0.5rem' },
        16: { value: '1rem' },
        full: { value: '9999px' },
      },
      sizes: {
        ...spacing,
        full: { value: '100%' },
        min: { value: 'min-content' },
        max: { value: 'max-content' },
        fit: { value: 'fit-content' },
        // room layout
        'room-side-panel': { value: '360px' },
        'room-side-panel-margin': { value: '1.5rem' },
        'room-control-bar': { value: '80px' },
        'room-reaction-toolbar-height': { value: '42px' },
        'tooltip-spacing': { value: '8px' },
      },
      spacing,
    }),
    semanticTokens: defineSemanticTokens({
      colors: {
        background: {
          value: 'var(--background)',
        },
        foreground: {
          value: 'var(--foreground)',
        },
        card: {
          value: 'var(--card)',
        },
        'card-foreground': {
          value: 'var(--card-foreground)',
        },
        popover: {
          value: 'var(--popover)',
        },
        'popover-foreground': {
          value: 'var(--popover-foreground)',
        },
        muted: {
          value: 'var(--muted)',
        },
        'muted-foreground': {
          value: 'var(--muted-foreground)',
        },
        primary: {
          DEFAULT: {
            value: 'var(--primary)',
          },
          hover: {
            value: 'var(--primary-hover)',
          },
          active: {
            value: 'var(--primary-active)',
          },
          text: {
            value: 'var(--primary-foreground)',
          },
          warm: {
            value: 'var(--accent)',
          },
          subtle: {
            value: 'var(--info)',
          },
          'subtle-text': {
            value: 'var(--info-foreground)',
          },
        },
        'primary-foreground': {
          value: 'var(--primary-foreground)',
        },
        secondary: {
          value: 'var(--secondary)',
        },
        'secondary-foreground': {
          value: 'var(--secondary-foreground)',
        },
        accent: {
          value: 'var(--accent)',
        },
        'accent-foreground': {
          value: 'var(--accent-foreground)',
        },
        selected: {
          value: 'var(--selected)',
        },
        'selected-foreground': {
          value: 'var(--selected-foreground)',
        },
        border: {
          value: 'var(--border)',
        },
        input: {
          value: 'var(--input)',
        },
        ring: {
          value: 'var(--ring)',
        },
        destructive: {
          value: 'var(--destructive)',
        },
        'destructive-foreground': {
          value: 'var(--destructive-foreground)',
        },
        'media-surface': {
          value: 'var(--media-surface)',
        },
        'media-foreground': {
          value: 'var(--media-foreground)',
        },
        'media-overlay': {
          value: 'var(--media-overlay)',
        },
        'media-overlay-foreground': {
          value: 'var(--media-overlay-foreground)',
        },
        info: {
          value: 'var(--info)',
        },
        'info-foreground': {
          value: 'var(--info-foreground)',
        },
        warning: {
          DEFAULT: {
            value: 'var(--warning)',
          },
          hover: {
            value: 'var(--warning-foreground)',
          },
          active: {
            value: 'var(--warning-foreground)',
          },
          text: {
            value: 'var(--warning-foreground)',
          },
          subtle: {
            value: 'var(--warning)',
          },
          'subtle-text': {
            value: 'var(--warning-foreground)',
          },
        },
        'warning-foreground': {
          value: 'var(--warning-foreground)',
        },
        success: {
          DEFAULT: {
            value: 'var(--success)',
          },
          hover: {
            value: 'var(--success-foreground)',
          },
          active: {
            value: 'var(--success-foreground)',
          },
          text: {
            value: 'var(--success-foreground)',
          },
          subtle: {
            value: 'var(--success)',
          },
          'subtle-text': {
            value: 'var(--success-foreground)',
          },
        },
        'success-foreground': {
          value: 'var(--success-foreground)',
        },
        recording: {
          value: 'var(--recording)',
        },
        'recording-foreground': {
          value: 'var(--recording-foreground)',
        },
        overlay: {
          value: 'var(--overlay)',
        },
        'primary-hover': {
          value: 'var(--primary-hover)',
        },
        'primary-active': {
          value: 'var(--primary-active)',
        },
        'destructive-hover': {
          value: 'var(--destructive-hover)',
        },
        'destructive-active': {
          value: 'var(--destructive-active)',
        },
        disabled: {
          value: 'var(--disabled)',
        },
        'disabled-foreground': {
          value: 'var(--disabled-foreground)',
        },
        danger: {
          DEFAULT: {
            value: 'var(--destructive)',
          },
          hover: {
            value: 'var(--destructive-hover)',
          },
          active: {
            value: 'var(--destructive-active)',
          },
          text: {
            value: 'var(--destructive-foreground)',
          },
          subtle: {
            value: 'var(--recording)',
          },
          'subtle-text': {
            value: 'var(--recording-foreground)',
          },
        },
        default: {
          text: {
            value: 'var(--foreground)',
          },
          bg: {
            value: 'var(--background)',
          },
          subtle: {
            value: 'var(--muted)',
          },
          'subtle-text': {
            value: 'var(--muted-foreground)',
          },
        },
        box: {
          text: {
            value: 'var(--card-foreground)',
          },
          bg: {
            value: 'var(--card)',
          },
          border: {
            value: 'var(--border)',
          },
        },
        control: {
          DEFAULT: {
            value: 'var(--card)',
          },
          hover: {
            value: 'var(--accent)',
          },
          active: {
            value: 'var(--selected)',
          },
          text: {
            value: 'var(--card-foreground)',
          },
          border: {
            value: 'var(--input)',
          },
          subtle: {
            value: 'var(--muted-foreground)',
          },
        },
        alert: {
          DEFAULT: {
            value: 'var(--info-foreground)',
          },
          notification: {
            value: 'var(--recording-foreground)',
          },
        },
        focusRing: {
          value: 'var(--ring)',
        },
      },
      shadows: {
        box: { value: '{shadows.sm}' },
      },
      spacing: {
        boxPadding: {
          DEFAULT: { value: '{spacing.2}' },
          sm: { value: '{spacing.1}' },
          xs: { value: '{spacing.0.5}' },
        },
        boxMargin: {
          xs: { value: '{spacing.0.5}' },
          DEFAULT: { value: '{spacing.1}' },
          lg: { value: '{spacing.2}' },
        },
        paragraph: { value: '{spacing.0.5}' },
        heading: { value: '{spacing.1}' },
        gutter: { value: '{spacing.1}' },
        textfield: { value: '{spacing.1}' },
      },
    }),
    textStyles: defineTextStyles({
      display: {
        value: {
          fontSize: '3rem',
          lineHeight: '2rem',
          fontWeight: 700,
        },
      },
      h1: {
        value: {
          fontSize: '1.5rem',
          lineHeight: '2rem',
          fontWeight: 700,
        },
      },
      h2: {
        value: {
          fontSize: '1.25rem',
          lineHeight: '1.75rem',
          fontWeight: 700,
        },
      },
      h3: {
        value: {
          fontSize: '1.125rem',
          lineHeight: '1.75rem',
        },
      },
      body: {
        value: {
          fontSize: '1rem',
          lineHeight: '1.5',
        },
      },
      sm: {
        value: {
          fontSize: '0.875rem',
          lineHeight: '1.25rem',
        },
      },
      xs: {
        value: {
          fontSize: '0.825rem',
          lineHeight: '1.15rem',
        },
      },
      badge: {
        value: {
          fontSize: '0.75rem',
          lineHeight: '1rem',
        },
      },
    }),
  },
}

export default defineConfig(config)
