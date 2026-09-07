import { useTrackMutedIndicator } from '@livekit/components-react'
import { type Participant, Track } from 'livekit-client'
import Source = Track.Source
import { RiMicOffFill } from '@remixicon/react'
import { css } from '@/styled-system/css'

export const MutedMicIndicator = ({
  participant,
}: {
  participant: Participant
}) => {
  const { isMuted } = useTrackMutedIndicator({
    participant: participant,
    source: Source.Microphone,
  })

  if (!isMuted && participant.isMicrophoneEnabled) {
    return null
  }

  return (
    <div
      className={css({
        backgroundColor: 'media-overlay',
        borderRadius: '4px',
        padding: 0.25,
      })}
    >
      <RiMicOffFill size={16} color="var(--media-overlay-foreground)" />
    </div>
  )
}
