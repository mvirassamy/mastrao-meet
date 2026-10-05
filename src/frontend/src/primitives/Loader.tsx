import { css } from '@/styled-system/css'

const ring = css({
  display: 'inline-block',
  flexShrink: 0,
  width: '24px',
  height: '24px',
  borderRadius: '50%',
  border: '2px solid color-mix(in srgb, currentColor 30%, transparent)',
  borderTopColor: 'currentColor',
  animation: 'rotate 0.8s linear infinite',
  _motionReduce: { animationDuration: '2.4s' },
})

/**
 * Thin spinning ring in the current text colour. Inside a Button it takes
 * the size of the button icons, which it replaces while loading.
 */
export const Loader = () => (
  <span data-loader aria-hidden="true" className={ring} />
)
