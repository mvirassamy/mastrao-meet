import { css } from '@/styled-system/css'

/** A grey placeholder bar that pulses while content loads. */
export const skeletonBar = css({
  display: 'block',
  flexShrink: 0,
  borderRadius: '6px',
  backgroundColor: 'muted',
  animation: 'pulse_background 1.6s ease-in-out infinite',
  _motionReduce: { animation: 'none' },
})
