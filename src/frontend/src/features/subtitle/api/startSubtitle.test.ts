import { afterEach, describe, expect, it, vi } from 'vitest'
import { startSubtitle } from './startSubtitle'

const fetchApiMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/fetchApi', () => ({
  fetchApi: fetchApiMock,
}))

const params = {
  id: 'room_0123456789abcdef0123456789abcdef',
  token: 'room-token',
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('startSubtitle', () => {
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
