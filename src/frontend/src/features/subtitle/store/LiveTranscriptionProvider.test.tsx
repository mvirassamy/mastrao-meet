import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LiveTranscriptionProvider } from './LiveTranscriptionProvider'
import { useLiveTranscription } from './liveTranscriptionContext'
import {
  LIVE_TRANSCRIPTION_GAP_TOPIC,
  LIVE_TRANSCRIPTION_TOPIC,
} from './liveTranscriptionTypes'

const useRoomContextMock = vi.hoisted(() => vi.fn())

vi.mock('@livekit/components-react', () => ({
  useRoomContext: useRoomContextMock,
}))

const createRoom = () => {
  const handlers = new Map<string, (...args: never[]) => Promise<void>>()
  const room = {
    name: 'room-1',
    localParticipant: { identity: 'local', trackPublications: new Map() },
    remoteParticipants: new Map(),
    getParticipantByIdentity: vi.fn(() => undefined),
    on: vi.fn(),
    off: vi.fn(),
    registerTextStreamHandler: vi.fn(
      (topic: string, handler: (...args: never[]) => Promise<void>) => {
        handlers.set(topic, handler)
      }
    ),
    unregisterTextStreamHandler: vi.fn(),
  }
  return { room, handlers }
}

const Probe = () => {
  const { status, segments, gaps } = useLiveTranscription()
  return (
    <output data-testid="probe">
      {status}:{segments.map((segment) => segment.text).join('|')}:{gaps.length}
    </output>
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('LiveTranscriptionProvider', () => {
  it('derives live status directly from the canonical room transcript', async () => {
    const { room, handlers } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    render(
      <LiveTranscriptionProvider>
        <Probe />
      </LiveTranscriptionProvider>
    )

    const handler = handlers.get(LIVE_TRANSCRIPTION_TOPIC)
    expect(handler).toBeDefined()
    await act(async () => {
      await handler?.(
        {
          info: { id: 'stream-1' },
          readAll: async () =>
            JSON.stringify({
              schemaVersion: 1,
              participantIdentity: 'unknown-participant',
              trackSid: 'room-mix',
              legId: 'leg-1',
              itemId: 'item-1',
              sequence: 1,
              revision: 1,
              state: 'final',
              text: 'Bonjour la salle',
              receivedAt: 1,
            }),
        } as never,
        { identity: 'room-audio-live-worker' } as never
      )
    })

    expect(screen.getByTestId('probe').textContent).toBe(
      'live:Bonjour la salle:0'
    )
  })

  it('marks a bounded room-audio gap as degraded', async () => {
    const { room, handlers } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    render(
      <LiveTranscriptionProvider>
        <Probe />
      </LiveTranscriptionProvider>
    )

    const handler = handlers.get(LIVE_TRANSCRIPTION_GAP_TOPIC)
    expect(handler).toBeDefined()
    await act(async () => {
      await handler?.({
        info: { id: 'gap-1' },
        readAll: async () =>
          JSON.stringify({
            schemaVersion: 1,
            gapId: 'gap-1',
            reason: 'provider-error',
            receivedAt: 1,
          }),
      } as never)
    })

    expect(screen.getByTestId('probe').textContent).toBe('degraded::1')
  })
})
