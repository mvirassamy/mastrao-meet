import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConnectionState } from 'livekit-client'
import type { VideoRecordingPolicy } from '@/features/rooms/api/ApiRoom'
import { RecordingPreparation } from './RecordingPreparation'

const activateRecording = vi.fn()
const decideRecording = vi.fn()
let isEnding = false
const video: VideoRecordingPolicy = {
  consultation_source: 'present',
  decision: 'absent',
  decision_basis: 'pending',
  start_status: 'pending',
  decision_lock: 'open',
  started_at: null,
  start_requested: false,
  start_available: true,
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@livekit/components-react', () => ({
  useConnectionState: () => ConnectionState.Connected,
}))
vi.mock('@/features/rooms/api/recordingConsent', () => ({
  activateRecording: (...args: unknown[]) => activateRecording(...args),
  decideRecording: (...args: unknown[]) => decideRecording(...args),
}))
vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({ isEnding }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  isEnding = false
  activateRecording.mockResolvedValue(undefined)
  decideRecording.mockResolvedValue(undefined)
})
afterEach(cleanup)

it('only requests capture after the host clicks and rereads authoritative status', async () => {
  const refresh = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={video}
      onRecordingChanged={refresh}
    />
  )
  expect(activateRecording).not.toHaveBeenCalled()
  expect(decideRecording).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'videoYes' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  expect(activateRecording).toHaveBeenCalledWith(
    'room-1',
    expect.stringMatching(/^activation_[0-9a-f]+$/)
  )
  expect(screen.queryByText('active')).toBeNull()
})

it('does not ask guests before the host requests recording', () => {
  const { container } = render(
    <RecordingPreparation roomId="room-1" video={video} />
  )
  expect(screen.queryByRole('button', { name: 'startVideo' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'videoYes' })).toBeNull()
  expect(container.firstChild).toBeNull()
})

it('asks a present guest after the host requests recording', () => {
  render(
    <RecordingPreparation
      roomId="room-1"
      video={{ ...video, start_requested: true }}
    />
  )
  expect(screen.getByRole('button', { name: 'videoYes' })).toBeDefined()
  expect(screen.getByRole('button', { name: 'videoNo' })).toBeDefined()
})

it('lets a present guest reverse a refusal after the request closes', () => {
  render(
    <RecordingPreparation
      roomId="room-1"
      video={{
        ...video,
        decision: 'refused',
        decision_basis: 'explicit',
        start_status: 'refused',
      }}
    />
  )
  expect(screen.getByRole('button', { name: 'videoYes' })).toBeDefined()
  expect(screen.getByRole('button', { name: 'videoNo' })).toBeDefined()
})

it.each(['pending', 'refused'] as const)(
  'honors unavailable start with %s decisions',
  (start_status) => {
    render(
      <RecordingPreparation
        roomId="room-1"
        canStart
        isHost
        video={{ ...video, start_status, start_available: false }}
      />
    )
    const start = screen.getByRole('button', { name: 'startVideo' })
    expect(start.hasAttribute('disabled')).toBe(true)
    fireEvent.click(start)
    expect(activateRecording).not.toHaveBeenCalled()
  }
)

it('does not ask an invitee whose email choice already applies', () => {
  render(
    <RecordingPreparation
      roomId="room-1"
      video={{
        ...video,
        consultation_source: 'email',
        decision_basis: 'no_opposition',
        start_requested: true,
      }}
    />
  )
  expect(screen.queryByRole('button', { name: 'videoYes' })).toBeNull()
})

it('does not confirm a response when the server rejects a locked decision', async () => {
  decideRecording.mockRejectedValueOnce(new Error('409'))
  const refresh = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingPreparation
      roomId="room-1"
      video={{ ...video, start_requested: true }}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'videoYes' }))
  await screen.findByRole('alert')
  expect(refresh).toHaveBeenCalledOnce()
  expect(
    screen
      .getByRole('button', { name: 'videoYes' })
      .getAttribute('aria-pressed')
  ).toBe('false')
})

