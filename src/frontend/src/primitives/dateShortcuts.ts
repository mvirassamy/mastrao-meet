import { type CalendarDate } from '@internationalized/date'

export type DateShortcutId = 'today' | 'tomorrow' | 'friday' | 'nextMonday'

export type DateShortcut = { id: DateShortcutId; date: CalendarDate }

// Same numbering as Date#getDay.
const MONDAY = 1
const FRIDAY = 5

/** First date strictly after `from` that falls on `weekday`. */
const nextWeekday = (from: CalendarDate, weekday: number) => {
  const current = from.toDate('UTC').getUTCDay()
  return from.add({ days: (weekday - current + 7) % 7 || 7 })
}

/**
 * Quick picks shown next to the calendar. A date already offered by an
 * earlier pick is not repeated, and no pick is earlier than `minDate`.
 */
export const dateShortcuts = (
  today: CalendarDate,
  minDate?: CalendarDate
): DateShortcut[] => {
  const candidates: DateShortcut[] = [
    { id: 'today', date: today },
    { id: 'tomorrow', date: today.add({ days: 1 }) },
    { id: 'friday', date: nextWeekday(today, FRIDAY) },
    { id: 'nextMonday', date: nextWeekday(today, MONDAY) },
  ]
  return candidates.filter(({ date }, index) => {
    const isFirstOffer =
      candidates.findIndex((other) => other.date.compare(date) === 0) === index
    const isAllowed = !minDate || date.compare(minDate) >= 0
    return isFirstOffer && isAllowed
  })
}
