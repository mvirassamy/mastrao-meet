import { RoomContext } from '@livekit/components-react'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { ConnectionState, Room, RoomEvent } from 'livekit-client'
import { afterEach, expect, it, vi } from 'vitest'
import type { VideoRecordingPolicy } from '@/features/rooms/api/ApiRoom'
import { RecordingPreparation } from './RecordingPreparation'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({ isEnding: false }),
}))

const video: VideoRecordingPolicy = {
  consultation_source: 'present',
  decision: 'accepted',
  decision_basis: 'explicit',
  start_status: 'authorized',
  decision_lock: 'open',
  started_at: null,
  start_requested: false,
  start_available: true,
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it.each([
  ConnectionState.Connecting,
  ConnectionState.Disconnected,
  ConnectionState.Reconnecting,
  ConnectionState.SignalReconnecting,
])('prevents video activation while the SDK is %s', (state) => {
  const room = new Room()
  room.state = state
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  render(
    <RoomContext.Provider value={room}>
      <RecordingPreparation roomId="room-1" canStart isHost video={video} />
    </RoomContext.Provider>
  )
  const start = screen.getByRole('button', { name: 'startVideo' })
  expect(start.hasAttribute('disabled')).toBe(true)
  fireEvent.click(start)
  expect(fetch).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'videoNo' })).toBeNull()
})

it('recovers from a roster 503 only after another explicit host click', async () => {
  const room = new Room()
  room.state = ConnectionState.Connected
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ message: 'Unavailable' }), {
        status: 503,
        headers: { 'content-type': 'application/json' },
      })
    )
    .mockResolvedValueOnce(
      new Response('{}', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
  vi.stubGlobal('fetch', fetch)
  const refresh = vi.fn().mockResolvedValue(undefined)
  render(
    <RoomContext.Provider value={room}>
      <RecordingPreparation
        roomId="room-1"
        canStart
        isHost
        video={video}
        onRecordingChanged={refresh}
      />
    </RoomContext.Provider>
  )
  const start = screen.getByRole('button', { name: 'startVideo' })
  expect(start.hasAttribute('disabled')).toBe(false)
  fireEvent.click(start)
  await waitFor(() => {
    expect(screen.getByRole('alert').textContent).toBe('videoActionError')
    expect(start.hasAttribute('disabled')).toBe(false)
  })
  expect(refresh).toHaveBeenCalledOnce()
  expect(fetch).toHaveBeenCalledOnce()

  fireEvent.click(start)
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls[1]).toEqual(fetch.mock.calls[0])
  expect(screen.queryByRole('alert')).toBeNull()
})

it('enables start on connection and disables it again on reconnect without activating', () => {
  const room = new Room()
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  render(
    <RoomContext.Provider value={room}>
      <RecordingPreparation roomId="room-1" canStart isHost video={video} />
    </RoomContext.Provider>
  )
  const start = screen.getByRole('button', { name: 'startVideo' })
  expect(start.hasAttribute('disabled')).toBe(true)
  act(() => {
    room.state = ConnectionState.Connected
    room.emit(RoomEvent.ConnectionStateChanged, room.state)
  })
  expect(start.hasAttribute('disabled')).toBe(false)
  act(() => {
    room.state = ConnectionState.Reconnecting
    room.emit(RoomEvent.ConnectionStateChanged, room.state)
  })
  expect(start.hasAttribute('disabled')).toBe(true)
  act(() => {
    room.state = ConnectionState.Connected
    room.emit(RoomEvent.ConnectionStateChanged, room.state)
  })
  expect(start.hasAttribute('disabled')).toBe(false)
  expect(fetch).not.toHaveBeenCalled()
})
