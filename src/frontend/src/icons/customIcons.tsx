import { forwardRef, useId, type SVGProps } from 'react'

/*
 * Icons missing from Heroicons, drawn on the same 20px solid grid and in
 * currentColor so they sit naturally next to @heroicons/react/20/solid.
 * The microphone reuses the Heroicons shape (MIT, see heroicons-LICENSE.txt).
 */
type SvgProps = SVGProps<SVGSVGElement>

const base = {
  xmlns: 'http://www.w3.org/2000/svg',
  viewBox: '0 0 20 20',
  fill: 'currentColor',
  'aria-hidden': true,
  'data-slot': 'icon',
} as const

const SLASH =
  'M3.28 2.22a.75.75 0 0 0-1.06 1.06l14.5 14.5a.75.75 0 1 0 1.06-1.06L3.28 2.22Z'

export const MicrophoneSlash = forwardRef<SVGSVGElement, SvgProps>(
  (props, ref) => {
    const maskId = useId()
    return (
      <svg {...base} ref={ref} {...props}>
        <mask id={maskId} maskUnits="userSpaceOnUse">
          <rect width="20" height="20" fill="white" />
          <path
            d="M2 2.5 17.5 18"
            stroke="black"
            strokeWidth="3.5"
            strokeLinecap="round"
          />
        </mask>
        <g mask={`url(#${maskId})`}>
          <path d="M7 4a3 3 0 0 1 6 0v6a3 3 0 1 1-6 0V4Z" />
          <path d="M5.5 9.643a.75.75 0 0 0-1.5 0V10c0 3.06 2.29 5.585 5.25 5.954V17.5h-1.5a.75.75 0 0 0 0 1.5h4.5a.75.75 0 0 0 0-1.5h-1.5v-1.546A6.001 6.001 0 0 0 16 10v-.357a.75.75 0 0 0-1.5 0V10a4.5 4.5 0 0 1-9 0v-.357Z" />
        </g>
        <path d={SLASH} />
      </svg>
    )
  }
)
MicrophoneSlash.displayName = 'MicrophoneSlash'

const PIN =
  'M6.5 2.75A.75.75 0 0 1 7.25 2h5.5a.75.75 0 0 1 0 1.5H12.5v3.94l2.78 2.78a.75.75 0 0 1-.53 1.28h-4v5.75a.75.75 0 0 1-1.5 0V11.5h-4a.75.75 0 0 1-.53-1.28L7.5 7.44V3.5h-.25a.75.75 0 0 1-.75-.75Z'

export const Pin = forwardRef<SVGSVGElement, SvgProps>((props, ref) => (
  <svg {...base} ref={ref} {...props}>
    <path d={PIN} />
  </svg>
))
Pin.displayName = 'Pin'

export const PinSlash = forwardRef<SVGSVGElement, SvgProps>((props, ref) => {
  const maskId = useId()
  return (
    <svg {...base} ref={ref} {...props}>
      <mask id={maskId} maskUnits="userSpaceOnUse">
        <rect width="20" height="20" fill="white" />
        <path
          d="M2 2.5 17.5 18"
          stroke="black"
          strokeWidth="3.5"
          strokeLinecap="round"
        />
      </mask>
      <path d={PIN} mask={`url(#${maskId})`} />
      <path d={SLASH} />
    </svg>
  )
})
PinSlash.displayName = 'PinSlash'

export const Keyboard = forwardRef<SVGSVGElement, SvgProps>((props, ref) => (
  <svg {...base} ref={ref} {...props}>
    <path
      fillRule="evenodd"
      clipRule="evenodd"
      d="M3.5 4A2.5 2.5 0 0 0 1 6.5v7A2.5 2.5 0 0 0 3.5 16h13a2.5 2.5 0 0 0 2.5-2.5v-7A2.5 2.5 0 0 0 16.5 4h-13ZM4 7h1.5v1.5H4V7Zm3 0h1.5v1.5H7V7Zm3 0h1.5v1.5H10V7Zm3 0h1.5v1.5H13V7Zm-9 3h1.5v1.5H4V10Zm3 0h1.5v1.5H7V10Zm3 0h1.5v1.5H10V10Zm3 0h1.5v1.5H13V10Zm-6.5 2.75h7v1.25h-7v-1.25Z"
    />
  </svg>
))
Keyboard.displayName = 'Keyboard'

export const PictureInPicture = forwardRef<SVGSVGElement, SvgProps>(
  (props, ref) => (
    <svg {...base} ref={ref} {...props}>
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M3.5 3A2.5 2.5 0 0 0 1 5.5v9A2.5 2.5 0 0 0 3.5 17h13a2.5 2.5 0 0 0 2.5-2.5v-9A2.5 2.5 0 0 0 16.5 3h-13ZM2.5 5.5a1 1 0 0 1 1-1h13a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-9Z"
      />
      <rect x="10" y="9.5" width="6" height="4.5" rx="1" />
    </svg>
  )
)
PictureInPicture.displayName = 'PictureInPicture'

export const Record = forwardRef<SVGSVGElement, SvgProps>((props, ref) => (
  <svg {...base} ref={ref} {...props}>
    <path
      fillRule="evenodd"
      clipRule="evenodd"
      d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm0-1.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Z"
    />
    <circle cx="10" cy="10" r="3.75" />
  </svg>
))
Record.displayName = 'Record'

export const InfinityLoop = forwardRef<SVGSVGElement, SvgProps>(
  (props, ref) => (
    <svg {...base} ref={ref} {...props}>
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        d="M5.75 7a3 3 0 1 0 0 6c1.75 0 2.75-1.5 4.25-3s2.5-3 4.25-3a3 3 0 1 1 0 6c-1.75 0-2.75-1.5-4.25-3S7.5 7 5.75 7Z"
      />
    </svg>
  )
)
InfinityLoop.displayName = 'InfinityLoop'

export const Glasses = forwardRef<SVGSVGElement, SvgProps>((props, ref) => (
  <svg {...base} ref={ref} {...props}>
    <path
      fillRule="evenodd"
      clipRule="evenodd"
      d="M5.5 7a3.5 3.5 0 0 0-3.46 2.97l-.79-.4a.75.75 0 1 0-.67 1.34l1.41.71A3.5 3.5 0 0 0 8.97 11h2.06a3.5 3.5 0 0 0 6.98.62l1.41-.71a.75.75 0 1 0-.67-1.34l-.79.4A3.5 3.5 0 0 0 11.12 9.5H8.88A3.5 3.5 0 0 0 5.5 7Zm0 1.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm9 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z"
    />
  </svg>
))
Glasses.displayName = 'Glasses'

export const Goblet = forwardRef<SVGSVGElement, SvgProps>((props, ref) => (
  <svg {...base} ref={ref} {...props}>
    <path d="M5.25 2A.75.75 0 0 0 4.5 2.75V6a5.5 5.5 0 0 0 4.75 5.45v4.05h-2.5a.75.75 0 0 0 0 1.5h6.5a.75.75 0 0 0 0-1.5h-2.5v-4.05A5.5 5.5 0 0 0 15.5 6V2.75a.75.75 0 0 0-.75-.75h-9.5Z" />
  </svg>
))
Goblet.displayName = 'Goblet'
