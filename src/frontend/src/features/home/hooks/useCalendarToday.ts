import { useEffect, useRef, useState } from 'react'
import {
  isSameCalendarDay,
  todayInTimeZone,
} from '../utils/authenticatedHomeDate'

const CALENDAR_REFRESH_INTERVAL_MS = 60_000

export const useCalendarToday = (
  timeZone: string,
  onDayChange?: (previous: Date, next: Date) => void
) => {
  const [today, setToday] = useState(() => todayInTimeZone(timeZone))
  const todayRef = useRef(today)
  const onDayChangeRef = useRef(onDayChange)
  onDayChangeRef.current = onDayChange

  useEffect(() => {
    const refresh = () => {
      const next = todayInTimeZone(timeZone)
      const previous = todayRef.current
      if (isSameCalendarDay(previous, next)) return

      todayRef.current = next
      setToday(next)
      onDayChangeRef.current?.(previous, next)
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
