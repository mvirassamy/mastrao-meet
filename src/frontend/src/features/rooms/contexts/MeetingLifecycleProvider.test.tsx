import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useMeetingLifecycle } from './MeetingLifecycleContext'
import { MeetingLifecycleProvider } from './MeetingLifecycleProvider'

const endMeeting = vi.hoisted(() => vi.fn())

vi.mock('../api/endMeeting', () => ({
  endMeeting: (...args: unknown[]) => endMeeting(...args),
}))

const Probe = () => {
  const lifecycle = useMeetingLifecycle()
  return (
    <button type="button" onClick={() => lifecycle.beginEnding()}>
      {lifecycle.phase}:{lifecycle.closeRequestId ?? 'none'}
    </button>
  )
}

const DoubleProbe = () => {
  const lifecycle = useMeetingLifecycle()
  return (
    <button
      type="button"
      onClick={() => {
        const first = lifecycle.beginEnding()
        const second = lifecycle.beginEnding()
        window.history.replaceState({ first, second }, '')
      }}
    >
      close
    </button>
  )
}

const UncertainProbe = () => {
  const lifecycle = useMeetingLifecycle()
  return (
    <button
      type="button"
      onClick={() => {
        lifecycle.beginEnding()
        lifecycle.markEndingUncertain()
      }}
    >
      {lifecycle.phase}:{lifecycle.closeRequestId ?? 'none'}
    </button>
  )
}

describe('MeetingLifecycleProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
    window.sessionStorage.clear()
    endMeeting.mockReturnValue(new Promise(() => undefined))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    cleanup()
  })

  it('restores and retries one uncertain close intent with the same id', async () => {
    vi.useFakeTimers()
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    const key = `mastrao-meeting-close-v1:${roomId}`
    window.sessionStorage.setItem(key, 'close_existing')
    endMeeting
      .mockRejectedValueOnce(new Error('temporary failure'))
      .mockResolvedValueOnce({ state: 'ending' })

    render(
      <MeetingLifecycleProvider roomId={roomId}>
        <Probe />
      </MeetingLifecycleProvider>
    )

    expect(screen.getByRole('button').textContent).toBe(
      'uncertain:close_existing'
    )
    await vi.waitFor(() =>
      expect(endMeeting).toHaveBeenCalledWith(
        roomId,
        'close_existing',
        expect.any(AbortSignal)
      )
    )
    await vi.advanceTimersByTimeAsync(5_000)
    await vi.waitFor(() => expect(endMeeting).toHaveBeenCalledTimes(2))
    expect(endMeeting.mock.calls[1][0]).toBe(roomId)
    expect(endMeeting.mock.calls[1][1]).toBe('close_existing')
    await vi.waitFor(() =>
      expect(screen.getByRole('button').textContent).toBe(
        'ending:close_existing'
      )
    )

    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByRole('button').textContent).toBe(
      'requesting:close_existing'
    )
  })

  it('reuses one request id for two close attempts in the same tick', () => {
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    window.sessionStorage.clear()

    render(
      <MeetingLifecycleProvider roomId={roomId}>
        <DoubleProbe />
      </MeetingLifecycleProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'close' }))

    expect(window.history.state.first).toBe(window.history.state.second)
    expect(window.history.state.first).toBe(
      window.sessionStorage.getItem(`mastrao-meeting-close-v1:${roomId}`)
    )
  })

  it('retries a current-tab uncertain close intent with the same id', async () => {
    vi.useFakeTimers()
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    endMeeting.mockResolvedValueOnce({ state: 'ending' })

    render(
      <MeetingLifecycleProvider roomId={roomId}>
        <UncertainProbe />
      </MeetingLifecycleProvider>
    )

    fireEvent.click(screen.getByRole('button'))

    await vi.waitFor(() => expect(endMeeting).toHaveBeenCalledOnce())
    const requestId = screen.getByRole('button').textContent?.split(':')[1]
    expect(endMeeting).toHaveBeenCalledWith(
      roomId,
      requestId,
      expect.any(AbortSignal)
    )
    await vi.waitFor(() =>
      expect(screen.getByRole('button').textContent).toBe(`ending:${requestId}`)
    )
  })

  it('keeps the current-tab close intent when storage is unavailable', () => {
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })

    render(
      <MeetingLifecycleProvider roomId={roomId}>
        <Probe />
      </MeetingLifecycleProvider>
    )

    expect(screen.getByRole('button').textContent).toBe('active:none')
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByRole('button').textContent).toMatch(/^requesting:close_/)
  })
})
