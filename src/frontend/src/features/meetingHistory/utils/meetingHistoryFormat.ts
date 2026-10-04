const safeTimeZone = (timeZone?: string) => {
  if (!timeZone) return undefined
  try {
    new Intl.DateTimeFormat('en', { timeZone })
    return timeZone
  } catch {
    return undefined
  }
}

const dateFormat = (
  locale: string,
  timeZone: string | undefined,
  options: Intl.DateTimeFormatOptions
) =>
  new Intl.DateTimeFormat(locale, {
    ...options,
    timeZone: safeTimeZone(timeZone),
  })

const capitalize = (value: string, locale: string) =>
  value.charAt(0).toLocaleUpperCase(locale) + value.slice(1)

export const formatMeetingDay = (
  date: Date,
  locale: string,
  timeZone?: string
) =>
  capitalize(
    dateFormat(locale, timeZone, {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    }).format(date),
    locale
  )

export const formatMeetingTimeRange = (
  start: Date,
  end: Date | null,
  locale: string,
  timeZone?: string
) => {
  const format = dateFormat(locale, timeZone, {
    hour: '2-digit',
    minute: '2-digit',
  })
  return end && end.getTime() > start.getTime()
    ? format.formatRange(start, end)
    : format.format(start)
}

/** Calendar day of a date in the user's time zone, as YYYY-MM-DD. */
export const dayKey = (date: Date, timeZone?: string) =>
  dateFormat('en-CA', timeZone, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date)

const DAY_MS = 24 * 60 * 60 * 1000

export type DayParts = {
  /** Full heading: "Aujourd’hui, 4 oct.", "Jeu. 1 oct.". */
  label: string
  /** Day of the month shown large: "4". */
  day: string
  /** Short caption under it: "Aujourd’hui · oct.", "Jeu. · oct.". */
  caption: string
}

/**
 * Heading of a day of meetings, relative to today ("Aujourd’hui", "Hier")
 * then with the weekday, and with the year only outside the current one.
 */
export const formatDayParts = (
  date: Date,
  locale: string,
  timeZone?: string,
  now = new Date()
): DayParts => {
  const key = dayKey(date, timeZone)
  const todayKey = dayKey(now, timeZone)
  const year = key.slice(0, 4) === todayKey.slice(0, 4) ? undefined : 'numeric'
  const format = (options: Intl.DateTimeFormatOptions) =>
    dateFormat(locale, timeZone, options).format(date)
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  const month = format({ month: 'short', year })

  let relativeName: string | null = null
  if (key === todayKey) relativeName = relative.format(0, 'day')
  if (key === dayKey(new Date(now.getTime() - DAY_MS), timeZone))
    relativeName = relative.format(-1, 'day')

  const name = relativeName ?? format({ weekday: 'short' })
  const label = relativeName
    ? `${relativeName}, ${format({ day: 'numeric', month: 'short' })}`
    : format({ weekday: 'short', day: 'numeric', month: 'short', year })
  return {
    label: capitalize(label, locale),
    day: format({ day: 'numeric' }),
    caption: capitalize(`${name} · ${month}`, locale),
  }
}

export const formatMeetingDuration = (
  start: Date,
  end: Date | null,
  locale: string
) => {
  if (!end) return null
  const totalMinutes = Math.round((end.getTime() - start.getTime()) / 60_000)
  if (totalMinutes < 1) return null
  const unit = (value: number, name: 'hour' | 'minute') =>
    new Intl.NumberFormat(locale, {
      style: 'unit',
      unit: name,
      unitDisplay: 'short',
    }).format(value)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours === 0) return unit(minutes, 'minute')
  if (minutes === 0) return unit(hours, 'hour')
  return `${unit(hours, 'hour')} ${unit(minutes, 'minute')}`
}

export const formatTranscriptTimestamp = (milliseconds: number) => {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(minutes)}:${pad(seconds)}`
}
