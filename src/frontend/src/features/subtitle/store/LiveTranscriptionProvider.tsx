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
  toLegacyTranscriptionEvent,
} from './liveTranscriptionContract'
import {
  createLiveTranscriptionState,
  liveTranscriptionReducer,
} from './liveTranscriptionReducer'
import {
  LIVE_TRANSCRIPTION_TOPIC,
  type LiveTranscriptionEventHandler,
  type LiveTranscriptionState,
  type LiveTranscriptionTextStreamReader,
} from './liveTranscriptionTypes'
import { LiveTranscriptionContext } from './liveTranscriptionContext'

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

  const handleLegacyTranscription = useCallback<LiveTranscriptionEventHandler>(
    (segments, participant, publication) => {
      dispatch({
        type: 'ingest',
        event: toLegacyTranscriptionEvent(segments, participant, publication),
      })
    },
    []
  )

  useEffect(() => {
    dispatch({ type: 'reset', roomId })

    const handleStatus = (status: LiveTranscriptionState['status']) =>
      dispatch({ type: 'status', status })
    const handleReconnecting = () => handleStatus('reconnecting')
    const handleReconnected = () => handleStatus('live')
    const handleDisconnected = () => dispatch({ type: 'reset', roomId })
    const handleTextStream = async (
      reader: LiveTranscriptionTextStreamReader,
      participantInfo: { identity: string }
    ) => {
      try {
        const events = await readLiveTranscriptionStream(
          reader,
          participantInfo.identity
        )
        events.forEach((event) => dispatch({ type: 'ingest', event }))
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

    room.on(RoomEvent.TranscriptionReceived, handleLegacyTranscription)
    room.on(RoomEvent.Reconnecting, handleReconnecting)
    room.on(RoomEvent.Reconnected, handleReconnected)
    room.on(RoomEvent.Disconnected, handleDisconnected)
    room.registerTextStreamHandler(LIVE_TRANSCRIPTION_TOPIC, handleTextStream)

    return () => {
      room.off(RoomEvent.TranscriptionReceived, handleLegacyTranscription)
      room.off(RoomEvent.Reconnecting, handleReconnecting)
      room.off(RoomEvent.Reconnected, handleReconnected)
      room.off(RoomEvent.Disconnected, handleDisconnected)
      room.unregisterTextStreamHandler(LIVE_TRANSCRIPTION_TOPIC)
    }
  }, [handleLegacyTranscription, room, roomId])

  const value = useMemo(() => ({ ...state, dispatch }), [state])
  return (
    <LiveTranscriptionContext.Provider value={value}>
      {children}
    </LiveTranscriptionContext.Provider>
  )
}
