import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAreSubtitlesAvailable } from './useAreSubtitlesAvailable'

const useConfigMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/useConfig', () => ({ useConfig: useConfigMock }))

describe('useAreSubtitlesAvailable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it.each([
    [true, true],
    [false, false],
  ])('returns the runtime subtitle capability %s', (enabled, expected) => {
    useConfigMock.mockReturnValue({ data: { subtitle: { enabled } } })

    const { result } = renderHook(() => useAreSubtitlesAvailable())

    expect(result.current).toBe(expected)
  })

  it('is unavailable until runtime configuration is loaded', () => {
    useConfigMock.mockReturnValue({ data: undefined })

    const { result } = renderHook(() => useAreSubtitlesAvailable())

    expect(result.current).toBe(false)
  })
})
