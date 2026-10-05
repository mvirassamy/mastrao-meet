import { describe, expect, it } from 'vitest'
import { MEETING_HISTORY_PATH } from '../paths'
import { meetingBackPath, meetingOriginState } from './meetingOrigin'

describe('meeting origin', () => {
  it('returns to the home day the meeting was opened from', () => {
    expect(meetingBackPath(meetingOriginState('/', 'jour=2026-10-05'))).toBe(
      '/?jour=2026-10-05'
    )
  })

  it('returns to the history list it was opened from', () => {
    expect(meetingBackPath(meetingOriginState(MEETING_HISTORY_PATH, ''))).toBe(
      MEETING_HISTORY_PATH
    )
  })

  it.each([null, undefined, {}, { meetingBackTo: '//evil.example' }, 'x'])(
    'falls back to the history when opened directly: %s',
    (state) => {
      expect(meetingBackPath(state)).toBe(MEETING_HISTORY_PATH)
    }
  )
})
