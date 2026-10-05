import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
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
  start_available: true,
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
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
      video={video}
      onRecordingChanged={refresh}
    />
  )
  expect(activateRecording).not.toHaveBeenCalled()
  expect(decideRecording).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  expect(activateRecording).toHaveBeenCalledWith(
    'room-1',
    expect.stringMatching(/^activation_[0-9a-f]+$/)
  )
  expect(screen.queryByText('active')).toBeNull()
})

it('does not give guests a start action', () => {
  render(<RecordingPreparation roomId="room-1" video={video} />)
  expect(screen.queryByRole('button', { name: 'startVideo' })).toBeNull()
  expect(screen.getByRole('button', { name: 'videoYes' })).toBeDefined()
})

it.each(['pending', 'refused'] as const)(
  'honors unavailable start with %s decisions',
  (start_status) => {
    render(
      <RecordingPreparation
        roomId="room-1"
        canStart
        video={{ ...video, start_status, start_available: false }}
      />
    )
    const start = screen.getByRole('button', { name: 'startVideo' })
    expect(start.hasAttribute('disabled')).toBe(true)
    fireEvent.click(start)
    expect(activateRecording).not.toHaveBeenCalled()
  }
)

it('shows absence of opposition without a false explicit yes', () => {
  render(
    <RecordingPreparation
      roomId="room-1"
      video={{
        ...video,
        consultation_source: 'email',
        decision_basis: 'no_opposition',
      }}
    />
  )
  expect(screen.getByText('noOpposition')).toBeDefined()
  expect(
    screen
      .getByRole('button', { name: 'videoYes' })
      .getAttribute('aria-pressed')
  ).toBe('false')
})

it('does not confirm a response when the server rejects a locked decision', async () => {
  decideRecording.mockRejectedValueOnce(new Error('409'))
  const refresh = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingPreparation
      roomId="room-1"
      video={video}
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
      video={video}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'videoYes' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
  rerender(
    <RecordingPreparation
      roomId="room-1"
      video={{ ...video, decision: 'accepted', decision_basis: 'explicit' }}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'videoNo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
  rerender(
    <RecordingPreparation
      roomId="room-1"
      video={{ ...video, decision: 'refused', decision_basis: 'explicit' }}
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
  render(<RecordingPreparation roomId="room-1" canStart video={video} />)
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

it('retries an unconfirmed activation using the same identifier', async () => {
  activateRecording.mockRejectedValueOnce(new Error('Unavailable'))
  const refresh = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingPreparation
      roomId="room-1"
      canStart
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
  render(
    <RecordingPreparation
      roomId="room-1"
      canStart
      video={video}
      onRecordingChanged={refresh}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByRole('button', { name: 'startVideo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
  expect(activateRecording.mock.calls[1][1]).not.toBe(
    activateRecording.mock.calls[0][1]
  )
})
