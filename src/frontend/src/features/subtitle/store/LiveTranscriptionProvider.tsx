import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from 'react'
import { useRoomContext } from '@livekit/components-react'
import { RoomEvent } from 'livekit-client'
import { useRoomData } from '@/features/rooms/livekit/hooks/useRoomData'
import { fetchSubtitleState } from '../api/fetchSubtitleState'
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

const getRoomId = (room: ReturnType<typeof useRoomContext>) =>
  room.name || 'unknown-room'

export const LiveTranscriptionProvider = ({
  children,
}: {
  children: ReactNode
}) => {
  const room = useRoomContext()
  const apiRoomData = useRoomData()
  const roomId = getRoomId(room)
  const subtitleRoomId = apiRoomData?.livekit?.room ?? roomId
  const subtitleToken = apiRoomData?.livekit?.token
  const subtitleRoomIdRef = useRef(subtitleRoomId)
  const subtitleTokenRef = useRef(subtitleToken)
  const subtitleStateRequestIdRef = useRef(0)
  subtitleRoomIdRef.current = subtitleRoomId
  subtitleTokenRef.current = subtitleToken
  const [state, dispatch] = useReducer(
    liveTranscriptionReducer,
    roomId,
    createLiveTranscriptionState
  )

  const syncSubtitleState = useCallback(async () => {
    const requestId = ++subtitleStateRequestIdRef.current
    const currentRoomId = subtitleRoomIdRef.current
    const currentToken = subtitleTokenRef.current

    if (!currentToken) {
      dispatch({ type: 'status', status: 'unknown' })
      return
    }

    try {
      const response = await fetchSubtitleState(currentRoomId, currentToken)
      if (requestId !== subtitleStateRequestIdRef.current) return
      dispatch({
        type: 'status',
        status: response.subtitle?.state ?? 'unknown',
      })
    } catch {
      if (requestId !== subtitleStateRequestIdRef.current) return
      dispatch({ type: 'status', status: 'unknown' })
    }
  }, [])

  useEffect(() => {
    subtitleStateRequestIdRef.current += 1
    dispatch({ type: 'reset', roomId })

    const handleReconnecting = () =>
      dispatch({ type: 'connection', status: 'reconnecting' })
    const handleReconnected = () => {
      dispatch({ type: 'connection', status: 'connected' })
      void syncSubtitleState()
    }
    const handleDisconnected = () => {
      subtitleStateRequestIdRef.current += 1
      dispatch({ type: 'reset', roomId })
      dispatch({ type: 'connection', status: 'disconnected' })
    }
    const ingestEvent = (event: LiveTranscriptionTransportEvent) => {
      if (event.type === 'status') subtitleStateRequestIdRef.current += 1
      dispatch({ type: 'ingest', event })
    }
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
          (event) => dispatch({ type: 'ingest', event })
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
  }, [room, roomId, syncSubtitleState])

  const hasSubtitleToken = Boolean(subtitleToken)
  useEffect(() => {
    void syncSubtitleState()
  }, [roomId, hasSubtitleToken, syncSubtitleState])

  const value = useMemo(
    () => ({ ...state, dispatch, syncSubtitleState }),
    [state, syncSubtitleState]
  )
  return (
    <LiveTranscriptionContext.Provider value={value}>
      {children}
    </LiveTranscriptionContext.Provider>
  )
}
