import { useEffect, useState } from 'react'

const MINUTE_MS = 60_000

/** Current time, refreshed at each new minute and when the page is shown again. */
export const useCurrentMinute = () => {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let timeout: number
    const tick = () => {
      setNow(new Date())
      timeout = window.setTimeout(tick, MINUTE_MS - (Date.now() % MINUTE_MS))
    }
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') setNow(new Date())
    }

    timeout = window.setTimeout(tick, MINUTE_MS - (Date.now() % MINUTE_MS))
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => {
      window.clearTimeout(timeout)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [])

  return now
}
