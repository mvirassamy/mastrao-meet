import { describe, expect, it, vi } from 'vitest'
import {
  addCalendarDays,
  calendarDateFromKey,
  calendarDayKey,
  isSameCalendarDay,
  startOfMondayWeek,
  todayInTimeZone,
} from './authenticatedHomeDate'

describe('authenticated home calendar dates', () => {
  it('reads a calendar day from the address and rejects invalid days', () => {
    const date = calendarDateFromKey('2026-10-01')
    expect(date && calendarDayKey(date)).toBe('2026-10-01')
    expect(calendarDateFromKey('2026-02-30')).toBeNull()
    expect(calendarDateFromKey('demain')).toBeNull()
    expect(calendarDateFromKey(null)).toBeNull()
  })

  it('starts the week on Monday across month boundaries', () => {
    const sunday = new Date(Date.UTC(2026, 9, 4, 12))
    expect(startOfMondayWeek(sunday).toISOString()).toBe(
      '2026-09-28T12:00:00.000Z'
    )
  })

  it('adds calendar days across year boundaries', () => {
    const newYearsEve = new Date(Date.UTC(2026, 11, 31, 12))
    expect(addCalendarDays(newYearsEve, 1).toISOString()).toBe(
      '2027-01-01T12:00:00.000Z'
    )
  })

  it('derives today from the user timezone', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-26T23:30:00.000Z'))

    const paris = todayInTimeZone('Europe/Paris')
    const newYork = todayInTimeZone('America/New_York')

    expect(paris.toISOString()).toBe('2026-09-27T12:00:00.000Z')
    expect(newYork.toISOString()).toBe('2026-09-26T12:00:00.000Z')
    expect(isSameCalendarDay(paris, newYork)).toBe(false)
    vi.useRealTimers()
  })
})
