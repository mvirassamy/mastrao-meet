import { act, cleanup, fireEvent, render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataPacket_Kind, RoomEvent } from 'livekit-client'
import { LiveTranscriptionProvider } from './LiveTranscriptionProvider'
import { useLiveTranscription } from './liveTranscriptionContext'
import { LIVE_TRANSCRIPTION_STATE_TOPIC } from './liveTranscriptionTypes'

const {
  fetchSubtitleStateMock,
  startSubtitleMock,
  useRoomContextMock,
  useRoomDataMock,
} = vi.hoisted(() => ({
  fetchSubtitleStateMock: vi.fn(),
  startSubtitleMock: vi.fn(),
  useRoomContextMock: vi.fn(),
  useRoomDataMock: vi.fn(),
}))

vi.mock('../api/startSubtitle', () => ({
  useStartSubtitle: () => ({
    mutateAsync: startSubtitleMock,
    status: 'idle',
  }),
}))

vi.mock('../api/fetchSubtitleState', () => ({
  fetchSubtitleState: fetchSubtitleStateMock,
}))

vi.mock('@livekit/components-react', () => ({
  useRoomContext: useRoomContextMock,
}))

vi.mock('@/features/rooms/livekit/hooks/useRoomData', () => ({
  useRoomData: useRoomDataMock,
}))

const createRoom = () => {
  const listeners = new Map<string, (...args: unknown[]) => void>()
  const room = {
    name: 'room-1',
    localParticipant: { identity: 'local', trackPublications: new Map() },
    remoteParticipants: new Map([
      [
        'alice',
        {
          identity: 'alice',
          trackPublications: new Map([['TR_alice', {}]]),
        },
      ],
    ]),
    getParticipantByIdentity: vi.fn(),
    on: vi.fn((event: string, callback: (...args: unknown[]) => void) => {
      listeners.set(event, callback)
    }),
    off: vi.fn((event: string) => {
      listeners.delete(event)
    }),
    registerTextStreamHandler: vi.fn(),
    unregisterTextStreamHandler: vi.fn(),
  }
  return {
    room,
    emit: (event: string, ...args: unknown[]) =>
      listeners.get(event)?.(...args),
  }
}

const Probe = () => {
  const { ensureSubtitleStarted, segments, status, resyncStatus } =
    useLiveTranscription()
  return (
    <>
      <button type="button" onClick={() => void ensureSubtitleStarted()}>
        Start
      </button>
      <output data-testid="transcription-state">
        {status}:
        {segments
          .map(
            ({ participantIdentity, text }) => `${participantIdentity}:${text}`
          )
          .join('|')}
      </output>
      <output data-testid="transcription-resync">{resyncStatus}</output>
    </>
  )
}

const App = ({ children }: { children?: ReactNode }) => (
  <LiveTranscriptionProvider>{children}</LiveTranscriptionProvider>
)

const statePacket = (overrides: Record<string, unknown> = {}) =>
  new TextEncoder().encode(
    JSON.stringify({
      schemaVersion: 1,
      roomSid: 'RM_room-1',
      state: 'live',
      stateVersion: 1,
      eventId: 'event-1',
      occurredAt: '2026-09-29T00:00:00.000Z',
      sessionId: 'session-1',
      reason: null,
      ...overrides,
    })
  )

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  startSubtitleMock.mockResolvedValue(undefined)
  fetchSubtitleStateMock.mockResolvedValue({
    subtitle: {
      state: 'unknown',
      stateVersion: 0,
      sessionId: null,
      updatedAt: null,
      reason: null,
      desired: 'OFF',
      roomSid: 'RM_room-1',
    },
  })
  useRoomDataMock.mockReturnValue(undefined)
})

