import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  type ReactNode,
} from 'react'
import { useRoomContext } from '@livekit/components-react'
import { RoomEvent } from 'livekit-client'
import {
  readLiveTranscriptionStream,
  parseLiveTranscriptionGapStream,
} from './liveTranscriptionContract'
import {
  createLiveTranscriptionState,
  liveTranscriptionReducer,
} from './liveTranscriptionReducer'
import { getParticipantForTrack } from './liveTranscriptionParticipants'
import {
  LIVE_TRANSCRIPTION_GAP_TOPIC,
  LIVE_TRANSCRIPTION_TOPIC,
  type LiveTranscriptionTextStreamReader,
  type LiveTranscriptionTransportEvent,
} from './liveTranscriptionTypes'
import { LiveTranscriptionContext } from './liveTranscriptionContext'
import { useLiveTranscriptSpeakers } from './useLiveTranscriptSpeakers'

const getRoomId = (room: ReturnType<typeof useRoomContext>) =>
  room.name || 'unknown-room'

export const LiveTranscriptionProvider = ({
  children,
}: {
  children: ReactNode
}) => {
  const room = useRoomContext()
  const roomId = getRoomId(room)
  const [state, dispatch] = useReducer(
    liveTranscriptionReducer,
    roomId,
    createLiveTranscriptionState
  )
  const speakers = useLiveTranscriptSpeakers(room, roomId, state.segments)
  const resolveSpeaker = useCallback(
    (identity: string) => speakers?.get(identity),
    [speakers]
  )

  useEffect(() => {
    dispatch({ type: 'reset', roomId })

    const handleReconnecting = () =>
      dispatch({ type: 'connection', status: 'reconnecting' })
    const handleReconnected = () =>
      dispatch({ type: 'connection', status: 'connected' })
    const handleDisconnected = () => {
      dispatch({ type: 'reset', roomId })
      dispatch({ type: 'connection', status: 'disconnected' })
    }
    const ingestEvent = (event: LiveTranscriptionTransportEvent) =>
      dispatch({ type: 'ingest', event })
    const handleTextStream = async (
      reader: LiveTranscriptionTextStreamReader,
      participantInfo: { identity: string }
    ) => {
      try {
        const events = await readLiveTranscriptionStream(
          reader,
          participantInfo.identity
        )
        events.forEach((event) => {
          if (event.type !== 'segments') {
            ingestEvent(event)
            return
          }
          dispatch({
            type: 'ingest',
            event: {
              type: 'segments',
              segments: event.segments.map((segment) => ({
                ...segment,
                participantIdentity: getParticipantForTrack(
                  room,
                  segment.trackSid,
                  segment.participantIdentity
                ),
              })),
            },
          })
        })
      } catch {
        dispatch({
          type: 'gap',
          gap: {
            id: `stream-error-${reader.info.id}`,
            reason: 'transcription-stream-error',
            receivedAt: Date.now(),
          },
        })
      }
    }
    const handleGapStream = async (
      reader: LiveTranscriptionTextStreamReader
    ) => {
      try {
        parseLiveTranscriptionGapStream(await reader.readAll()).forEach(
          ingestEvent
        )
      } catch {
        // A lost gap marker must not break the transcript itself.
      }
    }

    room.on(RoomEvent.Reconnecting, handleReconnecting)
    room.on(RoomEvent.Reconnected, handleReconnected)
    room.on(RoomEvent.Disconnected, handleDisconnected)
    room.registerTextStreamHandler(LIVE_TRANSCRIPTION_TOPIC, handleTextStream)
    room.registerTextStreamHandler(
      LIVE_TRANSCRIPTION_GAP_TOPIC,
      handleGapStream
    )

    return () => {
      room.off(RoomEvent.Reconnecting, handleReconnecting)
      room.off(RoomEvent.Reconnected, handleReconnected)
      room.off(RoomEvent.Disconnected, handleDisconnected)
      room.unregisterTextStreamHandler(LIVE_TRANSCRIPTION_TOPIC)
      room.unregisterTextStreamHandler(LIVE_TRANSCRIPTION_GAP_TOPIC)
    }
  }, [room, roomId])

  const value = useMemo(
    () => ({ ...state, dispatch, resolveSpeaker }),
    [state, resolveSpeaker]
  )
  return (
    <LiveTranscriptionContext.Provider value={value}>
      {children}
    </LiveTranscriptionContext.Provider>
  )
}
