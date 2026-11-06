export const getLiveTranscriptSpeakerLabel = ({
  participantIdentity,
  participantLabel,
}: {
  participantIdentity: string
  participantLabel?: string
}) => participantLabel || participantIdentity
