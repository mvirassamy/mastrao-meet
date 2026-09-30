import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSubtitles } from './useSubtitles'

const mocks = vi.hoisted(() => ({
  ensureSubtitleStarted: vi.fn(),
  layout: { showSubtitles: false },
  room: { on: vi.fn(), off: vi.fn() },
  subtitleStartStatus: 'idle',
}))

vi.mock('valtio', () => ({ useSnapshot: () => mocks.layout }))
vi.mock('@/stores/layout', () => ({ layoutStore: mocks.layout }))
vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => mocks.room,
}))
vi.mock('../store/liveTranscriptionContext', () => ({
  useLiveTranscription: () => ({
    ensureSubtitleStarted: mocks.ensureSubtitleStarted,
    subtitleStartStatus: mocks.subtitleStartStatus,
  }),
}))

describe('useSubtitles', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.layout.showSubtitles = false
    mocks.subtitleStartStatus = 'idle'
    mocks.ensureSubtitleStarted.mockResolvedValue(undefined)
  })

  it('uses the shared starter before opening compact captions', async () => {
    const { result } = renderHook(() => useSubtitles())

    await act(() => result.current.toggleSubtitles())

    expect(mocks.ensureSubtitleStarted).toHaveBeenCalledOnce()
    expect(mocks.layout.showSubtitles).toBe(true)
  })

  it('closes compact captions without starting again', async () => {
    mocks.layout.showSubtitles = true
    const { result } = renderHook(() => useSubtitles())

    await act(() => result.current.toggleSubtitles())

    expect(mocks.ensureSubtitleStarted).not.toHaveBeenCalled()
    expect(mocks.layout.showSubtitles).toBe(false)
  })
})
