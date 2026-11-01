import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RoomEvent } from 'livekit-client'
import { layoutStore } from '@/stores/layout'
import { useSubtitles } from './useSubtitles'

const useRoomContextMock = vi.hoisted(() => vi.fn())

vi.mock('@livekit/components-react', () => ({
  useRoomContext: useRoomContextMock,
}))

let disconnected: (() => void) | undefined
const room = {
  on: vi.fn((event: RoomEvent, callback: () => void) => {
    if (event === RoomEvent.Disconnected) disconnected = callback
  }),
  off: vi.fn(),
}

beforeEach(() => {
  layoutStore.showSubtitles = false
  disconnected = undefined
  useRoomContextMock.mockReturnValue(room)
})

afterEach(() => {
  layoutStore.showSubtitles = false
  vi.clearAllMocks()
})

describe('useSubtitles', () => {
  it('toggles only the local panel without starting a second pipeline', () => {
    const { result, rerender } = renderHook(() => useSubtitles())

    act(() => result.current.toggleSubtitles())
    expect(layoutStore.showSubtitles).toBe(true)
    expect(result.current.areSubtitlesPending).toBe(false)

    rerender()
    act(() => result.current.toggleSubtitles())
    expect(layoutStore.showSubtitles).toBe(false)
  })

  it('closes the panel when the LiveKit room disconnects', () => {
    layoutStore.showSubtitles = true
    renderHook(() => useSubtitles())

    act(() => disconnected?.())

    expect(layoutStore.showSubtitles).toBe(false)
  })
})
