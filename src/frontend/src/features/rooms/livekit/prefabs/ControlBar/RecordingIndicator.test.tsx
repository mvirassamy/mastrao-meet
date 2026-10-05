import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { VideoRecordingPolicy } from '@/features/rooms/api/ApiRoom'
import { RecordingIndicator } from './RecordingIndicator'

const activateRecording = vi.fn()
const decideRecording = vi.fn()
const stopRecording = vi.fn()
const video: VideoRecordingPolicy = {
  consultation_source: 'present',
  decision: 'absent',
  decision_basis: 'pending',
  start_status: 'pending',
  decision_lock: 'open',
  started_at: null,
  start_available: true,
}
const beginEnding = vi.fn()
let isEnding = false

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/features/rooms/api/recordingConsent', () => ({
  stopRecording: (...args: unknown[]) => stopRecording(...args),
  activateRecording: (...args: unknown[]) => activateRecording(...args),
  decideRecording: (...args: unknown[]) => decideRecording(...args),
}))
vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({ isEnding, beginEnding }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  isEnding = false
  stopRecording.mockResolvedValue(undefined)
  activateRecording.mockResolvedValue(undefined)
  decideRecording.mockResolvedValue(undefined)
})
afterEach(cleanup)

it('shows participants the recording state without an arrêt action', () => {
  render(
    <RecordingIndicator
      roomId="room-1"
      recording={{ mode: 'recorded', recording_state: 'active' }}
    />
  )

  expect(screen.getByRole('status').textContent).toContain('active')
  expect(screen.queryByRole('button', { name: 'stop' })).toBeNull()
})

it('shows a short badge while recording and keeps the full state for screen readers', () => {
  render(
    <RecordingIndicator
      roomId="room-1"
      recording={{ mode: 'recorded', recording_state: 'active' }}
    />
  )

  const badge = screen.getByText('badge')
  expect(badge.getAttribute('aria-hidden')).toBe('true')
  expect(screen.getByRole('status').textContent).toBe('badgeactive')
})

it('keeps the existing host stop action and refreshes the room', async () => {
  const onRecordingChanged = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'active',
        decision: 'accepted',
      }}
      onRecordingChanged={onRecordingChanged}
    />
  )

  const stopButton = screen.getByRole('button', { name: 'stop' })
  // Short visible label, full accessible name.
  expect(stopButton.textContent).toContain('stopShort')
  fireEvent.click(stopButton)

  await waitFor(() => expect(onRecordingChanged).toHaveBeenCalledOnce())
  expect(stopRecording).toHaveBeenCalledWith(
    'room-1',
    'host',
    expect.stringMatching(/^stop_[0-9a-f]+$/)
  )
})

it('lets the host stop video even with an email default instead of explicit acceptance', async () => {
  render(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'active',
        decision: 'absent',
      }}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'stop' }))
  await waitFor(() => expect(stopRecording).toHaveBeenCalledOnce())
})

it('does not offer the stop action while recording is stopping', () => {
  render(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'stopping',
        decision: 'accepted',
      }}
    />
  )

  expect(screen.getByRole('status').textContent).toContain('stopping')
  expect(screen.queryByRole('button', { name: 'stop' })).toBeNull()
})

it('lets the meeting continue when the stopped recording is processing', async () => {
  const onRecordingChanged = vi.fn().mockResolvedValue(undefined)
  const { rerender } = render(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'active',
        decision: 'accepted',
      }}
      onRecordingChanged={onRecordingChanged}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'stop' }))
  await waitFor(() => expect(onRecordingChanged).toHaveBeenCalledOnce())

  rerender(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'processing',
        decision: 'accepted',
      }}
      onRecordingChanged={onRecordingChanged}
    />
  )
  expect(beginEnding).not.toHaveBeenCalled()
  expect(screen.queryByRole('button')).toBeNull()
  expect(stopRecording).toHaveBeenCalledOnce()
})

it('allows retrying a failed stop with the same request identifier', async () => {
  stopRecording.mockRejectedValueOnce(new Error('Stop unavailable'))
  const onRecordingChanged = vi.fn().mockResolvedValue(undefined)
  render(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'active',
        decision: 'accepted',
      }}
      onRecordingChanged={onRecordingChanged}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: 'stop' }))
  await screen.findByText('withdrawError')
  expect(onRecordingChanged).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'stop' }))
  await waitFor(() => expect(onRecordingChanged).toHaveBeenCalledOnce())
  expect(stopRecording.mock.calls[1]).toEqual(stopRecording.mock.calls[0])
})

it('keeps choices during queued start until the fence, with stop and no second start', async () => {
  const refresh = vi.fn().mockResolvedValue(undefined)
  const { rerender } = render(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{ mode: 'recorded', recording_state: 'starting', video }}
      onRecordingChanged={refresh}
    />
  )
  expect(screen.queryByRole('button', { name: 'startVideo' })).toBeNull()
  expect(screen.getByRole('button', { name: 'stop' })).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: 'videoNo' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
  expect(decideRecording).toHaveBeenCalledWith(
    'room-1',
    'refused',
    expect.any(String)
  )
  expect(
    screen.getByRole('button', { name: 'videoNo' }).getAttribute('aria-pressed')
  ).toBe('false')
  rerender(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'starting',
        video: { ...video, decision: 'refused', decision_basis: 'explicit' },
      }}
      onRecordingChanged={refresh}
    />
  )
  expect(
    screen.getByRole('button', { name: 'videoNo' }).getAttribute('aria-pressed')
  ).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'videoYes' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
  expect(decideRecording.mock.calls.map((call) => call[1])).toEqual([
    'refused',
    'accepted',
  ])
  rerender(
    <RecordingIndicator
      roomId="room-1"
      canEnd
      recording={{
        mode: 'recorded',
        recording_state: 'starting',
        video: {
          ...video,
          decision_lock: 'start_in_progress',
          start_available: false,
        },
      }}
      onRecordingChanged={refresh}
    />
  )
  expect(screen.queryByRole('button', { name: 'videoYes' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'videoNo' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'startVideo' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'stop' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(3))
  expect(stopRecording).toHaveBeenCalledOnce()
  expect(activateRecording).not.toHaveBeenCalled()
})
