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

/**
 * Heading of a day of meetings: "Aujourd’hui, 4 oct.", "Hier, 3 oct.", then
 * "Jeu. 1 oct.", with the year only outside the current one.
 */
export const formatDayLabel = (
  date: Date,
  locale: string,
  timeZone?: string,
  now = new Date()
) => {
  const key = dayKey(date, timeZone)
  const shortDate = dateFormat(locale, timeZone, {
    day: 'numeric',
    month: 'short',
  }).format(date)
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (key === dayKey(now, timeZone))
    return capitalize(`${relative.format(0, 'day')}, ${shortDate}`, locale)
  if (key === dayKey(new Date(now.getTime() - DAY_MS), timeZone))
    return capitalize(`${relative.format(-1, 'day')}, ${shortDate}`, locale)

  const sameYear = key.slice(0, 4) === dayKey(now, timeZone).slice(0, 4)
  return capitalize(
    dateFormat(locale, timeZone, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      year: sameYear ? undefined : 'numeric',
    }).format(date),
    locale
  )
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
