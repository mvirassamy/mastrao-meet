import { describe, expect, it } from 'vitest'
import { meetingDayBounds } from './meetingDayBounds'

describe('canonical local day bounds', () => {
  it('converts the selected October day into UTC midnight bounds', () => {
    expect(meetingDayBounds('2026-10-06', 'Europe/Paris')).toEqual({
      start: Date.parse('2026-10-05T22:00:00Z') / 1000,
      end: Date.parse('2026-10-06T22:00:00Z') / 1000,
    })
  })
  it.each([
    ['2026-03-29', 23],
    ['2026-10-25', 25],
  ])('handles the %s clock change (%s hours)', (day, hours) => {
    const bounds = meetingDayBounds(day, 'Europe/Paris')
    expect(bounds.end - bounds.start).toBe(Number(hours) * 3600)
  })
  it('handles a local midnight skipped by a clock change', () => {
    const bounds = meetingDayBounds('2018-11-04', 'America/Sao_Paulo')
    expect(bounds.start).toBe(Date.parse('2018-11-04T03:00:00Z') / 1000)
    expect(bounds.end - bounds.start).toBe(23 * 3600)
  })
  it('is independent of the browser zone when a user zone is provided', () => {
    expect(meetingDayBounds('2026-10-06', 'America/New_York')).toEqual({
      start: Date.parse('2026-10-06T04:00:00Z') / 1000,
      end: Date.parse('2026-10-07T04:00:00Z') / 1000,
    })
  })
})
