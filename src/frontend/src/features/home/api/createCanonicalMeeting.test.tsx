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
      createCanonicalMeeting({ idempotencyKey: 'meet_0123456789abcdef' })
    ).resolves.toEqual({
      meetingRef: response.meeting_ref,
      roomRef: response.room_ref,
    })
    expect(fetchApiMock).toHaveBeenCalledWith('meetings/', {
      method: 'POST',
      headers: { 'X-Idempotency-Key': 'meet_0123456789abcdef' },
    })
  })

  it('sends UTC seconds and confirms the canonical persisted metadata', async () => {
    const schedule = {
      title: 'Équipe',
      startsAt: 1800000000,
      endsAt: 1800003600,
      timeZone: 'Europe/Paris',
    }
    const metadata = {
      title: schedule.title,
      scheduled_start_at: schedule.startsAt,
      scheduled_end_at: schedule.endsAt,
      timezone: schedule.timeZone,
    }
    fetchApiMock.mockResolvedValue({ ...response, ...metadata })
    await createCanonicalMeeting({
      idempotencyKey: 'meet_0123456789abcdef',
      schedule,
    })
    expect(JSON.parse(fetchApiMock.mock.calls[0][1]?.body as string)).toEqual(
      metadata
    )
    fetchApiMock.mockResolvedValue(response)
    await expect(
      createCanonicalMeeting({
        idempotencyKey: 'meet_0123456789abcdef',
        schedule,
      })
    ).rejects.toBeInstanceOf(CanonicalMeetingContractError)
  })

  it('omits an empty optional title and accepts the canonical null title', async () => {
    const schedule = {
      title: '',
      startsAt: 1800000000,
      endsAt: 1800003600,
      timeZone: 'Europe/Paris',
    }
    fetchApiMock.mockResolvedValue({
      ...response,
      title: null,
      scheduled_start_at: schedule.startsAt,
      scheduled_end_at: schedule.endsAt,
      timezone: schedule.timeZone,
    })
    await createCanonicalMeeting({
      idempotencyKey: 'meet_0123456789abcdef',
      schedule,
    })
    expect(
      JSON.parse(fetchApiMock.mock.calls[0][1]?.body as string)
    ).not.toHaveProperty('title')
  })

  it('refuses an invalid key and an unexpected response', async () => {
    await expect(
      createCanonicalMeeting({ idempotencyKey: 'short' })
    ).rejects.toThrow()
    expect(fetchApiMock).not.toHaveBeenCalled()
    fetchApiMock.mockResolvedValue({ room_ref: 'room_1' })
    await expect(
      createCanonicalMeeting({ idempotencyKey: 'meet_0123456789abcdef' })
    ).rejects.toBeInstanceOf(CanonicalMeetingContractError)
  })

  it('replays the same key when a retryable failure is retried', async () => {
    const client = new QueryClient({
      defaultOptions: { mutations: { retryDelay: 0 } },
    })
    const { result } = renderHook(() => useCreateCanonicalMeeting(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    })
    const key = createIdempotencyKey()
    const schedule = {
      title: 'Équipe',
      startsAt: 1800000000,
      endsAt: 1800003600,
      timeZone: 'Europe/Paris',
    }
    fetchApiMock
      .mockRejectedValueOnce(new ApiError(503, {}))
      .mockResolvedValueOnce({
        ...response,
        title: schedule.title,
        scheduled_start_at: schedule.startsAt,
        scheduled_end_at: schedule.endsAt,
        timezone: schedule.timeZone,
      })
    result.current.mutate({ idempotencyKey: key, schedule })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(fetchApiMock).toHaveBeenCalledTimes(2)
    expect(fetchApiMock.mock.calls[1]).toEqual(fetchApiMock.mock.calls[0])
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
    result.current.mutate({ idempotencyKey: createIdempotencyKey() })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(fetchApiMock).toHaveBeenCalledTimes(1)
  })
})
