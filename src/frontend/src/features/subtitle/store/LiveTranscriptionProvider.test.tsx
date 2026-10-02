import { act, cleanup, render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataPacket_Kind, RoomEvent } from 'livekit-client'
import { LiveTranscriptionProvider } from './LiveTranscriptionProvider'
import { useLiveTranscription } from './liveTranscriptionContext'
import { LiveTranscriptSidePanel } from '../component/LiveTranscriptSidePanel'
import { LIVE_TRANSCRIPTION_STATE_TOPIC } from './liveTranscriptionTypes'

const { fetchSubtitleStateMock, useRoomContextMock, useRoomDataMock } =
  vi.hoisted(() => ({
    fetchSubtitleStateMock: vi.fn(),
    useRoomContextMock: vi.fn(),
    useRoomDataMock: vi.fn(),
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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'connection.connected': 'Connected',
        'connection.disconnectedDescription': 'Meeting unavailable',
        'status.live': 'Live',
        segmentsLabel: 'Live transcript segments',
        empty: 'No transcript yet',
        interim: 'In progress',
        final: 'Final',
        error: 'Refresh failed',
        startError: 'Start failed',
        retry: 'Retry',
        refreshing: 'Refreshing',
        unknown: 'Unknown participant',
      })[key] ?? key,
  }),
}))

vi.mock('@/primitives', () => ({
  Button: ({
    children,
    isDisabled,
    onPress,
  }: {
    children: ReactNode
    isDisabled?: boolean
    onPress?: () => void
  }) => (
    <button disabled={isDisabled} onClick={onPress} type="button">
      {children}
    </button>
  ),
  Text: ({
    as = 'p',
    children,
    margin: _margin,
    padding: _padding,
    variant: _variant,
    wrap: _wrap,
    ...props
  }: {
    as?: keyof JSX.IntrinsicElements
    margin?: boolean
    padding?: boolean
    variant?: string
    wrap?: string
    children: ReactNode
    [key: string]: unknown
  }) => {
    const Component = as
    return <Component {...props}>{children}</Component>
  },
}))

vi.mock('@/components/Avatar', () => ({
  Avatar: ({ name, bgColor }: { name: string; bgColor: string }) => (
    <span data-testid="speaker-avatar" data-name={name} data-color={bgColor} />
  ),
}))