it('allows changing yes to no and back with a new idempotency identifier', async () => {
  const refresh = vi.fn().mockResolvedValue(undefined)
  const { rerender } = render(
    <RecordingPreparation
      roomId="room-1"
      video={{ ...video, start_requested: true }}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'videoYes' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
  rerender(
    <RecordingPreparation
      roomId="room-1"
      video={{
        ...video,
        start_requested: true,
        decision: 'accepted',
        decision_basis: 'explicit',
      }}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'videoNo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
  rerender(
    <RecordingPreparation
      roomId="room-1"
      video={{
        ...video,
        start_requested: true,
        decision: 'refused',
        decision_basis: 'explicit',
      }}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'videoYes' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(3))
  expect(decideRecording.mock.calls.map((call) => call[1])).toEqual([
    'accepted',
    'refused',
    'accepted',
  ])
  expect(new Set(decideRecording.mock.calls.map((call) => call[2])).size).toBe(
    3
  )
})

it.each(['start_in_progress', 'started', 'stopped'] as const)(
  'prevents choices and new start after %s',
  (decision_lock) => {
    render(
      <RecordingPreparation
        roomId="room-1"
        canStart
        isHost
        video={{ ...video, decision_lock }}
      />
    )
    expect(screen.queryByRole('button', { name: 'videoYes' })).toBeNull()
    const start = screen.getByRole('button', { name: 'startVideo' })
    expect(start.hasAttribute('disabled')).toBe(true)
    fireEvent.click(start)
    expect(activateRecording).not.toHaveBeenCalled()
  }
)

it('blocks actions while the meeting closes', () => {
  isEnding = true
  render(<RecordingPreparation roomId="room-1" canStart isHost video={video} />)
  screen.getAllByRole('button').forEach((button) => {
    expect(button.hasAttribute('disabled')).toBe(true)
    fireEvent.click(button)
  })
  expect(activateRecording).not.toHaveBeenCalled()
  expect(decideRecording).not.toHaveBeenCalled()
})

it('prevents duplicate requests while start is in flight', async () => {
  let complete!: () => void
  activateRecording.mockReturnValueOnce(
    new Promise<void>((resolve) => {
      complete = resolve
    })
  )
  const refresh = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={video}
      onRecordingChanged={refresh}
    />
  )
  const start = screen.getByRole('button', { name: 'startVideo' })
  fireEvent.click(start)
  fireEvent.click(start)
  expect(activateRecording).toHaveBeenCalledOnce()
  complete()
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
})

it('shows recording startup instead of missing agreements while activation is in flight', () => {
  activateRecording.mockReturnValueOnce(new Promise(() => undefined))
  render(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={{ ...video, start_requested: true }}
    />
  )

  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))

  expect(screen.getByRole('status').textContent).toBe('starting')
  expect(screen.queryByText('videoPending')).toBeNull()
})

it('keeps recording startup visible until authoritative state catches up', async () => {
  const refresh = vi.fn().mockResolvedValue(undefined)
  const { rerender } = render(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={{ ...video, start_requested: true }}
      onRecordingChanged={refresh}
    />
  )

  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))

  await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  expect(screen.getByRole('status').textContent).toBe('starting')
  expect(screen.queryByText('videoPending')).toBeNull()
  const start = screen.getByRole('button', { name: 'startVideo' })
  expect(start.hasAttribute('disabled')).toBe(true)
  fireEvent.click(start)
  expect(activateRecording).toHaveBeenCalledOnce()

  rerender(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={{
        ...video,
        start_requested: true,
        decision_lock: 'start_in_progress',
      }}
      onRecordingChanged={refresh}
    />
  )

  expect(screen.getByRole('status').textContent).toBe('starting')
})

it('retries an unconfirmed activation using the same identifier', async () => {
  activateRecording.mockRejectedValueOnce(new Error('Unavailable'))
  const refresh = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={video}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
  expect(activateRecording.mock.calls[1]).toEqual(
    activateRecording.mock.calls[0]
  )
})

it('creates a new request when a completed attempt returns to pending decisions', async () => {
  const refresh = vi.fn().mockResolvedValue(undefined)
  const { rerender } = render(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={video}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))

  rerender(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={{ ...video, decision_lock: 'start_in_progress' }}
      onRecordingChanged={refresh}
    />
  )
  rerender(
    <RecordingPreparation
      roomId="room-1"
      canStart
      isHost
      video={video}
      onRecordingChanged={refresh}
    />
  )
  await waitFor(() =>
    expect(
      screen
        .getByRole('button', { name: 'startVideo' })
        .hasAttribute('disabled')
    ).toBe(false)
  )

  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
  expect(activateRecording.mock.calls[1][1]).not.toBe(
    activateRecording.mock.calls[0][1]
  )
})
