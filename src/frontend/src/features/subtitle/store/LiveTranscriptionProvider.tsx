import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from 'react'
import { useRoomContext } from '@livekit/components-react'
import { RoomEvent, type RoomEventCallbacks } from 'livekit-client'
import { useRoomData } from '@/features/rooms/livekit/hooks/useRoomData'
import { fetchSubtitleState } from '../api/fetchSubtitleState'
import {
  readLiveTranscriptionStream,
  parseLiveTranscriptionGapStream,
} from './liveTranscriptionContract'
import {
  isReliableBackendStatePacket,
  parseLiveTranscriptionStatePacket,
  parseLiveTranscriptionStateSnapshot,
} from './liveTranscriptionStateContract'
import {
  createLiveTranscriptionState,
  liveTranscriptionReducer,
} from './liveTranscriptionReducer'
import { getParticipantForTrack } from './liveTranscriptionParticipants'
import {
  LIVE_TRANSCRIPTION_GAP_TOPIC,
  LIVE_TRANSCRIPTION_STATE_TOPIC,
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
  const apiRoomData = useRoomData()
  const roomId = getRoomId(room)
  const subtitleRoomId = apiRoomData?.livekit?.room ?? roomId
  const subtitleToken = apiRoomData?.livekit?.token
  const subtitleRoomIdRef = useRef(subtitleRoomId)
  const subtitleTokenRef = useRef(subtitleToken)
  const subtitleStateRequestIdRef = useRef(0)
  const handledResyncRequestRef = useRef<string | null>(null)
  subtitleRoomIdRef.current = subtitleRoomId
  subtitleTokenRef.current = subtitleToken
  const [state, dispatch] = useReducer(
    liveTranscriptionReducer,
    roomId,
    createLiveTranscriptionState
  )

  const speakers = useLiveTranscriptSpeakers(
    room,
    subtitleRoomId,
    state.segments
  )
  const resolveSpeaker = useCallback(
    (identity: string) => speakers?.get(identity),
    [speakers]
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
      const snapshotResult = parseLiveTranscriptionStateSnapshot(
        response.subtitle
      )
      if (!snapshotResult.ok) {
        dispatch({ type: 'resync-failed' })
        return
      }
      dispatch({ type: 'snapshot', snapshot: snapshotResult.snapshot })
    } catch {
      if (requestId !== subtitleStateRequestIdRef.current) return
      dispatch({ type: 'resync-failed' })
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
          (event) => dispatch({ type: 'ingest', event })
        )
      } catch {
        // A lost gap marker must not break the transcript itself.
      }
    }
    const handleDataReceived = (
      ...args: Parameters<RoomEventCallbacks['dataReceived']>
    ) => {
      const [payload, participant, kind, topic] = args
      if (
        topic !== LIVE_TRANSCRIPTION_STATE_TOPIC ||
        !isReliableBackendStatePacket(participant, kind)
      ) {
        return
      }

      const result = parseLiveTranscriptionStatePacket(payload)
      if (!result.ok) {
        dispatch({ type: 'request-resync' })
        return
      }
      dispatch({ type: 'state-packet', packet: result.packet })
    }

    room.on(RoomEvent.Reconnecting, handleReconnecting)
    room.on(RoomEvent.Reconnected, handleReconnected)
    room.on(RoomEvent.Disconnected, handleDisconnected)
    room.on(RoomEvent.DataReceived, handleDataReceived)
    room.registerTextStreamHandler(LIVE_TRANSCRIPTION_TOPIC, handleTextStream)
    room.registerTextStreamHandler(
      LIVE_TRANSCRIPTION_GAP_TOPIC,
      handleGapStream
    )

    return () => {
      room.off(RoomEvent.Reconnecting, handleReconnecting)
      room.off(RoomEvent.Reconnected, handleReconnected)
      room.off(RoomEvent.Disconnected, handleDisconnected)
      room.off(RoomEvent.DataReceived, handleDataReceived)
      room.unregisterTextStreamHandler(LIVE_TRANSCRIPTION_TOPIC)
      room.unregisterTextStreamHandler(LIVE_TRANSCRIPTION_GAP_TOPIC)
    }
  }, [room, roomId, subtitleRoomId, syncSubtitleState])

  const hasSubtitleToken = Boolean(subtitleToken)
  useEffect(() => {
    void syncSubtitleState()
  }, [roomId, hasSubtitleToken, subtitleRoomId, syncSubtitleState])

  useEffect(() => {
    if (state.resyncStatus !== 'pending') return
    const requestKey = `${roomId}:${state.roomSid ?? 'unbound'}:${state.resyncRequestId}`
    if (handledResyncRequestRef.current === requestKey) return
    handledResyncRequestRef.current = requestKey
    void syncSubtitleState()
  }, [
    roomId,
    state.resyncRequestId,
    state.resyncStatus,
    state.roomSid,
    syncSubtitleState,
  ])

  const value = useMemo(
    () => ({ ...state, dispatch, syncSubtitleState, resolveSpeaker }),
    [state, syncSubtitleState, resolveSpeaker]
  )
  return (
    <LiveTranscriptionContext.Provider value={value}>
      {children}
    </LiveTranscriptionContext.Provider>
  )
}
