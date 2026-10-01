import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { layoutStore } from '@/stores/layout'
import { useSubtitles } from './useSubtitles'

const startSubtitleMock = vi.hoisted(() => vi.fn())
const useRoomContextMock = vi.hoisted(() => vi.fn())
const useRoomDataMock = vi.hoisted(() => vi.fn())
const useStartSubtitleMock = vi.hoisted(() => vi.fn())

vi.mock('@livekit/components-react', () => ({
  useRoomContext: useRoomContextMock,
}))

vi.mock('@/features/rooms/livekit/hooks/useRoomData', () => ({
  useRoomData: useRoomDataMock,
}))

vi.mock('../api/startSubtitle', () => ({
  useStartSubtitle: useStartSubtitleMock,
}))

const room = {
  on: vi.fn(),
  off: vi.fn(),
}

beforeEach(() => {
  layoutStore.showSubtitles = false
  useRoomContextMock.mockReturnValue(room)
  useRoomDataMock.mockReturnValue({
    livekit: { room: 'room-id', token: 'room-token' },
  })
  useStartSubtitleMock.mockReturnValue({
    mutateAsync: startSubtitleMock,
    isPending: false,
  })
  startSubtitleMock.mockResolvedValue({})
})

afterEach(() => {
  layoutStore.showSubtitles = false
  vi.clearAllMocks()
})

describe('useSubtitles', () => {
  it('keeps the CC button flow and does not start again when closing', async () => {
    const { result } = renderHook(() => useSubtitles())

    await act(async () => {
      await result.current.toggleSubtitles()
    })
    await act(async () => {
      await result.current.toggleSubtitles()
    })

    expect(startSubtitleMock).toHaveBeenCalledOnce()
    expect(layoutStore.showSubtitles).toBe(false)
  })

  it('leaves CC closed after failure and retries the ON intent', async () => {
    const failure = new Error('subtitle unavailable')
    startSubtitleMock.mockRejectedValueOnce(failure).mockResolvedValueOnce({})
    const { result } = renderHook(() => useSubtitles())

    await expect(result.current.toggleSubtitles()).rejects.toBe(failure)
    expect(layoutStore.showSubtitles).toBe(false)

    await act(async () => {
      await result.current.toggleSubtitles()
    })

    expect(startSubtitleMock).toHaveBeenCalledTimes(2)
    expect(layoutStore.showSubtitles).toBe(true)
  })

  it('keeps the start callback stable across equivalent room refreshes', () => {
    const { result, rerender } = renderHook(() => useSubtitles())
    const initialCallback = result.current.ensureSubtitlesStarted

    useRoomDataMock.mockReturnValue({
      livekit: { room: 'room-id', token: 'room-token' },
    })
    rerender()

    expect(result.current.ensureSubtitlesStarted).toBe(initialCallback)
  })
})
