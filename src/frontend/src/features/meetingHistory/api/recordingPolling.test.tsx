import { act, cleanup, renderHook } from '@testing-library/react'
import {
  focusManager,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { useMeetingHistory, useMeetingHistoryDetail } from './useMeetingHistory'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
const fetchMock = vi.mocked(fetchApi)
const clients: QueryClient[] = []

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-11T12:00:00Z'))
  focusManager.setFocused(true)
  fetchMock.mockReset()
})
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  focusManager.setFocused(undefined)
  vi.useRealTimers()
})

const wrapper = ({ children }: { children: ReactNode }) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  clients.push(client)
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
const advance = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}
const response = (status: string) => ({
  id: 'm1',
  started_at: '2026-10-11T11:00:00Z',
  summary_status: 'failed',
  transcript_status: 'failed',
  recording_status: status,
  recording: { status },
})

describe('video polling on the real history hooks', () => {
  it.each(['available', 'expired', 'failed', 'absent'])(
    'polls a processing list and stops at %s',
    async (terminalStatus) => {
      fetchMock
        .mockResolvedValueOnce({ results: [response('processing')] })
        .mockResolvedValue({ results: [response(terminalStatus)] })
      const hook = renderHook(() => useMeetingHistory(), { wrapper })
      await advance(20)
      expect(hook.result.current.data?.pages[0].items[0].recordingStatus).toBe(
        'processing'
      )
      await advance(4_400)
      expect(fetchMock).toHaveBeenCalledTimes(1)
      await advance(1_200)
      expect(hook.result.current.data?.pages[0].items[0].recordingStatus).toBe(
        terminalStatus
      )
      await advance(120_000)
      expect(fetchMock).toHaveBeenCalledTimes(2)
    }
  )

  it('backs off while checking unknown video detail and stops when available', async () => {
    fetchMock
      .mockResolvedValueOnce(response('unknown'))
      .mockResolvedValueOnce(response('processing'))
      .mockResolvedValue(response('available'))
    const hook = renderHook(() => useMeetingHistoryDetail('m1'), { wrapper })
    await advance(20)
    expect(hook.result.current.data?.recording.status).toBe('unknown')
    await advance(5_600)
    expect(hook.result.current.data?.recording.status).toBe('processing')
    await advance(10_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await advance(7_000)
    expect(hook.result.current.data?.recording.status).toBe('available')
    await advance(120_000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('does not add video polling for an older response without recording fields', async () => {
    fetchMock.mockResolvedValue({
      id: 'm1',
      started_at: '2026-10-11T11:00:00Z',
      summary_status: 'failed',
      transcript_status: 'failed',
    })
    const hook = renderHook(() => useMeetingHistoryDetail('m1'), { wrapper })
    await advance(20)
    expect(hook.result.current.data?.recording.status).toBe('absent')
    await advance(120_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
