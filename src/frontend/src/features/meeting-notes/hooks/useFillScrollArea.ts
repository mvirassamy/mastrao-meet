import { useLayoutEffect, useRef, useState } from 'react'

/** Below this, the page scrolls rather than squeeze the note further. */
const MIN_HEIGHT_PX = 128

/** The nearest ancestor that scrolls, or the document itself. */
function scrollArea(element: HTMLElement): HTMLElement {
  for (let node = element.parentElement; node; node = node.parentElement) {
    const overflow = getComputedStyle(node).overflowY
    if (overflow === 'auto' || overflow === 'scroll') return node
  }
  return document.documentElement
}

/** Where the scroll area's content ends, in viewport coordinates. */
function contentBottom(area: HTMLElement): number {
  const children = Array.from(area.children)
  const bottom = Math.max(
    ...children.map((child) => child.getBoundingClientRect().bottom)
  )
  return bottom + parseFloat(getComputedStyle(area).paddingBottom)
}

/**
 * The height that makes the element end exactly where the page must end for
 * its scroll area not to scroll: what sits above and below it stays as is,
 * and the element takes the rest of the screen.
 */
export function useFillScrollArea<T extends HTMLElement>(enabled: boolean) {
  const ref = useRef<T>(null)
  const [height, setHeight] = useState<number>()

  useLayoutEffect(() => {
    const element = ref.current
    if (!enabled || !element) return
    const area = scrollArea(element)
    const measure = () => {
      const box = element.getBoundingClientRect()
      const areaTop = area.getBoundingClientRect().top + area.clientTop
      const top = box.top - areaTop + area.scrollTop
      const after = contentBottom(area) - box.bottom
      setHeight(
        Math.max(MIN_HEIGHT_PX, Math.floor(area.clientHeight - top - after))
      )
    }
    measure()
    // What surrounds the note loads and changes after it: measure again
    // whenever the area or its content changes size.
    const observer = new ResizeObserver(measure)
    observer.observe(area)
    for (const child of Array.from(area.children)) observer.observe(child)
    return () => observer.disconnect()
  }, [enabled])

  return { ref, height }
}
