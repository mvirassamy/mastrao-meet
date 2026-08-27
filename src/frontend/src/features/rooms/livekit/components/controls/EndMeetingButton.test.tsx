import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EndMeetingButton } from './EndMeetingButton'

const endMeeting = vi.fn()
const beginEnding = vi.fn()
const markEnding = vi.fn()
const markEndingUncertain = vi.fn()
const markEnded = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/features/rooms/api/endMeeting', () => ({
  endMeeting: (...args: unknown[]) => endMeeting(...args),
}))

vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({
    phase: 'active',
    isEnding: false,
    beginEnding,
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
    onPress?: () => void
    isDisabled?: boolean
    'aria-label'?: string
  }) => (
    <button
      type="button"
      aria-label={ariaLabel}
      disabled={isDisabled}
      onClick={onPress}
    >
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
}))

vi.mock('@/styled-system/jsx', () => ({
  HStack: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

describe('EndMeetingButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    beginEnding.mockReturnValue('close_0123456789abcdef')
  })

  afterEach(cleanup)

  it('raises the ending fence before waiting for the close response', () => {
    endMeeting.mockReturnValue(new Promise(() => undefined))

    render(<EndMeetingButton roomId="room_0123456789abcdef0123456789abcdef" />)

    fireEvent.click(screen.getByRole('button', { name: 'label' }))
    fireEvent.click(screen.getByRole('button', { name: 'dialog.confirm' }))

    expect(endMeeting).toHaveBeenCalledOnce()
    expect(beginEnding).toHaveBeenCalledOnce()
    expect(beginEnding.mock.invocationCallOrder[0]).toBeLessThan(
      endMeeting.mock.invocationCallOrder[0]
    )
    expect(endMeeting.mock.calls[0][1]).toBe('close_0123456789abcdef')
  })

  it('retries an uncertain close with the same request id', async () => {
    endMeeting.mockRejectedValueOnce(new Error('response lost'))
    endMeeting.mockResolvedValueOnce({ state: 'ended' })

    render(<EndMeetingButton roomId="room_0123456789abcdef0123456789abcdef" />)

    fireEvent.click(screen.getByRole('button', { name: 'label' }))
    fireEvent.click(screen.getByRole('button', { name: 'dialog.confirm' }))
    await screen.findByText('dialog.error')
    fireEvent.click(screen.getByRole('button', { name: 'dialog.confirm' }))

    expect(endMeeting).toHaveBeenCalledTimes(2)
    expect(endMeeting.mock.calls[1][1]).toBe(endMeeting.mock.calls[0][1])
    expect(markEndingUncertain).toHaveBeenCalledOnce()
  })

  it('waits for authoritative completion when Core returns ending', async () => {
    endMeeting.mockResolvedValueOnce({ state: 'ending' })
    const onEnded = vi.fn()
    render(
      <EndMeetingButton
        roomId="room_0123456789abcdef0123456789abcdef"
        onEnded={onEnded}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'label' }))
    fireEvent.click(screen.getByRole('button', { name: 'dialog.confirm' }))

    await vi.waitFor(() => expect(markEnding).toHaveBeenCalledOnce())
    expect(markEnded).not.toHaveBeenCalled()
    expect(onEnded).not.toHaveBeenCalled()
  })

  it('turns a bounded network timeout into an uncertain retry', async () => {
    vi.useFakeTimers()
    endMeeting.mockImplementation(
      (_roomId: string, _requestId: string, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(new Error('timeout')))
        })
    )
    render(<EndMeetingButton roomId="room_0123456789abcdef0123456789abcdef" />)

    fireEvent.click(screen.getByRole('button', { name: 'label' }))
    fireEvent.click(screen.getByRole('button', { name: 'dialog.confirm' }))
    await vi.advanceTimersByTimeAsync(15_000)

    expect(markEndingUncertain).toHaveBeenCalledOnce()
    vi.useRealTimers()
  })
})
