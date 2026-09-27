import { describe, expect, it } from 'vitest'
import {
  formatMeetingDuration,
  formatMeetingTimeRange,
  formatTranscriptTimestamp,
  monthKey,
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
    expect(monthKey(start, 'Europe/Paris')).toBe('2026-10')
    expect(monthKey(start, 'UTC')).toBe('2026-09')
    expect(formatMeetingTimeRange(start, null, 'fr', 'Not/AZone')).toMatch(
      /\d{2}:\d{2}/
    )
  })
})
