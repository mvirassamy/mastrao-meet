import type { Participant, Room } from 'livekit-client'

export const getParticipantForTranscription = (
  room: Room,
  identity: string
): Participant | undefined => {
  if (room.localParticipant.identity === identity) return room.localParticipant
  return room.getParticipantByIdentity(identity)
}
