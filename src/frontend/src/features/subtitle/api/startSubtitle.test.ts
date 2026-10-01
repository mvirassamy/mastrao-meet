import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startSubtitle, useStartSubtitle } from './startSubtitle'

const fetchApiMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/fetchApi', () => ({
  fetchApi: fetchApiMock,
}))

const params = {
  id: 'room_0123456789abcdef0123456789abcdef',
  token: 'room-token',
}

const wrapperFor = (client: QueryClient) => {
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  return Wrapper
}

beforeEach(() => {
  fetchApiMock.mockReset()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('startSubtitle', () => {
  it('reuses the pending POST when a consumer remounts', async () => {
    let resolveRequest: ((value: unknown) => void) | undefined
    fetchApiMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRequest = resolve
      })
    )
    const wrapper = wrapperFor(new QueryClient())
    const firstConsumer = renderHook(() => useStartSubtitle(), { wrapper })
    const firstRequest = firstConsumer.result.current.mutateAsync(params)
    await waitFor(() =>
      expect(firstConsumer.result.current.isPending).toBe(true)
    )

    firstConsumer.unmount()
    const secondConsumer = renderHook(() => useStartSubtitle(), { wrapper })
    const secondRequest = secondConsumer.result.current.mutateAsync({
      ...params,
      token: 'refreshed-room-token',
    })
    await waitFor(() =>
      expect(secondConsumer.result.current.isPending).toBe(true)
    )

    expect(fetchApiMock).toHaveBeenCalledOnce()
    resolveRequest?.({})
    await act(async () => {
      await Promise.all([firstRequest, secondRequest])
    })
  })

  it('reuses one pending ON request for concurrent callers', async () => {
    let resolveRequest: ((value: unknown) => void) | undefined
    fetchApiMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRequest = resolve
      })
    )

    const firstRequest = startSubtitle(params)
    const secondRequest = startSubtitle(params)

    expect(fetchApiMock).toHaveBeenCalledOnce()
    expect(secondRequest).toBe(firstRequest)

    resolveRequest?.({})
    await Promise.all([firstRequest, secondRequest])
  })

  it('allows a retry after the ON request fails', async () => {
    const failure = new Error('subtitle unavailable')
    fetchApiMock.mockRejectedValueOnce(failure).mockResolvedValueOnce({})

    await expect(startSubtitle(params)).rejects.toBe(failure)
    await expect(startSubtitle(params)).resolves.toEqual({})

    expect(fetchApiMock).toHaveBeenCalledTimes(2)
  })
})