vi.mock('../hooks/useSubtitles', () => ({
  useSubtitles: () => ({ ensureSubtitlesStarted: async () => undefined }),
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
          name: 'Alice Martin',
          attributes: { color: 'hsl(120, 50%, 40%)' },
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
  const { segments, status, resyncStatus } = useLiveTranscription()
  return (
    <>
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

const speakerSegment = {
  participantIdentity: 'alice',
  trackSid: '',
  legId: 'leg',
  itemId: 'item',
  state: 'final' as const,
  text: 'Hello',
  sequence: 0,
  revision: 1,
  receivedAt: 1,
  metadataSource: 'envelope' as const,
}
const Seed = () => {
  const { dispatch } = useLiveTranscription()
  return (
    <button
      onClick={() =>
        dispatch({
          type: 'ingest',
          event: { type: 'segments', segments: [speakerSegment] },
        })
      }
    >
      seed
    </button>
  )
}
const SpeakerProbe = () => {
  const context = useLiveTranscription()
  const speaker = context.resolveSpeaker('alice')
  return (
    <output data-testid="speaker">
      {speaker ? `${speaker.label}:${speaker.color}` : 'unknown'}
    </output>
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
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

beforeEach(() => {
  window.sessionStorage.clear()
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
  it('remembers participant names while the transcript panel is closed', () => {
    const { room, emit } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    render(<App />)

    act(() => {
      emit(RoomEvent.ParticipantConnected, {
        identity: 'bob',
        name: 'Bob Dupont',
        attributes: { color: 'hsl(120, 50%, 40%)' },
      })
    })

    expect(
      window.sessionStorage.getItem(
        'mastrao-live-transcript-speakers-v1:room-1'
      )
    ).toContain('Bob Dupont')
  })

  it('keeps speakers through a closed panel, departure and panel remount without storage', () => {
    vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new Error('blocked')
    })
    const { room, emit } = createRoom()
    room.name = 'no-storage'
    useRoomContextMock.mockReturnValue(room)
    const view = render(<App />)
    const alice = room.remoteParticipants.get('alice')!
    room.remoteParticipants.clear()
    act(() => emit(RoomEvent.ParticipantDisconnected, alice))
    view.rerender(
      <App>
        <Seed />
        <LiveTranscriptSidePanel />
      </App>
    )
    act(() => view.getByText('seed').click())
    expect(view.getByText('Alice Martin')).toBeTruthy()
    view.rerender(<App />)
    view.rerender(
      <App>
        <LiveTranscriptSidePanel />
      </App>
    )
    expect(view.getByText('Alice Martin')).toBeTruthy()
    view.unmount()
    const returned = render(
      <App>
        <SpeakerProbe />
      </App>
    )
    expect(returned.getByTestId('speaker').textContent).toContain(
      'Alice Martin'
    )
  })

  it('updates name and color without receiving another segment', () => {
    const { room, emit } = createRoom()
    room.name = 'updates'
    useRoomContextMock.mockReturnValue(room)
    const view = render(
      <App>
        <SpeakerProbe />
      </App>
    )
    expect(view.getByTestId('speaker').textContent).toContain('Alice Martin')
    const alice = room.remoteParticipants.get('alice')!
    alice.name = 'Alice Updated'
    act(() => emit(RoomEvent.ParticipantNameChanged, alice.name, alice))
    expect(view.getByTestId('speaker').textContent).toContain('Alice Updated')
    alice.attributes.color = 'hsl(200, 60%, 40%)'
    act(() =>
      emit(RoomEvent.ParticipantAttributesChanged, alice.attributes, alice)
    )
    expect(view.getByTestId('speaker').textContent).toContain(
      'hsl(200, 60%, 40%)'
    )
  })

  it('retains a resolved alias after departure and updates the visible name and color', () => {
    const { room, emit } = createRoom()
    room.name = 'alias-room'
    const alice = room.remoteParticipants.get('alice')!
    alice.identity = 'canonical-alice'
    room.getParticipantByIdentity.mockReturnValue(alice)
    useRoomContextMock.mockReturnValue(room)
    const view = render(
      <App>
        <Seed />
        <LiveTranscriptSidePanel />
      </App>
    )
    act(() => view.getByText('seed').click())
    expect(view.getByText('Alice Martin')).toBeTruthy()
    alice.name = 'Alice Renamed'
    act(() => emit(RoomEvent.ParticipantNameChanged, alice.name, alice))
    expect(view.getByText('Alice Renamed')).toBeTruthy()
    alice.attributes.color = 'hsl(200, 60%, 40%)'
    act(() =>
      emit(RoomEvent.ParticipantAttributesChanged, alice.attributes, alice)
    )
    expect(view.getByTestId('speaker-avatar').dataset.color).toBe(
      alice.attributes.color
    )
    view.rerender(<App />)
    room.remoteParticipants.clear()
    room.getParticipantByIdentity.mockReturnValue(undefined)
    act(() => emit(RoomEvent.ParticipantDisconnected, alice))
    view.rerender(
      <App>
        <LiveTranscriptSidePanel />
      </App>
    )
    expect(view.getByText('Alice Renamed')).toBeTruthy()
    view.unmount()
    const returned = render(
      <App>
        <Seed />
        <LiveTranscriptSidePanel />
      </App>
    )
    act(() => returned.getByText('seed').click())
    expect(returned.getByText('Alice Renamed')).toBeTruthy()
  })

  it('restores a remounted Provider and isolates room changes', () => {
    const { room } = createRoom()
    room.name = 'restore'
    useRoomContextMock.mockReturnValue(room)
    const first = render(
      <App>
        <SpeakerProbe />
      </App>
    )
    first.unmount()
    room.remoteParticipants.clear()
    const view = render(
      <App>
        <SpeakerProbe />
      </App>
    )
    expect(view.getByTestId('speaker').textContent).toContain('Alice Martin')
    room.name = 'different-room'
    view.rerender(
      <App>
        <SpeakerProbe />
      </App>
    )
    expect(view.getByTestId('speaker').textContent).toBe('unknown')
    room.name = 'restore'
    view.rerender(
      <App>
        <SpeakerProbe />
      </App>
    )
    expect(view.getByTestId('speaker').textContent).toContain('Alice Martin')
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
