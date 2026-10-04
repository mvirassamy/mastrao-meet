import { describe, expect, it } from 'vitest'
import {
  dayKey,
  formatDayLabel,
  formatMeetingDuration,
  formatMeetingTimeRange,
  formatTranscriptTimestamp,
} from './meetingHistoryFormat'

describe('meeting history formatting', () => {
  it('formats transcript timestamps', () => {
    expect(formatTranscriptTimestamp(4_000)).toBe('00:04')
    expect(formatTranscriptTimestamp(754_000)).toBe('12:34')
    expect(formatTranscriptTimestamp(3_725_000)).toBe('1:02:05')
  })

  it('formats durations only when the meeting has a real end', () => {
    const start = new Date('2026-09-20T10:00:00Z')
    expect(formatMeetingDuration(start, null, 'fr')).toBeNull()
    expect(formatMeetingDuration(start, start, 'fr')).toBeNull()
    expect(
      formatMeetingDuration(start, new Date('2026-09-20T10:45:00Z'), 'en')
    ).toBe('45 min')
    expect(
      formatMeetingDuration(start, new Date('2026-09-20T11:05:00Z'), 'en')
    ).toBe('1 hr 5 min')
  })

  it('uses the user time zone and tolerates an invalid one', () => {
    const start = new Date('2026-09-30T22:30:00Z')
    expect(dayKey(start, 'Europe/Paris')).toBe('2026-10-01')
    expect(dayKey(start, 'UTC')).toBe('2026-09-30')
    expect(formatMeetingTimeRange(start, null, 'fr', 'Not/AZone')).toMatch(
      /\d{2}:\d{2}/
    )
  })

  it('labels days relative to today, with the year only when it differs', () => {
    const now = new Date('2026-10-04T15:00:00Z')
    const label = (iso: string) =>
      formatDayLabel(new Date(iso), 'fr', 'Europe/Paris', now)
    expect(label('2026-10-04T08:00:00Z')).toBe('Aujourd’hui, 4 oct.')
    expect(label('2026-10-03T08:00:00Z')).toBe('Hier, 3 oct.')
    expect(label('2026-10-01T08:00:00Z')).toBe('Jeu. 1 oct.')
    expect(label('2025-12-30T08:00:00Z')).toBe('Mar. 30 déc. 2025')
  })
})
