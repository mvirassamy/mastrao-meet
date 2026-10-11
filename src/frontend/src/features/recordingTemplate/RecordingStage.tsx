import { useEffect, useRef } from 'react'
import {
  VideoTrack,
  useRoomContext,
  useTracks,
} from '@livekit/components-react'
import {
  getTrackReferenceSource,
  isTrackReference,
  type TrackReference,
  type TrackReferenceOrPlaceholder,
} from '@livekit/components-core'
import { RoomEvent, Track } from 'livekit-client'
import { RecordingParticipantTile } from './RecordingParticipantTile'

const RECORDING_SOURCES = [
  { source: Track.Source.Camera, withPlaceholder: true },
  { source: Track.Source.ScreenShare, withPlaceholder: false },
]
const RECORDING_START_POLL_MS = 100
const AUDIO_ONLY_START_DELAY_MS = 500
const FRAME_DECODE_TIMEOUT_MS = 5_000

function RecordingLifecycle() {
  const room = useRoomContext()
  const recordingStarted = useRef(false)

  useEffect(() => {
    const startedAt = Date.now()
    const startRecording = () => {
      if (recordingStarted.current) return
      recordingStarted.current = true
      // LiveKit Egress consumes this exact browser-console signal.
      console.log('START_RECORDING')
    }
    const finishRecording = () => {
      if (!recordingStarted.current) return
      console.log('END_RECORDING')
    }
    const interval = window.setInterval(async () => {
      let hasVideoTrack = false
      let hasSubscribedTrack = false
      let hasDecodedFrame = false

      for (const participant of room.remoteParticipants.values()) {
        for (const publication of participant.trackPublications.values()) {
          if (publication.isSubscribed) hasSubscribedTrack = true
          if (publication.kind !== Track.Kind.Video) continue

          hasVideoTrack = true
          const stats = await publication.videoTrack?.getRTCStatsReport()
          if (!stats) continue

          hasDecodedFrame ||= Array.from(stats.values()).some(
            (item) =>
              item.type === 'inbound-rtp' &&
              'framesDecoded' in item &&
              Number(item.framesDecoded) > 0
          )
        }
      }

      const elapsed = Date.now() - startedAt
      const audioIsReady =
        !hasVideoTrack &&
        hasSubscribedTrack &&
        elapsed > AUDIO_ONLY_START_DELAY_MS
      const decodeTimedOut =
        hasSubscribedTrack && elapsed > FRAME_DECODE_TIMEOUT_MS

      if (hasDecodedFrame || audioIsReady || decodeTimedOut) {
        window.clearInterval(interval)
        startRecording()
      }
    }, RECORDING_START_POLL_MS)

    room.on(RoomEvent.Disconnected, finishRecording)
    return () => {
      window.clearInterval(interval)
      room.off(RoomEvent.Disconnected, finishRecording)
    }
  }, [room])

  return null
}

function ScreenShareStage({
  cameraTracks,
  screenShareTracks,
}: {
  cameraTracks: TrackReferenceOrPlaceholder[]
  screenShareTracks: TrackReference[]
}) {
  return (
    <main className="recording-stage recording-share-stage">
      <section className="recording-share-grid" aria-label="Partage d’écran">
        {screenShareTracks.map((track) => (
          <VideoTrack
            key={track.publication.trackSid}
            trackRef={track}
            className="recording-screen-share"
          />
        ))}
      </section>
      <section
        className="recording-participant-strip"
        aria-label="Participants"
      >
        {cameraTracks.map((track) => (
          <RecordingParticipantTile
            key={track.participant.identity}
            track={track}
          />
        ))}
      </section>
      <RecordingBrand />
    </main>
  )
}

function ParticipantGrid({
  cameraTracks,
}: {
  cameraTracks: TrackReferenceOrPlaceholder[]
}) {
  return (
    <main className="recording-stage recording-participant-grid">
      {cameraTracks.map((track) => (
        <RecordingParticipantTile
          key={track.participant.identity}
          track={track}
        />
      ))}
      <RecordingBrand />
    </main>
  )
}

function RecordingBrand() {
  return (
    <div className="recording-brand" aria-label="Mastrao">
      <img
        className="recording-brand-mark"
        src="/assets/mastrao-icon.svg"
        alt=""
      />
      <span>Mastrao</span>
    </div>
  )
}

export function RecordingStage() {
  const room = useRoomContext()
  const visualTracks = useTracks(RECORDING_SOURCES, {
    onlySubscribed: true,
  }).filter(
    (track) => track.participant.identity !== room.localParticipant.identity
  )

  const cameraTracks = visualTracks.filter(
    (track) => getTrackReferenceSource(track) === Track.Source.Camera
  )
  const screenShareTracks = visualTracks
    .filter(isTrackReference)
    .filter((track) => track.publication.source === Track.Source.ScreenShare)

  return (
    <>
      <RecordingLifecycle />
      {screenShareTracks.length > 0 ? (
        <ScreenShareStage
          cameraTracks={cameraTracks}
          screenShareTracks={screenShareTracks}
        />
      ) : (
        <ParticipantGrid cameraTracks={cameraTracks} />
      )}
    </>
  )
}
