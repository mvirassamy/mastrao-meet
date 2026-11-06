import { describe, expect, it } from 'vitest'
import {
  isRoomAudioLiveWorker,
  meetingParticipantCount,
  meetingParticipants,
} from './roomParticipants'

const participant = (identity: string) => ({ identity })

describe('room participants', () => {
  const worker = participant(
    'room-audio-worker-live-0123456789abcdef0123456789abcdef'
  )

  it('recognizes only the exact live Room Audio worker identity', () => {
    expect(isRoomAudioLiveWorker(worker)).toBe(true)
    expect(
      isRoomAudioLiveWorker(participant('room-audio-worker-live-Martin'))
    ).toBe(false)
    expect(
      isRoomAudioLiveWorker(
        participant('room-audio-worker-post-0123456789abcdef0123456789abcdef')
      )
    ).toBe(false)
  })

  it('keeps meeting participants and removes the system worker', () => {
    const participants = [
      participant('guest-1'),
      worker,
      participant('guest-2'),
    ]

    expect(
      meetingParticipants(participants).map(({ identity }) => identity)
    ).toEqual(['guest-1', 'guest-2'])
  })

  it('does not count the system worker as a meeting participant', () => {
    const room = {
      localParticipant: participant('host'),
      remoteParticipants: new Map([
        ['guest-1', participant('guest-1')],
        [worker.identity, worker],
        ['guest-2', participant('guest-2')],
      ]),
    }

    expect(meetingParticipantCount(room)).toBe(3)
  })
})
