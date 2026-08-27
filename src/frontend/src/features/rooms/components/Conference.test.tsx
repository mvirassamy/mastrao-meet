import { render, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Conference } from './Conference'

const createRoom = vi.fn()
const fetchRoomLifecycle = vi.fn()
const fetchRoom = vi.fn()
const navigateTo = vi.fn()
let liveKitOnDisconnected: ((reason: number) => void) | undefined
const markActive = vi.fn()
const markEnding = vi.fn()

let lifecyclePhase: 'active' | 'requesting' | 'ending' | 'uncertain' | 'ended' =
  'active'
let lifecycleCloseRequestId: string | undefined

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryFn }: { queryFn: () => Promise<unknown> }) => {
    void queryFn().catch(() => undefined)
    return {
      status: 'pending',
      isError: false,
      data: undefined,
      refetch: vi.fn(),
    }
  },
}))

vi.mock('@livekit/components-react', () => ({
  LiveKitRoom: ({
    children,
    onDisconnected,
  }: {
    children: ReactNode
    onDisconnected?: (reason: number) => void
  }) => {
    liveKitOnDisconnected = onDisconnected
    return <>{children}</>
  },
  usePersistentUserChoices: () => ({ userChoices: {} }),
}))

vi.mock('livekit-client', () => ({
  DisconnectReason: {
    ROOM_DELETED: 4,
    CLIENT_INITIATED: 1,
    DUPLICATE_IDENTITY: 2,
    PARTICIPANT_REMOVED: 3,
  },
  MediaDeviceFailure: { getFailure: () => undefined },
  Room: class {
    numParticipants = 0
    localParticipant = { setMicrophoneEnabled: vi.fn() }
    prepareConnection = vi.fn()
  },
  VideoPresets: {},
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/api/useConfig', () => ({
  useConfig: () => ({ data: undefined }),
}))

vi.mock('@/api/queryClient', () => ({
  queryClient: {
    getQueryData: vi.fn(),
    setQueryData: vi.fn(),
    removeQueries: vi.fn(),
  },
}))

vi.mock('../api/fetchRoom', () => ({
  fetchRoom: (...args: unknown[]) => fetchRoom(...args),
}))

vi.mock('../api/fetchRoomLifecycle', () => ({
  fetchRoomLifecycle: (...args: unknown[]) => fetchRoomLifecycle(...args),
}))

vi.mock('../contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({
    phase: lifecyclePhase,
    isEnding: lifecyclePhase !== 'active',
    closeRequestId: lifecycleCloseRequestId,
    beginEnding: vi.fn(),
    markActive,
    markEnding,
    markEndingUncertain: vi.fn(),
    markEnded: vi.fn(),
  }),
}))

vi.mock('@/navigation/navigateTo', () => ({
  navigateTo: (...args: unknown[]) => navigateTo(...args),
}))

vi.mock('../api/createRoom', () => ({
  useCreateRoom: () => ({
    mutateAsync: createRoom,
    status: 'idle',
    isError: false,
  }),
}))

vi.mock('@/stores/user', () => ({ userStore: { username: 'Host' } }))
vi.mock('@/stores/userPreferences', () => ({ userPreferencesStore: {} }))
vi.mock('valtio', () => ({ useSnapshot: (value: unknown) => value }))
vi.mock('@/stores/connectionObserver', () => ({
  connectionObserverStore: {
    publisher: null,
    publisherChangesCount: 0,
    subscriber: null,
    subscriberChangesCount: 0,
  },
}))
vi.mock('@/utils/useIsMobile', () => ({ useIsMobile: () => false }))
vi.mock('@/utils/livekit', () => ({ isFireFox: () => false }))
vi.mock('@/features/analytics/telemetry', () => ({
  captureMediaEvent: vi.fn(),
  reportError: vi.fn(),
}))
vi.mock('@/features/notifications/utils', () => ({
  notifyAutoMutedOnJoin: vi.fn(),
}))
vi.mock('../api/recordingConsent', () => ({ activateRecording: vi.fn() }))

vi.mock('@/components/QueryAware', () => ({
  QueryAware: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock('@/layout/Screen', () => ({
  Screen: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock('@/components/ErrorScreen', () => ({ ErrorScreen: () => null }))
vi.mock('./InviteDialog', () => ({ InviteDialog: () => null }))
vi.mock('./WatchMediaDeviceErrors', () => ({
  WatchMediaDeviceErrors: () => null,
}))
vi.mock('../livekit/prefabs/VideoConference', () => ({
  VideoConference: () => null,
}))
vi.mock('@/features/pip/components/PictureInPictureConference', () => ({
  PictureInPictureConference: () => null,
}))
vi.mock('../livekit/components/blur', () => ({
  BackgroundProcessorFactory: { fromProcessorConfig: () => undefined },
}))
vi.mock('@/primitives', () => ({ Button: () => null }))
vi.mock('@/styled-system/css', () => ({ css: () => '' }))

describe('Conference room lookup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    liveKitOnDisconnected = undefined
    lifecyclePhase = 'active'
    lifecycleCloseRequestId = undefined
  })

  it.each(['404', '410'])(
    'never tries to create a canonical room after a %s',
    async (statusCode) => {
      fetchRoom.mockRejectedValue({ statusCode })
      render(<Conference roomId="room_0123456789abcdef0123456789abcdef" />)

      await waitFor(() => expect(fetchRoom).toHaveBeenCalledOnce())
      expect(createRoom).not.toHaveBeenCalled()
    }
  )

  it('keeps legacy room deletion on the feedback route', async () => {
    fetchRoom.mockResolvedValue({})
    render(<Conference roomId="abc-defg-hij" />)

    await waitFor(() => expect(liveKitOnDisconnected).toBeDefined())
    liveKitOnDisconnected?.(4)

    expect(navigateTo).toHaveBeenCalledWith(
      'feedback',
      { outcome: 'ended', roomId: 'abc-defg-hij' },
      expect.objectContaining({
        state: expect.objectContaining({ reason: 4 }),
      })
    )
  })

  it('keeps a pending close intent when canonical lifecycle is still open', async () => {
    lifecyclePhase = 'uncertain'
    lifecycleCloseRequestId = 'close_existing'
    fetchRoom.mockResolvedValue({})
    fetchRoomLifecycle.mockResolvedValueOnce({ state: 'open' })

    render(<Conference roomId="room_0123456789abcdef0123456789abcdef" />)

    await waitFor(() => expect(fetchRoomLifecycle).toHaveBeenCalled())
    expect(markActive).not.toHaveBeenCalled()
    expect(markEnding).not.toHaveBeenCalled()
  })
})