describe('LiveTranscriptionProvider', () => {
  it('deduplicates concurrent starts from the panel and compact captions', async () => {
    const { room } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-api-id', token: 'livekit-token' },
    })
    let resolveStart: (() => void) | undefined
    startSubtitleMock.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveStart = resolve
        })
    )
    const view = render(
      <App>
        <Probe />
      </App>
    )

    fireEvent.click(view.getByRole('button', { name: 'Start' }))
    fireEvent.click(view.getByRole('button', { name: 'Start' }))

    expect(startSubtitleMock).toHaveBeenCalledOnce()
    expect(startSubtitleMock).toHaveBeenCalledWith({
      id: 'room-api-id',
      token: 'livekit-token',
    })
    await act(async () => resolveStart?.())
  })

  it('reuses a successful start while backend state propagation is pending', async () => {
    const { room } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-api-id', token: 'livekit-token' },
    })
    const view = render(
      <App>
        <Probe />
      </App>
    )

    await act(async () => {
      fireEvent.click(view.getByRole('button', { name: 'Start' }))
      await Promise.resolve()
    })
    fireEvent.click(view.getByRole('button', { name: 'Start' }))

    expect(startSubtitleMock).toHaveBeenCalledOnce()
  })

  it('listens to the transcript and gap topics and releases both', async () => {
    const { room } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    const view = render(
      <App>
        <Probe />
      </App>
    )
    const handlers = new Map(
      room.registerTextStreamHandler.mock.calls.map(
        ([topic, handler]) => [topic, handler] as const
      )
    )
    expect([...handlers.keys()]).toEqual([
      'lk.transcription',
      'mastrao.transcription.gap.v1',
    ])

    await act(async () => {
      await handlers.get('mastrao.transcription.gap.v1')?.({
        info: { id: 'stream-gap' },
        readAll: async () =>
          JSON.stringify({
            schemaVersion: 1,
            gapId: 'GAP_1',
            reason: 'provider-reconnect',
          }),
      })
    })
    expect(view.getByTestId('transcription-state').textContent).toBe('unknown:')

    view.unmount()
    expect(room.unregisterTextStreamHandler).toHaveBeenCalledWith(
      'mastrao.transcription.gap.v1'
    )
  })

  it('keeps the room store mounted while the compact view closes and reopens', async () => {
    const { room } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    const view = render(
      <App>
        <Probe />
      </App>
    )

    const handlers = new Map(
      room.registerTextStreamHandler.mock.calls.map(
        ([topic, handler]) => [topic, handler] as const
      )
    )

    await act(async () => {
      await handlers.get('lk.transcription')?.(
        {
          info: {
            id: 'stream-1',
            attributes: {
              'lk.segment_id': 'item-1',
              'lk.transcribed_track_id': 'TR_alice',
              'lk.transcription_final': 'true',
            },
          },
          readAll: async () => 'bonjour',
        },
        { identity: 'agent' }
      )
    })
    expect(view.getByTestId('transcription-state').textContent).toContain(
      'bonjour'
    )

    view.rerender(<App />)
    view.rerender(
      <App>
        <Probe />
      </App>
    )

    expect(view.getByTestId('transcription-state').textContent).toContain(
      'bonjour'
    )
  })

  it('keeps the store and handlers stable across a JWT refresh', async () => {
    const { room } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-1', token: 'jwt-1' },
    })
    const view = render(
      <App>
        <Probe />
      </App>
    )
    await act(async () => {
      await Promise.resolve()
    })

    const handler = new Map(
      room.registerTextStreamHandler.mock.calls.map(
        ([topic, callback]) => [topic, callback] as const
      )
    ).get('lk.transcription')
    await act(async () => {
      await handler?.(
        {
          info: {
            id: 'stream-jwt',
            attributes: {
              'lk.segment_id': 'item-jwt',
              'lk.transcribed_track_id': 'TR_alice',
              'lk.transcription_final': 'true',
            },
          },
          readAll: async () => 'conservé',
        },
        { identity: 'agent' }
      )
    })

    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-1', token: 'jwt-2' },
    })
    view.rerender(
      <App>
        <Probe />
      </App>
    )
    await act(async () => {
      await Promise.resolve()
    })

    expect(view.getByTestId('transcription-state').textContent).toContain(
      'conservé'
    )
    expect(room.registerTextStreamHandler).toHaveBeenCalledTimes(2)
    expect(fetchSubtitleStateMock).toHaveBeenCalledTimes(1)
  })

  it('ingests one segment when legacy and text transports carry the same phrase', async () => {
    const { room, emit } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    const view = render(
      <App>
        <Probe />
      </App>
    )
    const handlers = new Map(
      room.registerTextStreamHandler.mock.calls.map(
        ([topic, handler]) => [topic, handler] as const
      )
    )

    await act(async () => {
      emit(
        RoomEvent.TranscriptionReceived,
        [
          {
            id: 'item-1',
            text: 'bonjour',
            language: 'fr',
            startTime: 0,
            endTime: 1,
            final: true,
            firstReceivedTime: 1,
            lastReceivedTime: 2,
          },
        ],
        { identity: 'alice' },
        { trackSid: 'TR_alice' }
      )
      await handlers.get('lk.transcription')?.(
        {
          info: {
            id: 'stream-1',
            attributes: {
              'lk.segment_id': 'item-1',
              'lk.transcribed_track_id': 'TR_alice',
              'lk.transcription_final': 'true',
            },
          },
          readAll: async () => 'bonjour',
        },
        { identity: 'agent' }
      )
    })

    expect(view.getByTestId('transcription-state').textContent).toBe(
      'unknown:alice:bonjour'
    )
    expect(
      room.on.mock.calls.some(
        ([event]) => event === RoomEvent.TranscriptionReceived
      )
    ).toBe(false)
  })

  it('attributes a text stream to the room publication instead of its sender', async () => {
    const { room } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    const view = render(
      <App>
        <Probe />
      </App>
    )
    await act(async () => {
      await Promise.resolve()
    })
    const handler = new Map(
      room.registerTextStreamHandler.mock.calls.map(
        ([topic, callback]) => [topic, callback] as const
      )
    ).get('lk.transcription')

    await act(async () => {
      await handler?.(
        {
          info: {
            id: 'stream-1',
            attributes: {
              'lk.segment_id': 'item-1',
              'lk.transcribed_track_id': 'TR_alice',
            },
          },
          readAll: async () => 'bonjour',
        },
        { identity: 'transcriber-agent' }
      )
    })

    expect(view.getByTestId('transcription-state').textContent).toContain(
      'alice:bonjour'
    )
  })

  it('keeps collective status stable across media reconnects', async () => {
    const { room, emit } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-1', token: 'token-1' },
    })
    fetchSubtitleStateMock.mockResolvedValue({
      subtitle: {
        state: 'live',
        stateVersion: 1,
        sessionId: 'session-1',
        updatedAt: '2026-09-29T00:00:00.000Z',
        reason: null,
        desired: 'ON',
        roomSid: 'RM_room-1',
      },
    })
    const view = render(
      <App>
        <Probe />
      </App>
    )
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      emit(
        RoomEvent.DataReceived,
        statePacket(),
        undefined,
        DataPacket_Kind.RELIABLE,
        LIVE_TRANSCRIPTION_STATE_TOPIC
      )
    })
    expect(view.getByTestId('transcription-state').textContent).toBe('live:')

    await act(async () => {
      emit(RoomEvent.Reconnecting)
      emit(RoomEvent.Reconnected)
      await Promise.resolve()
    })

    expect(view.getByTestId('transcription-state').textContent).toBe('live:')
  })

  it('ignores an API response that is older than a backend state packet', async () => {
    const { room, emit } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-1', token: 'token-1' },
    })
    let resolveSubtitleState: (response: unknown) => void = () => undefined
    fetchSubtitleStateMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSubtitleState = resolve
        })
    )

    const view = render(
      <App>
        <Probe />
      </App>
    )
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      emit(
        RoomEvent.DataReceived,
        statePacket(),
        undefined,
        DataPacket_Kind.RELIABLE,
        LIVE_TRANSCRIPTION_STATE_TOPIC
      )
    })

    await act(async () => {
      resolveSubtitleState({
        subtitle: {
          state: 'inactive',
          stateVersion: 0,
          sessionId: null,
          updatedAt: null,
          reason: null,
          desired: 'OFF',
          roomSid: 'RM_room-1',
        },
      })
      await Promise.resolve()
    })

    expect(view.getByTestId('transcription-state').textContent).toBe('live:')
  })

  it('accepts only reliable backend state packets', async () => {
    const { room, emit } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-1', token: 'token-1' },
    })
    const view = render(
      <App>
        <Probe />
      </App>
    )
    await act(async () => {
      await Promise.resolve()
      emit(
        RoomEvent.DataReceived,
        statePacket(),
        { identity: 'agent' },
        DataPacket_Kind.RELIABLE,
        LIVE_TRANSCRIPTION_STATE_TOPIC
      )
      emit(
        RoomEvent.DataReceived,
        statePacket(),
        undefined,
        DataPacket_Kind.LOSSY,
        LIVE_TRANSCRIPTION_STATE_TOPIC
      )
    })

    expect(view.getByTestId('transcription-state').textContent).toBe('unknown:')
  })

  it('keeps the last reliable state when resync REST fails', async () => {
    const { room, emit } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-1', token: 'token-1' },
    })
    fetchSubtitleStateMock
      .mockResolvedValueOnce({
        subtitle: {
          state: 'live',
          stateVersion: 1,
          sessionId: 'session-1',
          updatedAt: '2026-09-29T00:00:00.000Z',
          reason: null,
          desired: 'ON',
          roomSid: 'RM_room-1',
        },
      })
      .mockRejectedValueOnce(new Error('REST unavailable'))
    const view = render(
      <App>
        <Probe />
      </App>
    )
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      emit(
        RoomEvent.DataReceived,
        statePacket({ stateVersion: 3, eventId: 'event-3' }),
        undefined,
        DataPacket_Kind.RELIABLE,
        LIVE_TRANSCRIPTION_STATE_TOPIC
      )
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(view.getByTestId('transcription-state').textContent).toBe('live:')
    expect(view.getByTestId('transcription-resync').textContent).toBe('failed')
  })
})
