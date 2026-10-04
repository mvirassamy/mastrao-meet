const DATE_PARTS_LOCALE = 'en-CA'

const toCalendarDate = (year: number, month: number, day: number) =>
  new Date(Date.UTC(year, month - 1, day, 12))

/** YYYY-MM-DD of a calendar date (calendar dates are stored at noon UTC). */
export const calendarDayKey = (date: Date) => date.toISOString().slice(0, 10)

export const todayInTimeZone = (timeZone?: string) => {
  const parts = new Intl.DateTimeFormat(DATE_PARTS_LOCALE, {
    timeZone: timeZone || undefined,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
  }).formatToParts(new Date())

  const part = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((item) => item.type === type)?.value)

  return toCalendarDate(part('year'), part('month'), part('day'))
}

export const addCalendarDays = (date: Date, days: number) => {
  const next = new Date(date)
  next.setUTCDate(next.getUTCDate() + days)
  return next
}

export const startOfMondayWeek = (date: Date) => {
  const day = date.getUTCDay()
  return addCalendarDays(date, -(day === 0 ? 6 : day - 1))
}

export const isSameCalendarDay = (left: Date, right: Date) =>
  left.getUTCFullYear() === right.getUTCFullYear() &&
  left.getUTCMonth() === right.getUTCMonth() &&
  left.getUTCDate() === right.getUTCDate()

export const formatCalendarDate = (
  date: Date,
  locale: string,
  options: Intl.DateTimeFormatOptions
) =>
  new Intl.DateTimeFormat(locale, {
    ...options,
    timeZone: 'UTC',
  }).format(date)
