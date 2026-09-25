import { useEffect, useRef } from 'react'

export type useLongPressProps = {
  keyCode?: string
  onKeyDown: () => void
  onKeyUp: () => void
  longPressThreshold?: number
  isDisabled?: boolean
}

export const useLongPress = ({
  keyCode,
  onKeyDown,
  onKeyUp,
  longPressThreshold = 300,
  isDisabled = false,
}: useLongPressProps) => {
  const timeoutIdRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const callbacksRef = useRef({ onKeyDown, onKeyUp })

  useEffect(() => {
    callbacksRef.current = { onKeyDown, onKeyUp }
  }, [onKeyDown, onKeyUp])

  useEffect(() => {
    if (isDisabled) {
      if (timeoutIdRef.current) {
        clearTimeout(timeoutIdRef.current)
        timeoutIdRef.current = null
      }
      return
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.code != keyCode || timeoutIdRef.current) return
      timeoutIdRef.current = setTimeout(() => {
        callbacksRef.current.onKeyDown()
      }, longPressThreshold)
    }

    const release = () => {
      if (!timeoutIdRef.current) return
      clearTimeout(timeoutIdRef.current)
      timeoutIdRef.current = null
      callbacksRef.current.onKeyUp()
    }

    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.code === keyCode) release()
    }

    if (!keyCode) return

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    window.addEventListener('blur', release)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
      window.removeEventListener('blur', release)
      release()
    }
  }, [keyCode, longPressThreshold, isDisabled])

  return
}

export default useLongPress
