import type { CSSProperties } from 'react'
import { VideoTrack, useIsSpeaking } from '@livekit/components-react'
import {
  isTrackReference,
  type TrackReferenceOrPlaceholder,
} from '@livekit/components-core'
import { participantHue, participantInitials } from './recordingTemplateConfig'

interface RecordingParticipantTileProps {
  track: TrackReferenceOrPlaceholder
}

export function RecordingParticipantTile({
  track,
}: RecordingParticipantTileProps) {
  const { participant } = track
  const isSpeaking = useIsSpeaking(participant)
  const displayName = participant.name?.trim() || participant.identity
  const hasCamera =
    isTrackReference(track) &&
    Boolean(track.publication.track) &&
    !track.publication.isMuted
  const style = {
    '--participant-hue': `${participantHue(participant.identity)}deg`,
  } as CSSProperties

  return (
    <article
      className="recording-participant-tile"
      data-speaking={isSpeaking}
      style={style}
    >
      {hasCamera && isTrackReference(track) ? (
        <VideoTrack trackRef={track} />
      ) : (
        <div className="recording-participant-avatar" aria-hidden="true">
          {participantInitials(displayName)}
        </div>
      )}
      <div className="recording-participant-name">{displayName}</div>
    </article>
  )
}
