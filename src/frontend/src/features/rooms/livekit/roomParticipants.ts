import type { Participant } from 'livekit-client'

const ROOM_AUDIO_LIVE_WORKER_IDENTITY = /^room-audio-worker-live-[0-9a-f]{32}$/u

type IdentifiedParticipant = Pick<Participant, 'identity'>

type MeetingRoomParticipants = {
  localParticipant: IdentifiedParticipant
  remoteParticipants: ReadonlyMap<string, IdentifiedParticipant>
}

export const isRoomAudioLiveWorker = (
  participant: IdentifiedParticipant
): boolean => ROOM_AUDIO_LIVE_WORKER_IDENTITY.test(participant.identity)

export const meetingParticipants = <T extends IdentifiedParticipant>(
  participants: Iterable<T>
): T[] =>
  Array.from(participants).filter(
    (participant) => !isRoomAudioLiveWorker(participant)
  )

export const meetingParticipantCount = (
  room: MeetingRoomParticipants
): number => meetingParticipants(room.remoteParticipants.values()).length + 1
