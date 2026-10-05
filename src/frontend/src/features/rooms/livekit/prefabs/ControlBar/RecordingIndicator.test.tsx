import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { RecordingIndicator } from './RecordingIndicator'

const stopRecording = vi.fn()
const beginEnding = vi.fn()
let isEnding = false

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/features/rooms/api/recordingConsent', () => ({
  stopRecording: (...args: unknown[]) => stopRecording(...args),
}))
vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({ isEnding, beginEnding }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  isEnding = false
  stopRecording.mockResolvedValue(undefined)
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
