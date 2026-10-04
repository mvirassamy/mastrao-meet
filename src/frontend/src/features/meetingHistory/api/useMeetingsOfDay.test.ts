import { describe, expect, it } from 'vitest'
import type { MeetingHistoryItem } from './types'
import { meetingsOfDay, olderPagesMayHoldDay } from './useMeetingsOfDay'

const meeting = (id: string, startedAt: string) =>
  ({ id, startedAt: new Date(startedAt) }) as MeetingHistoryItem

// Most recent first, as returned by the history.
const items = [
  meeting('evening', '2026-10-03T22:30:00Z'),
  meeting('morning', '2026-10-03T08:00:00Z'),
  meeting('older', '2026-10-01T09:00:00Z'),
]

describe('meetings of a day', () => {
  it('keeps the meetings of the day in the user time zone', () => {
    expect(
      meetingsOfDay(items, '2026-10-03', 'Europe/Paris').map((item) => item.id)
    ).toEqual(['morning'])
    expect(
      meetingsOfDay(items, '2026-10-04', 'Europe/Paris').map((item) => item.id)
    ).toEqual(['evening'])
  })

  it('asks for older pages until the oldest meeting is before the day', () => {
    expect(olderPagesMayHoldDay([], '2026-10-03', 'UTC')).toBe(true)
    expect(olderPagesMayHoldDay(items, '2026-10-01', 'UTC')).toBe(true)
    expect(olderPagesMayHoldDay(items, '2026-10-02', 'UTC')).toBe(false)
    expect(olderPagesMayHoldDay(items, '2026-10-05', 'UTC')).toBe(false)
  })
})
