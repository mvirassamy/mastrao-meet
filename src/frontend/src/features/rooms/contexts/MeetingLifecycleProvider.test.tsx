import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/ApiError'
import { useMeetingLifecycle } from './MeetingLifecycleContext'
import { MeetingLifecycleProvider } from './MeetingLifecycleProvider'

const endMeeting = vi.hoisted(() => vi.fn())
const fetchRoomLifecycle = vi.hoisted(() => vi.fn())
vi.mock('../api/fetchRoomLifecycle', () => ({ fetchRoomLifecycle }))

vi.mock('../api/endMeeting', () => ({
  endMeeting: (...args: unknown[]) => endMeeting(...args),
  isRetryableEndMeetingError: (error: { statusCode?: unknown }) =>
    typeof error.statusCode !== 'number' ||
    error.statusCode === 408 ||
    error.statusCode === 429 ||
    error.statusCode >= 500,
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
    fetchRoomLifecycle.mockReset().mockReturnValue(new Promise(() => undefined))
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

  it('stops retrying an authoritative close refusal', async () => {
    vi.useFakeTimers()
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    const key = `mastrao-meeting-close-v1:${roomId}`
    window.sessionStorage.setItem(key, 'close_existing')
    endMeeting.mockRejectedValueOnce(new ApiError(409, { message: 'conflict' }))

    render(
      <MeetingLifecycleProvider roomId={roomId}>
        <Probe />
      </MeetingLifecycleProvider>
    )

    await vi.waitFor(() => expect(endMeeting).toHaveBeenCalledOnce())
    await vi.advanceTimersByTimeAsync(5_000)

    expect(endMeeting).toHaveBeenCalledOnce()
    expect(screen.getByRole('button').textContent).toBe('active:none')
    expect(window.sessionStorage.getItem(key)).toBeNull()
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

const ObservationProbe = ({ name = 'client' }: { name?: string }) => {
  const lifecycle = useMeetingLifecycle()
  return (
    <>
      <output aria-label={name}>
        {lifecycle.phase}:{lifecycle.canonicalLifecycle?.state ?? 'checking'}
      </output>
      <button
        onClick={() => {
          lifecycle.reconcileLifecycle()
          lifecycle.reconcileLifecycle()
          lifecycle.markEndingUncertain()
        }}
      >
        observe {name}
      </button>
      <button
        onClick={() => {
          lifecycle.beginEnding()
          lifecycle.markEndingUncertain()
        }}
      >
        close {name}
      </button>
      <button onClick={lifecycle.markEnding}>late command {name}</button>
    </>
  )
}

const roomA = 'room_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const roomB = 'room_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
const deferredLifecycle = () => {
  let resolve!: (value: { state: 'open' | 'ending' | 'ended' }) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<{ state: 'open' | 'ending' | 'ended' }>(
    (yes, no) => {
      resolve = yes
      reject = no
    }
  )
  return { promise, resolve, reject }
}

describe('canonical lifecycle observation', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.sessionStorage.clear()
    fetchRoomLifecycle.mockReset()
    endMeeting.mockReset().mockReturnValue(new Promise(() => undefined))
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('coalesces simultaneous triggers and never overlaps slow reads', async () => {
    const pending = deferredLifecycle()
    fetchRoomLifecycle.mockReturnValue(pending.promise)
    render(
      <MeetingLifecycleProvider roomId={roomA}>
        <ObservationProbe />
      </MeetingLifecycleProvider>
    )
    fireEvent.click(screen.getByText('observe client'))
    await act(async () => vi.advanceTimersByTimeAsync(10_000))
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
    await act(async () => pending.resolve({ state: 'open' }))
    expect(screen.getByLabelText('client').textContent).toBe('active:open')
    await act(async () => vi.advanceTimersByTimeAsync(5_000))
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
    expect(endMeeting).not.toHaveBeenCalled()
  })

  it.each([404, 503])(
    'retries %s without inventing a terminal state, then accepts 410',
    async (code) => {
      fetchRoomLifecycle
        .mockRejectedValueOnce(new ApiError(code, { message: 'unavailable' }))
        .mockRejectedValueOnce(new ApiError(410, { message: 'gone' }))
      render(
        <MeetingLifecycleProvider roomId={roomA}>
          <ObservationProbe />
        </MeetingLifecycleProvider>
      )
      fireEvent.click(screen.getByText('observe client'))
      await act(async () => undefined)
      expect(screen.getByLabelText('client').textContent).toBe(
        'uncertain:checking'
      )
      await act(async () => vi.advanceTimersByTimeAsync(1_000))
      expect(screen.getByLabelText('client').textContent).toBe('ended:ended')
      fireEvent.click(screen.getByText('late command client'))
      expect(screen.getByLabelText('client').textContent).toBe('ended:ended')
      await act(async () => vi.advanceTimersByTimeAsync(5_000))
      expect(fetchRoomLifecycle).toHaveBeenCalledTimes(2)
    }
  )

  it.each(['resolve', 'reject'] as const)(
    'ignores a late %s after changing the route room key',
    async (completion) => {
      const old = deferredLifecycle()
      fetchRoomLifecycle
        .mockReturnValueOnce(old.promise)
        .mockResolvedValue({ state: 'open' })
      const { rerender } = render(
        <MeetingLifecycleProvider key={roomA} roomId={roomA}>
          <ObservationProbe />
        </MeetingLifecycleProvider>
      )
      fireEvent.click(screen.getByText('observe client'))
      const oldSignal = fetchRoomLifecycle.mock.calls[0][1] as AbortSignal
      rerender(
        <MeetingLifecycleProvider key={roomB} roomId={roomB}>
          <ObservationProbe />
        </MeetingLifecycleProvider>
      )
      expect(oldSignal.aborted).toBe(true)
      fireEvent.click(screen.getByText('observe client'))
      await act(async () => undefined)
      await act(async () => {
        if (completion === 'resolve') old.resolve({ state: 'ended' })
        else old.reject(new ApiError(410, { message: 'gone' }))
      })
      expect(screen.getByLabelText('client').textContent).toBe('active:open')
      await act(async () => vi.advanceTimersByTimeAsync(5_000))
      expect(fetchRoomLifecycle).toHaveBeenCalledTimes(2)
      expect(fetchRoomLifecycle.mock.calls[1][0]).toBe(roomB)
    }
  )

  it('aborts and stops observation after unmount', async () => {
    const pending = deferredLifecycle()
    fetchRoomLifecycle.mockReturnValue(pending.promise)
    const { unmount } = render(
      <MeetingLifecycleProvider roomId={roomA}>
        <ObservationProbe />
      </MeetingLifecycleProvider>
    )
    fireEvent.click(screen.getByText('observe client'))
    const signal = fetchRoomLifecycle.mock.calls[0][1] as AbortSignal
    unmount()
    await act(async () => pending.resolve({ state: 'ending' }))
    await act(async () => vi.advanceTimersByTimeAsync(5_000))
    expect(signal.aborted).toBe(true)
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
  })

  it('preserves a refreshed close id through open and ending observations while retrying the command', async () => {
    window.sessionStorage.setItem(
      `mastrao-meeting-close-v1:${roomA}`,
      'close_restored'
    )
    fetchRoomLifecycle
      .mockResolvedValueOnce({ state: 'open' })
      .mockResolvedValue({ state: 'ending' })
    endMeeting
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ state: 'ending' })
    render(
      <MeetingLifecycleProvider roomId={roomA}>
        <ObservationProbe />
      </MeetingLifecycleProvider>
    )
    await act(async () => undefined)
    expect(screen.getByLabelText('client').textContent).toBe('uncertain:open')
    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(screen.getByLabelText('client').textContent).toBe('uncertain:ending')
    await act(async () => vi.advanceTimersByTimeAsync(4_000))
    expect(endMeeting).toHaveBeenCalledTimes(2)
    expect(endMeeting.mock.calls.map((call) => call[1])).toEqual([
      'close_restored',
      'close_restored',
    ])
    expect(screen.getByLabelText('client').textContent).toBe('ending:ending')
  })

  it('keeps observing a canonical close after the recovery command is refused', async () => {
    window.sessionStorage.setItem(
      `mastrao-meeting-close-v1:${roomA}`,
      'close_restored'
    )
    let refuse!: (error: unknown) => void
    endMeeting.mockReturnValueOnce(
      new Promise((_, reject) => {
        refuse = reject
      })
    )
    fetchRoomLifecycle
      .mockResolvedValueOnce({ state: 'ending' })
      .mockRejectedValueOnce(new ApiError(503, { message: 'unavailable' }))
      .mockResolvedValueOnce({ state: 'ended' })
    render(
      <MeetingLifecycleProvider roomId={roomA}>
        <ObservationProbe />
      </MeetingLifecycleProvider>
    )
    await act(async () => undefined)
    expect(screen.getByLabelText('client').textContent).toBe('uncertain:ending')
    await act(async () => refuse(new ApiError(409, { message: 'conflict' })))
    expect(screen.getByLabelText('client').textContent).toBe('ending:ending')
    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(screen.getByLabelText('client').textContent).toBe('ending:ending')
    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(screen.getByLabelText('client').textContent).toBe('ended:ended')
    expect(endMeeting).toHaveBeenCalledOnce()
    expect(fetchRoomLifecycle).toHaveBeenCalledTimes(3)
  })

  it('converges two independent clients on the canonical end without a guest close command', async () => {
    let state: 'ending' | 'ended' = 'ending'
    fetchRoomLifecycle.mockImplementation(async () => ({ state }))
    endMeeting.mockResolvedValue({ state: 'ending' })
    render(
      <>
        <MeetingLifecycleProvider roomId={roomA}>
          <ObservationProbe name="host" />
        </MeetingLifecycleProvider>
        <MeetingLifecycleProvider roomId={roomA}>
          <ObservationProbe name="guest" />
        </MeetingLifecycleProvider>
      </>
    )
    fireEvent.click(screen.getByText('close host'))
    // The guest receives the same lifecycle trigger as a ROOM_DELETED event.
    fireEvent.click(screen.getByText('observe guest'))
    await act(async () => undefined)
    expect(screen.getByLabelText('host').textContent).toBe('ending:ending')
    expect(screen.getByLabelText('guest').textContent).toBe('ending:ending')
    state = 'ended'
    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(screen.getByLabelText('host').textContent).toBe('ended:ended')
    expect(screen.getByLabelText('guest').textContent).toBe('ended:ended')
    expect(endMeeting).toHaveBeenCalledOnce()
    expect(
      window.sessionStorage.getItem(`mastrao-meeting-close-v1:${roomA}`)
    ).toBeNull()
  })
})
