const STEP_MINUTES = 15
const MINUTES_PER_DAY = 24 * 60
const TIME = /^([01]\d|2[0-3]):([0-5]\d)$/

export type TimeSlot = {
  /** "HH:MM" */
  time: string
  /** Minutes since the given start, or null when there is no start. */
  duration: number | null
}

const toMinutes = (time: string) => {
  const match = TIME.exec(time)
  return match ? Number(match[1]) * 60 + Number(match[2]) : null
}

const pad = (value: number) => String(value).padStart(2, '0')

const toTime = (minutes: number) =>
  `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`

/**
 * Quarter-hour slots of one day. After a valid start time, only the later
 * slots of the same day are offered, each with its duration.
 */
export const timeSlots = (start?: string): TimeSlot[] => {
  const startMinutes = start ? toMinutes(start) : null
  const first =
    startMinutes === null
      ? 0
      : (Math.floor(startMinutes / STEP_MINUTES) + 1) * STEP_MINUTES
  const slots: TimeSlot[] = []
  for (let minutes = first; minutes < MINUTES_PER_DAY; minutes += STEP_MINUTES)
    slots.push({
      time: toTime(minutes),
      duration: startMinutes === null ? null : minutes - startMinutes,
    })
  return slots
}

/**
 * Reads the usual ways of typing a time ("9", "9h", "9h30", "9:30") as
 * "HH:MM". Anything else is returned unchanged for the form to reject.
 */
export const normalizeTime = (value: string) => {
  const match = /^(\d{1,2})(?:\s*[:hH.]\s*(\d{2})?)?$/.exec(value.trim())
  if (!match) return value
  const hours = Number(match[1])
  const minutes = Number(match[2] ?? 0)
  if (hours > 23 || minutes > 59) return value
  return `${pad(hours)}:${pad(minutes)}`
}
