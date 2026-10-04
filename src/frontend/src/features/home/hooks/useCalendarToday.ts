import { useEffect, useRef, useState } from 'react'
import {
  isSameCalendarDay,
  todayInTimeZone,
} from '../utils/authenticatedHomeDate'

const CALENDAR_REFRESH_INTERVAL_MS = 60_000

/** Today in the user time zone, refreshed when the day changes. */
export const useCalendarToday = (timeZone: string) => {
  const [today, setToday] = useState(() => todayInTimeZone(timeZone))
  const todayRef = useRef(today)

  useEffect(() => {
    const refresh = () => {
      const next = todayInTimeZone(timeZone)
      if (isSameCalendarDay(todayRef.current, next)) return

      todayRef.current = next
      setToday(next)
    }

    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }

    const interval = window.setInterval(refresh, CALENDAR_REFRESH_INTERVAL_MS)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    refresh()

    return () => {
      window.clearInterval(interval)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [timeZone])

  return today
}
