import { describe, expect, it } from 'vitest'
import {
  isMeetingInputValid,
  normalizeMeetingInput,
  parseMeetingInput,
} from './meetingJoinInput'

describe('meeting join input', () => {
  it('normalizes a compact legacy meeting code', () => {
    expect(normalizeMeetingInput(' ABCDEFGHIJ ')).toBe('abc-defg-hij')
    expect(isMeetingInputValid(' ABCDEFGHIJ ')).toBe(true)
  })

  it('extracts a room code from a local Meet URL', () => {
    expect(
      normalizeMeetingInput(`${window.location.origin}/abc-defg-hij/`)
    ).toBe('abc-defg-hij')
    expect(parseMeetingInput(`${window.location.origin}/abc-defg-hij/`)).toBe(
      'abc-defg-hij'
    )
  })

  it('rejects unrelated URLs and malformed codes', () => {
    expect(isMeetingInputValid('https://example.com/abc-defg-hij')).toBe(false)
    expect(isMeetingInputValid('not-a-room')).toBe(false)
    expect(parseMeetingInput('not-a-room')).toBeNull()
  })
})
