import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { css } from '@/styled-system/css'

type Point = { x: number; y: number }

// Watercolor tile size: large enough that darker patches stay sparse.
const TILE = { width: 1100, height: 619 }

// Four mirrored copies, so the texture continues across every tile edge.
const MIRRORS = [
  { scale: '1 1', x: 0, y: 0 },
  { scale: '-1 1', x: 2 * TILE.width, y: 0 },
  { scale: '1 -1', x: 0, y: 2 * TILE.height },
  { scale: '-1 -1', x: 2 * TILE.width, y: 2 * TILE.height },
]

/*
 * Watercolor ribbon of the Mastrao landing pages, winding behind every
 * child marked with data-watercolor-anchor. Decorative, wide screens only.
 */
export const WatercolorTrail = ({ children }: { children: ReactNode }) => {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const [trail, setTrail] = useState<{ path: string; height: number }>()
  const id = useId()

  useLayoutEffect(() => {
    const wrapper = wrapperRef.current
    if (!wrapper) return
    const measure = () => {
      const box = wrapper.getBoundingClientRect()
      const anchors = [
        ...wrapper.querySelectorAll('[data-watercolor-anchor]'),
      ].map((anchor) => {
        const rect = anchor.getBoundingClientRect()
        return {
          x: rect.left - box.left + rect.width / 2,
          y: rect.top - box.top + rect.height / 2,
          height: rect.height,
        }
      })
      if (anchors.length < 2) return setTrail(undefined)
      const first = anchors[0]
      const last = anchors[anchors.length - 1]
      // Starts from the lower edge of the first illustration and ends
      // behind the last one, so it never reaches the next section.
      setTrail({
        height: box.height,
        path: smoothPath([
          { x: first.x, y: first.y + first.height * 0.3 },
          ...anchors.slice(1, -1),
          { x: last.x, y: last.y + last.height * 0.1 },
        ]),
      })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(wrapper)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={wrapperRef} className={css({ position: 'relative' })}>
      {trail && (
        <svg
          aria-hidden="true"
          width="100%"
          height={trail.height}
          className={css({
            display: { base: 'none', lg: 'block' },
            position: 'absolute',
            inset: 0,
            overflow: 'visible',
            pointerEvents: 'none',
          })}
        >
          <defs>
            <pattern
              id={`${id}-paint`}
              patternUnits="userSpaceOnUse"
              width={2 * TILE.width}
              height={2 * TILE.height}
            >
              {MIRRORS.map((mirror) => (
                <image
                  key={mirror.scale}
                  href="/assets/home/aquarelle.webp"
                  width={TILE.width}
                  height={TILE.height}
                  preserveAspectRatio="none"
                  transform={`translate(${mirror.x} ${mirror.y}) scale(${mirror.scale})`}
                />
              ))}
            </pattern>
            {/* Irregular brush edges. */}
            <filter
              id={`${id}-edges`}
              x="-20%"
              y="-20%"
              width="140%"
              height="140%"
            >
              <feTurbulence
                type="fractalNoise"
                baseFrequency={0.018}
                numOctaves={3}
                seed={4}
                result="noise"
              />
              <feDisplacementMap
                in="SourceGraphic"
                in2="noise"
                scale={34}
                xChannelSelector="R"
                yChannelSelector="G"
              />
            </filter>
          </defs>
          <path
            d={trail.path}
            fill="none"
            stroke={`url(#${id}-paint)`}
            strokeWidth={170}
            strokeLinecap="round"
            filter={`url(#${id}-edges)`}
            opacity={0.6}
          />
        </svg>
      )}
      <div className={css({ position: 'relative' })}>{children}</div>
    </div>
  )
}

// One vertical S-curve between each pair of consecutive points.
const smoothPath = (points: Point[]) =>
  points.slice(1).reduce((path, point, index) => {
    const previous = points[index]
    const middle = (previous.y + point.y) / 2
    return `${path} C${previous.x},${middle} ${point.x},${middle} ${point.x},${point.y}`
  }, `M${points[0].x},${points[0].y}`)
