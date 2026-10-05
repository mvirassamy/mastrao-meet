import {
  addCalendarDays,
  calendarDateFromKey,
  calendarDayKey,
} from './authenticatedHomeDate'

/** First instant of a local calendar day, including clock changes at midnight. */
const firstInstantOfDay = (date: Date, format: Intl.DateTimeFormat) => {
  const target = calendarDayKey(date)
  let before = Math.floor(date.getTime() / 1000) - 36 * 60 * 60
  let after = before + 72 * 60 * 60
  // Calendar days are ordered, even when a local hour is skipped or repeated.
  while (before < after) {
    const middle = Math.floor((before + after) / 2)
    if (format.format(new Date(middle * 1000)) < target) before = middle + 1
    else after = middle
  }
  return before
}

/** UTC seconds from this local midnight to the next; never assume a 24-hour day. */
export const meetingDayBounds = (day: string, timeZone?: string) => {
  const date = calendarDateFromKey(day)
  if (!date) throw new Error('Invalid meeting day')
  const format = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  return {
    start: firstInstantOfDay(date, format),
    end: firstInstantOfDay(addCalendarDays(date, 1), format),
  }
}
