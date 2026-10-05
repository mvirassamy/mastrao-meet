export type MeetingScheduleDraft = {
  title: string
  date: string
  startTime: string
  endTime: string
}

export type MeetingSchedule = {
  title: string
  startsAt: number
  endsAt: number
  timeZone: string
}

export type ScheduleIssue =
  | 'invalidDate'
  | 'invalidTime'
  | 'nonexistentTime'
  | 'ambiguousTime'
  | 'endBeforeStart'
  | 'pastTime'

export type ScheduleErrors = Partial<
  Record<'date' | 'startTime' | 'endTime', ScheduleIssue>
>

const DATE = /^\d{4}-\d{2}-\d{2}$/
const TIME = /^(?:[01]\d|2[0-3]):[0-5]\d$/

const pad = (value: number) => String(value).padStart(2, '0')

/** The current time as "HH:MM" when `date` is today in the browser. */
export const earliestTimeOn = (date: string, now = new Date()) => {
  const todayKey = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  if (date !== todayKey) return undefined
  return `${pad(now.getHours())}:${pad(now.getMinutes())}`
}

const validCalendarDate = (value: string) => {
  if (!DATE.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  )
}

const matchesLocalTime = (instant: Date, date: string, time: string) => {
  const [year, month, day] = date.split('-').map(Number)
  const [hour, minute] = time.split(':').map(Number)
  return (
    instant.getFullYear() === year &&
    instant.getMonth() === month - 1 &&
    instant.getDate() === day &&
    instant.getHours() === hour &&
    instant.getMinutes() === minute
  )
}

const localInstant = (
  date: string,
  time: string
): { instant: Date } | { issue: ScheduleIssue } => {
  if (!TIME.test(time)) return { issue: 'invalidTime' }
  const instant = new Date(`${date}T${time}:00`)
  if (!matchesLocalTime(instant, date, time))
    return { issue: 'nonexistentTime' }

  // Date chooses the earlier instant when clocks repeat. Reject that ambiguity
  // rather than persist a silently chosen UTC offset, including 30-minute shifts.
  const nextDay = new Date(instant)
  nextDay.setDate(nextDay.getDate() + 1)
  const repeatedMinutes =
    nextDay.getTimezoneOffset() - instant.getTimezoneOffset()
  if (repeatedMinutes > 0) {
    const later = new Date(instant.getTime() + repeatedMinutes * 60_000)
    if (matchesLocalTime(later, date, time)) return { issue: 'ambiguousTime' }
  }
  return { instant }
}

/** Convert browser-local form values into unambiguous UTC seconds. */
export const validateMeetingSchedule = (
  draft: MeetingScheduleDraft,
  now = new Date()
):
  | { schedule: MeetingSchedule; errors: ScheduleErrors }
  | { errors: ScheduleErrors } => {
  if (!validCalendarDate(draft.date)) return { errors: { date: 'invalidDate' } }
  const start = localInstant(draft.date, draft.startTime)
  const end = localInstant(draft.date, draft.endTime)
  const errors: ScheduleErrors = {}
  if ('issue' in start) errors.startTime = start.issue
  if ('issue' in end) errors.endTime = end.issue
  if ('issue' in start || 'issue' in end) return { errors }
  const currentMinute = new Date(now)
  currentMinute.setSeconds(0, 0)
  if (start.instant < currentMinute)
    return { errors: { startTime: 'pastTime' } }
  if (end.instant <= start.instant)
    return { errors: { endTime: 'endBeforeStart' } }
  return {
    schedule: {
      title: draft.title.trim(),
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      startsAt: start.instant.getTime() / 1000,
      endsAt: end.instant.getTime() / 1000,
    },
    errors,
  }
}
