import type { Participant, Room } from 'livekit-client'

export const getParticipantForTranscription = (
  room: Room,
  identity: string
): Participant | undefined => {
  if (room.localParticipant.identity === identity) return room.localParticipant
  return room.getParticipantByIdentity(identity)
}

export const getParticipantForTrack = (
  room: Room,
  trackSid: string,
  fallbackIdentity: string
): string => {
  if (trackSid) {
    const participants = [
      room.localParticipant,
      ...room.remoteParticipants.values(),
    ]
    const participant = participants.find((candidate) =>
      candidate.trackPublications.has(trackSid)
    )
    if (participant) return participant.identity
  }
  return fallbackIdentity
}
