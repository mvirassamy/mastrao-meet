import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useConfig } from '@/api/useConfig'
import { useAreSubtitlesAvailable } from './useAreSubtitlesAvailable'

vi.mock('@/api/useConfig', () => ({
  useConfig: vi.fn(),
}))

const useConfigMock = vi.mocked(useConfig)

afterEach(() => {
  vi.clearAllMocks()
})

describe('useAreSubtitlesAvailable', () => {
  it('uses only the runtime subtitle capability', () => {
    useConfigMock.mockReturnValue({
      data: { subtitle: { enabled: true } },
    } as ReturnType<typeof useConfig>)

    const { result } = renderHook(() => useAreSubtitlesAvailable())

    expect(result.current).toBe(true)
  })

  it('hides subtitles when the runtime capability is disabled', () => {
    useConfigMock.mockReturnValue({
      data: { subtitle: { enabled: false } },
    } as ReturnType<typeof useConfig>)

    const { result } = renderHook(() => useAreSubtitlesAvailable())

    expect(result.current).toBe(false)
  })
})
