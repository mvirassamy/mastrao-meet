import { describe, expect, it } from 'vitest'
import { getLiveTranscriptSpeakerLabel } from './liveTranscriptSpeakerLabel'

describe('getLiveTranscriptSpeakerLabel', () => {
  it('keeps the room-audio label authoritative over technical identities', () => {
    expect(
      getLiveTranscriptSpeakerLabel({
        segment: { trackSid: 'room-mix' },
        participantLabel: 'unknown-participant',
        roomAudioLabel: 'Meeting audio',
        unknownParticipantLabel: 'Unknown participant',
      })
    ).toBe('Meeting audio')
  })

  it('uses the resolved participant label for individual tracks', () => {
    expect(
      getLiveTranscriptSpeakerLabel({
        segment: { trackSid: 'track-alice' },
        participantLabel: 'Alice Martin',
        roomAudioLabel: 'Meeting audio',
        unknownParticipantLabel: 'Unknown participant',
      })
    ).toBe('Alice Martin')
  })
})
