import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from '@testing-library/react'
import { useState, type ReactNode } from 'react'
import type { LocalVideoTrack } from 'livekit-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { konamiStore } from '@/stores/konami'
import { useHasFunnyEffectsAccess } from './useHasFunnyEffectsAccess'
import { KONAMI_CODE } from './useKonami'
import { FunnyEffects } from '../components/effects/FunnyEffects'

const mocks = vi.hoisted(() => ({
  analytics: true,
  flag: vi.fn(),
  createProcessor: vi.fn(),
}))
vi.mock('posthog-js/react', () => ({ useFeatureFlagEnabled: mocks.flag }))
vi.mock('@/features/analytics/hooks/useIsAnalyticsEnabled', () => ({
  useIsAnalyticsEnabled: () => mocks.analytics,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('../components/blur', () => ({
  ProcessorType: { FACE_LANDMARKS: 'faceLandmarks' },
}))
vi.mock('../components/blur/FaceLandmarksProcessor', () => ({
  FaceLandmarksProcessor: class {
    type = 'faceLandmarks'
    constructor(public options: { showGlasses: boolean; showFrench: boolean }) {
      mocks.createProcessor()
    }
  },
}))
vi.mock('@/features/analytics/telemetry', () => ({ reportError: vi.fn() }))
vi.mock('@/primitives', () => ({
  H: ({ children }: { children: ReactNode }) => <h3>{children}</h3>,
  ToggleButton: ({
    children,
    onChange,
    isDisabled,
    'aria-label': label,
  }: {
    children: ReactNode
    onChange: () => void
    isDisabled?: boolean
    'aria-label': string
  }) => (
    <button aria-label={label} disabled={isDisabled} onClick={onChange}>
      {children}
    </button>
  ),
}))

const typeCode = async (keys = KONAMI_CODE) => {
  await act(async () => {
    for (const keyCode of keys) fireEvent.keyUp(document, { keyCode })
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.analytics = true
  mocks.flag.mockReturnValue(false)
  konamiStore.areFunnyEffectsEnabled = false
})
afterEach(() => {
  cleanup()
  konamiStore.areFunnyEffectsEnabled = false
})

describe('funny effects availability', () => {
  it.each([true, false, undefined])(
    'requires Konami independently of rollout value %s',
    async (flag) => {
      mocks.flag.mockReturnValue(flag)
      const { result, rerender } = renderHook(useHasFunnyEffectsAccess)
      expect(result.current).toBe(false)
      await typeCode([38, 40, 66, 65])
      expect(result.current).toBe(false)
      await typeCode(KONAMI_CODE.slice(0, -1))
      expect(result.current).toBe(false)
      await typeCode(KONAMI_CODE.slice(-1))
      expect(result.current).toBe(true)
      mocks.analytics = false
      rerender()
      expect(result.current).toBe(true)
      await typeCode()
      expect(result.current).toBe(false)
      expect(mocks.flag).not.toHaveBeenCalled()
    }
  )

  it('removes the keyboard listener on unmount', async () => {
    const { unmount } = renderHook(useHasFunnyEffectsAccess)
    unmount()
    await typeCode()
    expect(konamiStore.areFunnyEffectsEnabled).toBe(false)
  })
})

it('does not create or attach a processor until the user selects an effect', async () => {
  let processor:
    | { type: string; options: { showGlasses: boolean; showFrench: boolean } }
    | undefined
  let finishAttachment!: () => void
  const setProcessor = vi.fn(
    (value: NonNullable<typeof processor>) =>
      new Promise<void>((resolve) => {
        finishAttachment = () => {
          processor = value
          resolve()
        }
      })
  )
  const stopProcessor = vi.fn(async () => {
    processor = undefined
  })
  const track = {
    getProcessor: () => processor,
    setProcessor,
    stopProcessor,
  } as unknown as LocalVideoTrack
  const Effects = () => {
    const allowed = useHasFunnyEffectsAccess()
    const [pending, setPending] = useState(false)
    return allowed ? (
      <FunnyEffects
        videoTrack={track}
        isPending={pending}
        onPending={setPending}
      />
    ) : null
  }
  render(<Effects />)
  expect(screen.queryByRole('button')).toBeNull()
  await typeCode()
  expect(
    screen.getByRole('button', { name: 'faceLandmarks.glasses.apply' })
  ).toBeTruthy()
  expect(mocks.createProcessor).not.toHaveBeenCalled()
  expect(setProcessor).not.toHaveBeenCalled()
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'faceLandmarks.glasses.apply' })
    )
  )
  expect(mocks.createProcessor).toHaveBeenCalledOnce()
  expect(setProcessor).toHaveBeenCalledOnce()
  await act(async () => finishAttachment())
  expect(processor?.options).toEqual({ showGlasses: true, showFrench: false })
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'faceLandmarks.glasses.clear' })
    )
  )
  expect(stopProcessor).toHaveBeenCalledOnce()
  await typeCode()
  expect(screen.queryByRole('button')).toBeNull()
})
