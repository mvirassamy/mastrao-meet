import {
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiConfig } from '@/api/useConfig'
import { RecordingMode } from '@/features/recording/types'
import { useHasRecordingAccess } from '@/features/recording/hooks/useHasRecordingAccess'
import { useHasFeatureWithoutAdminRights } from '@/features/recording/hooks/useHasFeatureWithoutAdminRights'
import { TranscriptMenuItem } from '@/features/rooms/livekit/components/controls/Options/TranscriptMenuItem'
import { ScreenRecordingMenuItem } from '@/features/rooms/livekit/components/controls/Options/ScreenRecordingMenuItem'

let config: Partial<ApiConfig> | undefined
let role: string | undefined
const featureFlag = vi.hoisted(() => vi.fn())
const openTranscript = vi.hoisted(() => vi.fn())
const openScreenRecording = vi.hoisted(() => vi.fn())
vi.mock('@/api/useConfig', () => ({ useConfig: () => ({ data: config }) }))
vi.mock('posthog-js/react', () => ({ useFeatureFlagEnabled: featureFlag }))
vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => ({ localParticipant: {} }),
  useParticipantAttribute: () => role,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('react-aria-components', () => ({
  MenuItem: ({
    children,
    onAction,
  }: {
    children: ReactNode
    onAction: () => void
  }) => <button onClick={onAction}>{children}</button>,
}))
vi.mock('@/features/rooms/livekit/hooks/useSidePanel', () => ({
  useSidePanel: () => ({
    openTranscript,
    openScreenRecording,
    toggleTools: vi.fn(),
  }),
}))
// Keep the actual access hooks while avoiding unrelated recording UI imports.
vi.mock('@/features/recording', async () => ({
  ...(await import('@/features/recording/types')),
  ...(await import('@/features/recording/hooks/useHasRecordingAccess')),
}))

beforeEach(() => {
  vi.clearAllMocks()
  featureFlag.mockReturnValue(false)
  role = 'owner'
  config = {
    analytics: { id: 'analytics-test', host: 'https://analytics.test' },
    recording: {
      is_enabled: true,
      available_modes: Object.values(RecordingMode),
    },
  }
})
afterEach(cleanup)

describe.each(Object.values(RecordingMode))('%s availability', (mode) => {
  it.each(['owner', 'administrator'])(
    'allows %s when rollout is disabled',
    (currentRole) => {
      role = currentRole
      const { result } = renderHook(() => useHasRecordingAccess(mode))
      expect(result.current).toBe(true)
    }
  )

  it.each(['member', 'guest', undefined])(
    'does not grant recording control to %s',
    (currentRole) => {
      role = currentRole
      const { result } = renderHook(() => useHasRecordingAccess(mode))
      expect(result.current).toBeFalsy()
    }
  )

  it('keeps non-admin feature availability separate from recording control', () => {
    role = 'member'
    const { result } = renderHook(() => ({
      control: useHasRecordingAccess(mode),
      available: useHasFeatureWithoutAdminRights(mode),
    }))
    expect(result.current).toEqual({ control: false, available: true })
  })

  it.each(['owner', 'administrator'])(
    'does not offer the non-admin path to %s',
    (currentRole) => {
      role = currentRole
      const { result } = renderHook(() => useHasFeatureWithoutAdminRights(mode))
      expect(result.current).toBe(false)
    }
  )

  it.each([
    undefined,
    {},
    {
      recording: {
        is_enabled: false,
        available_modes: Object.values(RecordingMode),
      },
    },
    { recording: { is_enabled: true } },
    { recording: { is_enabled: true, available_modes: [] } },
  ])(
    'retains missing or disabled server capability gates: %j',
    (serverConfig) => {
      config = serverConfig
      const { result, rerender } = renderHook(() => ({
        control: useHasRecordingAccess(mode),
        available: useHasFeatureWithoutAdminRights(mode),
      }))
      expect(result.current.control).toBeFalsy()
      role = 'member'
      rerender()
      expect(result.current.available).toBeFalsy()
    }
  )

  it('does not grant a mode merely because the other mode is enabled', () => {
    config!.recording!.available_modes = Object.values(RecordingMode).filter(
      (value) => value !== mode
    )
    const { result } = renderHook(() => useHasRecordingAccess(mode))
    expect(result.current).toBe(false)
  })

  it.each([true, false, undefined])(
    'is independent of PostHog value %s and analytics configuration',
    (flagValue) => {
      featureFlag.mockReturnValue(flagValue)
      const { result, rerender } = renderHook(() => useHasRecordingAccess(mode))
      expect(result.current).toBe(true)
      config!.analytics = undefined
      rerender()
      expect(result.current).toBe(true)
      expect(featureFlag).not.toHaveBeenCalled()
    }
  )
})

it('opens both permitted recording panels even when rollout flags are disabled', () => {
  render(
    <>
      <TranscriptMenuItem />
      <ScreenRecordingMenuItem />
    </>
  )
  fireEvent.click(screen.getByRole('button', { name: 'transcript' }))
  fireEvent.click(screen.getByRole('button', { name: 'screenRecording' }))
  expect(openTranscript).toHaveBeenCalledOnce()
  expect(openScreenRecording).toHaveBeenCalledOnce()
  expect(featureFlag).not.toHaveBeenCalled()
})
