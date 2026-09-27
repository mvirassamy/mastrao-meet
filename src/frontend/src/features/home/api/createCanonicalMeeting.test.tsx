import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import {
  CanonicalMeetingContractError,
  createCanonicalMeeting,
  createIdempotencyKey,
  useCreateCanonicalMeeting,
} from './createCanonicalMeeting'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

const fetchApiMock = vi.mocked(fetchApi)
const response = {
  meeting_ref: 'meeting_0123456789abcdef',
  room_ref: 'room_0123456789abcdef',
}

beforeEach(() => {
  fetchApiMock.mockReset()
})
afterEach(cleanup)

describe('createCanonicalMeeting', () => {
  it('generates keys accepted by the backend, unique per action', () => {
    const keys = new Set(Array.from({ length: 50 }, createIdempotencyKey))
    expect(keys.size).toBe(50)
    for (const key of keys) expect(key).toMatch(/^[A-Za-z0-9_-]{16,128}$/)
  })

  it('posts with the idempotency header and reads the response', async () => {
    fetchApiMock.mockResolvedValue(response)
    await expect(
      createCanonicalMeeting('meet_0123456789abcdef')
    ).resolves.toEqual({
      meetingRef: response.meeting_ref,
      roomRef: response.room_ref,
    })
    expect(fetchApiMock).toHaveBeenCalledWith('meetings/', {
      method: 'POST',
      headers: { 'X-Idempotency-Key': 'meet_0123456789abcdef' },
    })
  })

  it('refuses an invalid key and an unexpected response', async () => {
    await expect(createCanonicalMeeting('short')).rejects.toThrow()
    expect(fetchApiMock).not.toHaveBeenCalled()
    fetchApiMock.mockResolvedValue({ room_ref: 'room_1' })
    await expect(
      createCanonicalMeeting('meet_0123456789abcdef')
    ).rejects.toBeInstanceOf(CanonicalMeetingContractError)
  })

  it('replays the same key when a retryable failure is retried', async () => {
    fetchApiMock
      .mockRejectedValueOnce(new ApiError(503, {}))
      .mockResolvedValueOnce(response)
    const client = new QueryClient({
      defaultOptions: { mutations: { retryDelay: 0 } },
    })
    const { result } = renderHook(() => useCreateCanonicalMeeting(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    })
    const key = createIdempotencyKey()
    result.current.mutate(key)
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(fetchApiMock).toHaveBeenCalledTimes(2)
    for (const [, options] of fetchApiMock.mock.calls)
      expect(options?.headers).toEqual({ 'X-Idempotency-Key': key })
  })

  it('does not retry a refusal', async () => {
    fetchApiMock.mockRejectedValue(new ApiError(422, {}))
    const client = new QueryClient({
      defaultOptions: { mutations: { retryDelay: 0 } },
    })
    const { result } = renderHook(() => useCreateCanonicalMeeting(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    })
    result.current.mutate(createIdempotencyKey())
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(fetchApiMock).toHaveBeenCalledTimes(1)
  })
})
