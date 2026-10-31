import type { LiveTranscriptionSegment } from '../store/liveTranscriptionTypes'

const ROOM_MIX_TRACK_SID = 'room-mix'

export const getLiveTranscriptSpeakerLabel = ({
  segment,
  participantLabel,
  roomAudioLabel,
  unknownParticipantLabel,
}: {
  segment: Pick<LiveTranscriptionSegment, 'trackSid'>
  participantLabel?: string
  roomAudioLabel: string
  unknownParticipantLabel: string
}) => {
  if (segment.trackSid === ROOM_MIX_TRACK_SID) return roomAudioLabel
  if (participantLabel) return participantLabel
  return unknownParticipantLabel
}
