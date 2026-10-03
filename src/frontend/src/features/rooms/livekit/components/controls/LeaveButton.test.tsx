import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConnectionState } from 'livekit-client'

import { LeaveButton } from './LeaveButton'

const disconnect = vi.fn()
const navigateTo = vi.fn()
const reportError = vi.fn()
const endMeeting = vi.fn()
const beginEnding = vi.fn()
const markEnding = vi.fn()
const markEndingUncertain = vi.fn()
const markEnded = vi.fn()
const markActive = vi.fn()
let connectionState = ConnectionState.Connected

vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => ({ disconnect }),
  useConnectionState: () => connectionState,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/navigation/navigateTo', () => ({
  navigateTo: (...args: unknown[]) => navigateTo(...args),
}))
vi.mock('@/features/analytics/telemetry', () => ({
  reportError: (...args: unknown[]) => reportError(...args),
}))
vi.mock('@/features/rooms/api/endMeeting', () => ({
  endMeeting: (...args: unknown[]) => endMeeting(...args),
  isRetryableEndMeetingError: (error: { statusCode?: unknown }) =>
    typeof error.statusCode !== 'number' || error.statusCode >= 500,
}))
vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({
    phase: 'active',
    beginEnding,
    markActive,
    markEnding,
    markEndingUncertain,
    markEnded,
  }),
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
  Dialog: ({
    children,
    isOpen,
    title,
  }: {
    children: ReactNode
    isOpen: boolean
    title: string
  }) => (isOpen ? <section aria-label={title}>{children}</section> : null),
  P: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogActions: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}))

describe('LeaveButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    connectionState = ConnectionState.Connected
    disconnect.mockResolvedValue(undefined)
    beginEnding.mockReturnValue('close_0123456789abcdef')
  })
  afterEach(cleanup)

  it('lets a disconnected participant leave even when the SDK emits no event', async () => {
    connectionState = ConnectionState.Disconnected
    render(<LeaveButton roomId="abc-defg-hij" />)
    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    await waitFor(() =>
      expect(navigateTo).toHaveBeenCalledWith('feedback', {
        outcome: 'left',
        roomId: 'abc-defg-hij',
      })
    )
    expect(disconnect).toHaveBeenCalledWith(true)
  })

  it('keeps the existing SDK disconnection flow during an active call', () => {
    render(<LeaveButton roomId="abc-defg-hij" />)
    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    expect(disconnect).toHaveBeenCalledWith(true)
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('asks a host whether to leave alone or end the meeting', () => {
    render(<LeaveButton roomId="abc-defg-hij" canEnd />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.leave' })
    )

    expect(disconnect).toHaveBeenCalledWith(true)
    expect(endMeeting).not.toHaveBeenCalled()
  })

  it('raises the ending fence before closing the meeting for everyone', async () => {
    endMeeting.mockResolvedValueOnce({ state: 'ended' })
    const onEnded = vi.fn()
    render(<LeaveButton roomId="abc-defg-hij" canEnd onEnded={onEnded} />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )

    await waitFor(() => expect(markEnded).toHaveBeenCalledOnce())
    expect(beginEnding.mock.invocationCallOrder[0]).toBeLessThan(
      endMeeting.mock.invocationCallOrder[0]
    )
    expect(endMeeting.mock.calls[0][1]).toBe('close_0123456789abcdef')
    expect(onEnded).toHaveBeenCalledOnce()
    expect(disconnect).not.toHaveBeenCalled()
  })

  it('keeps an uncertain close available for retry', async () => {
    endMeeting.mockRejectedValueOnce(new Error('response lost'))
    endMeeting.mockResolvedValueOnce({ state: 'ending' })
    render(<LeaveButton roomId="abc-defg-hij" canEnd />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )
    await screen.findByText('endMeeting.dialog.error')
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )

    await waitFor(() => expect(markEnding).toHaveBeenCalledOnce())
    expect(endMeeting.mock.calls[1][1]).toBe(endMeeting.mock.calls[0][1])
    expect(markEndingUncertain).toHaveBeenCalledOnce()
  })
})
