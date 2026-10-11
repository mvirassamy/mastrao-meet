import { describe, expect, it } from 'vitest'
import {
  participantHue,
  participantInitials,
  readRecordingTemplateConfig,
} from './recordingTemplateConfig'

describe('recording template configuration', () => {
  it('reads the LiveKit connection without exposing it elsewhere', () => {
    expect(
      readRecordingTemplateConfig('?url=wss%3A%2F%2Frtc.test&token=secret')
    ).toEqual({ serverUrl: 'wss://rtc.test', token: 'secret' })
  })

  it('refuses incomplete connection parameters', () => {
    expect(() => readRecordingTemplateConfig('?url=wss://rtc.test')).toThrow(
      'Missing LiveKit recording template connection parameters.'
    )
  })
})

describe('participant presentation', () => {
  it.each([
    ['Matthias Virassamy', 'MV'],
    ['Vanille', 'V'],
    ['  Martin   Dupont  ', 'MD'],
    ['', '?'],
  ])('builds stable initials for %j', (name, expected) => {
    expect(participantInitials(name)).toBe(expected)
  })

  it('keeps participant colours stable', () => {
    expect(participantHue('participant-1')).toBe(
      participantHue('participant-1')
    )
    expect(participantHue('participant-1')).not.toBe(
      participantHue('participant-2')
    )
  })
})
