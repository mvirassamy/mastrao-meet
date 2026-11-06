import { describe, expect, it } from 'vitest'
import { getLiveTranscriptSpeakerLabel } from './liveTranscriptSpeakerLabel'

describe('getLiveTranscriptSpeakerLabel', () => {
  it('uses the resolved participant label for individual tracks', () => {
    expect(
      getLiveTranscriptSpeakerLabel({
        participantIdentity: 'alice',
        participantLabel: 'Alice Martin',
      })
    ).toBe('Alice Martin')
  })

  it('keeps the backend identity when the participant already left', () => {
    expect(
      getLiveTranscriptSpeakerLabel({
        participantIdentity: 'matthias',
      })
    ).toBe('matthias')
  })
})
