import { cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useNoiseReductionAvailable } from './useNoiseReductionAvailable'
import { useNoiseReduction } from './useNoiseReduction'

const mocks = vi.hoisted(() => ({
  mobile: false,
  analytics: true,
  enabled: false,
  hasTrack: true,
  processor: undefined as object | undefined,
  flag: vi.fn(),
  setProcessor: vi.fn(),
  stopProcessor: vi.fn(),
}))
vi.mock('posthog-js/react', () => ({ useFeatureFlagEnabled: mocks.flag }))
vi.mock('@/features/analytics/hooks/useIsAnalyticsEnabled', () => ({
  useIsAnalyticsEnabled: () => mocks.analytics,
}))
vi.mock('@livekit/components-core', () => ({
  isMobileBrowser: () => mocks.mobile,
}))
vi.mock('livekit-client', () => ({
  Track: { Source: { Microphone: 'microphone' } },
}))
const audioTrack = {
  getProcessor: () => mocks.processor,
  setProcessor: mocks.setProcessor,
  stopProcessor: mocks.stopProcessor,
}
vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => ({
    localParticipant: {
      getTrackPublication: () => (mocks.hasTrack ? { audioTrack } : undefined),
    },
  }),
}))
vi.mock('valtio', () => ({
  useSnapshot: () => ({ noiseReductionEnabled: mocks.enabled }),
}))
vi.mock('@/stores/userChoices', () => ({ userChoicesStore: {} }))
vi.mock('../processors/RnnNoiseProcessor', () => ({
  RnnNoiseProcessor: class {},
}))

beforeEach(() => {
  vi.clearAllMocks()
  Object.assign(mocks, {
    mobile: false,
    analytics: true,
    enabled: false,
    hasTrack: true,
    processor: undefined,
  })
  mocks.flag.mockReturnValue(false)
})
afterEach(cleanup)

describe('noise reduction availability', () => {
  it.each([true, false, undefined])(
    'is available on desktop regardless of rollout value %s or analytics',
    (flag) => {
      mocks.flag.mockReturnValue(flag)
      const { result, rerender } = renderHook(useNoiseReductionAvailable)
      expect(result.current).toBe(true)
      mocks.analytics = false
      rerender()
      expect(result.current).toBe(true)
      expect(mocks.flag).not.toHaveBeenCalled()
    }
  )

  it.each([true, false, undefined])(
    'remains unavailable on mobile with rollout value %s',
    (flag) => {
      mocks.mobile = true
      mocks.flag.mockReturnValue(flag)
      const { result, rerender } = renderHook(useNoiseReductionAvailable)
      expect(result.current).toBe(false)
      mocks.analytics = false
      rerender()
      expect(result.current).toBe(false)
    }
  )
})

describe('noise reduction user choice', () => {
  it('does not start processing before the user enables it', () => {
    const { rerender } = renderHook(useNoiseReduction)
    expect(mocks.setProcessor).not.toHaveBeenCalled()
    expect(mocks.stopProcessor).not.toHaveBeenCalled()
    mocks.enabled = true
    rerender()
    expect(mocks.setProcessor).toHaveBeenCalledOnce()
    mocks.processor = mocks.setProcessor.mock.calls[0][0]
    mocks.enabled = false
    rerender()
    expect(mocks.stopProcessor).toHaveBeenCalledOnce()
  })

  it('honors an already enabled preference on desktop', () => {
    mocks.enabled = true
    renderHook(useNoiseReduction)
    expect(mocks.setProcessor).toHaveBeenCalledOnce()
  })

  it('never starts processing on mobile even with an enabled preference', () => {
    mocks.mobile = true
    mocks.enabled = true
    renderHook(useNoiseReduction)
    expect(mocks.setProcessor).not.toHaveBeenCalled()
    expect(mocks.stopProcessor).not.toHaveBeenCalled()
  })

  it('does not start processing without a microphone track', () => {
    mocks.hasTrack = false
    mocks.enabled = true
    renderHook(useNoiseReduction)
    expect(mocks.setProcessor).not.toHaveBeenCalled()
  })

  it('does not replace an existing processor', () => {
    mocks.enabled = true
    mocks.processor = {}
    renderHook(useNoiseReduction)
    expect(mocks.setProcessor).not.toHaveBeenCalled()
    expect(mocks.stopProcessor).not.toHaveBeenCalled()
  })
})
