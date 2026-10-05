// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  validateMeetingSchedule,
  type MeetingScheduleDraft,
} from './meetingSchedule'

const draft: MeetingScheduleDraft = {
  title: '  Réunion équipe  ',
  date: '2026-10-07',
  startTime: '10:00',
  endTime: '11:00',
}
afterEach(() => vi.unstubAllEnvs())

describe('browser-local meeting schedule', () => {
  it('persists UTC seconds and the browser zone, trimming the optional title', () => {
    vi.stubEnv('TZ', 'Europe/Paris')
    expect(validateMeetingSchedule(draft)).toEqual({
      errors: {},
      schedule: {
        title: 'Réunion équipe',
        startsAt: Date.parse('2026-10-07T08:00:00Z') / 1000,
        endsAt: Date.parse('2026-10-07T09:00:00Z') / 1000,
        timeZone: 'Europe/Paris',
      },
    })
  })
  it('converts a different browser zone without a fixed Paris offset', () => {
    vi.stubEnv('TZ', 'America/New_York')
    const result = validateMeetingSchedule({ ...draft, title: '' })
    expect('schedule' in result && result.schedule.startsAt).toBe(
      Date.parse('2026-10-07T14:00:00Z') / 1000
    )
  })
  it.each(['', '2026-02-29', '2026-04-31', '2026-13-01'])(
    'rejects invalid date %s',
    (date) => {
      expect(validateMeetingSchedule({ ...draft, date })).toEqual({
        errors: { date: 'invalidDate' },
      })
    }
  )
  it('accepts a leap day', () => {
    expect(
      validateMeetingSchedule({ ...draft, date: '2028-02-29' })
    ).toHaveProperty('schedule')
  })
  it.each(['', '24:00', '12:60'])('rejects invalid time %s', (startTime) => {
    expect(validateMeetingSchedule({ ...draft, startTime })).toEqual({
      errors: { startTime: 'invalidTime' },
    })
  })
  it.each(['09:00', '10:00'])(
    'requires the end after the start (%s)',
    (endTime) => {
      expect(validateMeetingSchedule({ ...draft, endTime })).toEqual({
        errors: { endTime: 'endBeforeStart' },
      })
    }
  )
  it('rejects the spring clock gap instead of normalizing it', () => {
    vi.stubEnv('TZ', 'Europe/Paris')
    expect(
      validateMeetingSchedule({
        ...draft,
        date: '2026-03-29',
        startTime: '02:30',
        endTime: '04:00',
      })
    ).toEqual({ errors: { startTime: 'nonexistentTime' } })
  })
  it('rejects the repeated autumn hour instead of choosing an offset', () => {
    vi.stubEnv('TZ', 'Europe/Paris')
    expect(
      validateMeetingSchedule({
        ...draft,
        date: '2026-10-25',
        startTime: '02:30',
        endTime: '04:00',
      })
    ).toEqual({ errors: { startTime: 'ambiguousTime' } })
  })
  it('rejects a repeated half-hour in Lord Howe', () => {
    vi.stubEnv('TZ', 'Australia/Lord_Howe')
    expect(
      validateMeetingSchedule({
        ...draft,
        date: '2026-04-05',
        startTime: '01:45',
        endTime: '03:00',
      })
    ).toEqual({ errors: { startTime: 'ambiguousTime' } })
  })
})
