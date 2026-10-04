import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { RecordingIndicator } from './RecordingIndicator'

const stopRecording = vi.fn()
let isEnding = false

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/features/rooms/api/recordingConsent', () => ({
  stopRecording: (...args: unknown[]) => stopRecording(...args),
}))
vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({ isEnding }),
}))
vi.mock('@/primitives', () => ({
  Button: ({
    children,
    onPress,
    isDisabled,
    'aria-label': ariaLabel,
  }: {
    children: ReactNode
    onPress: () => void
    isDisabled?: boolean
    'aria-label'?: string
  }) => (
    <button aria-label={ariaLabel} disabled={isDisabled} onClick={onPress}>
      {children}
    </button>
  ),
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

  fireEvent.click(screen.getByRole('button', { name: 'stop' }))

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
