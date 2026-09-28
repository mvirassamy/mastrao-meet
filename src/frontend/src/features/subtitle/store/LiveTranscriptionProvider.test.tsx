import { act, cleanup, render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RoomEvent } from 'livekit-client'
import { LiveTranscriptionProvider } from './LiveTranscriptionProvider'
import { useLiveTranscription } from './liveTranscriptionContext'

const { useRoomContextMock } = vi.hoisted(() => ({
  useRoomContextMock: vi.fn(),
}))

vi.mock('@livekit/components-react', () => ({
  useRoomContext: useRoomContextMock,
}))

const createRoom = () => {
  const listeners = new Map<string, (...args: unknown[]) => void>()
  const room = {
    name: 'room-1',
    localParticipant: { identity: 'local' },
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
  const { segments, status } = useLiveTranscription()
  return (
    <output data-testid="transcription-state">
      {status}:{segments.map(({ text }) => text).join('|')}
    </output>
  )
}

const App = ({ children }: { children?: ReactNode }) => (
  <LiveTranscriptionProvider>{children}</LiveTranscriptionProvider>
)

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('LiveTranscriptionProvider', () => {
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
    expect(view.getByTestId('transcription-state').textContent).toBe(
      'degraded:'
    )

    view.unmount()
    expect(room.unregisterTextStreamHandler).toHaveBeenCalledWith(
      'mastrao.transcription.gap.v1'
    )
  })

  it('keeps the room store mounted while the compact view closes and reopens', () => {
    const { room, emit } = createRoom()
    useRoomContextMock.mockReturnValue(room)
    const view = render(
      <App>
        <Probe />
      </App>
    )

    act(() => {
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
})
