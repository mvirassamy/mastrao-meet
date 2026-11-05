import { describe, expect, it } from 'vitest'
import { getLiveTranscriptSpeakerLabel } from './liveTranscriptSpeakerLabel'

describe('getLiveTranscriptSpeakerLabel', () => {
  it('uses the room-audio label when attribution is unresolved', () => {
    expect(
      getLiveTranscriptSpeakerLabel({
        segment: {
          participantIdentity: 'unknown-participant',
          trackSid: 'room-mix',
        },
        participantLabel: 'unknown-participant',
        roomAudioLabel: 'Meeting audio',
        unknownParticipantLabel: 'Unknown participant',
      })
    ).toBe('Meeting audio')
  })

  it('uses the resolved participant label for individual tracks', () => {
    expect(
      getLiveTranscriptSpeakerLabel({
        segment: { participantIdentity: 'alice', trackSid: 'track-alice' },
        participantLabel: 'Alice Martin',
        roomAudioLabel: 'Meeting audio',
        unknownParticipantLabel: 'Unknown participant',
      })
    ).toBe('Alice Martin')
  })

  it('uses the resolved participant label on the mixed room track', () => {
    expect(
      getLiveTranscriptSpeakerLabel({
        segment: { participantIdentity: 'matthias', trackSid: 'room-mix' },
        participantLabel: 'Matthias',
        roomAudioLabel: 'Meeting audio',
        unknownParticipantLabel: 'Unknown participant',
      })
    ).toBe('Matthias')
  })
})
