import { act, render, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Conference } from './Conference'
import { ApiAccessLevel, type ApiRoom } from '../api/ApiRoom'
import { activateRecording } from '../api/recordingConsent'

const createRoom = vi.fn()
const fetchRoomLifecycle = vi.fn()
const fetchRoom = vi.fn()
const navigateTo = vi.fn()
let liveKitOnDisconnected: ((reason: number) => void) | undefined
let liveKitOnConnected: (() => Promise<void>) | undefined
let liveKitAudio: unknown
let liveKitVideo: unknown
let localTrackPublished: (() => void) | undefined
const refetchRoom = vi.fn().mockResolvedValue(undefined)
const markActive = vi.fn()
const markEnding = vi.fn()

let lifecyclePhase: 'active' | 'requesting' | 'ending' | 'uncertain' | 'ended' =
  'active'
let lifecycleCloseRequestId: string | undefined

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({
    queryFn,
    initialData,
  }: {
    queryFn: () => Promise<unknown>
    initialData?: ApiRoom
  }) => {
    void queryFn().catch(() => undefined)
    return {
      status: 'pending',
      isError: false,
      data: initialData,
      refetch: refetchRoom,
    }
  },
}))

vi.mock('@livekit/components-react', () => ({
  LiveKitRoom: ({
    children,
    onDisconnected,
    onConnected,
    audio,
    video,
  }: {
    children: ReactNode
    onDisconnected?: (reason: number) => void
    onConnected?: () => Promise<void>
    audio?: unknown
    video?: unknown
  }) => {
    liveKitOnDisconnected = onDisconnected
    liveKitOnConnected = onConnected
    liveKitAudio = audio
    liveKitVideo = video
    return <>{children}</>
  },
}))

vi.mock('livekit-client', () => ({
  DisconnectReason: {
    ROOM_DELETED: 4,
    CLIENT_INITIATED: 1,
    DUPLICATE_IDENTITY: 2,
    PARTICIPANT_REMOVED: 3,
  },
  MediaDeviceFailure: { getFailure: () => undefined },
  RoomEvent: {
    LocalTrackPublished: 'localTrackPublished',
    LocalTrackUnpublished: 'localTrackUnpublished',
  },
  Room: class {
    numParticipants = 0
    localParticipant = {
      setMicrophoneEnabled: vi.fn(),
      trackPublications: new Map(),
    }
    prepareConnection = vi.fn()
    on = vi.fn((event: string, handler: () => void) => {
      if (event === 'localTrackPublished') {
        localTrackPublished = () => {
          this.localParticipant.trackPublications.set('audio', {})
          handler()
        }
      }
    })
    off = vi.fn()
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
vi.mock('@/stores/userChoices', () => ({
  userChoicesStore: {
    audioEnabled: true,
    videoEnabled: true,
  },
}))
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
    liveKitOnConnected = undefined
    liveKitAudio = undefined
    liveKitVideo = undefined
    localTrackPublished = undefined
    lifecyclePhase = 'active'
    lifecycleCloseRequestId = undefined
    vi.mocked(activateRecording).mockResolvedValue(
      {} as Awaited<ReturnType<typeof activateRecording>>
    )
  })

  it.each([false, true, undefined])(
    'respects video activation availability %s after connection',
    async (available) => {
      const room: ApiRoom = {
        id: 'room_0123456789abcdef0123456789abcdef',
        name: 'Test',
        slug: 'test',
        access_level: ApiAccessLevel.RESTRICTED,
        is_administrable: true,
        can_end: true,
        recording: {
          mode: 'recorded',
          recording_state: 'collecting',
          decision: 'accepted',
          activation_available: available,
        },
      }
      fetchRoom.mockResolvedValue(room)
      render(<Conference roomId={room.id} initialRoomData={room} />)
      await act(async () => {
        await liveKitOnConnected?.()
      })
      expect(activateRecording).not.toHaveBeenCalled()
      await act(async () => {
        localTrackPublished?.()
      })
      expect(activateRecording).toHaveBeenCalledTimes(
        available === false ? 0 : 1
      )
    }
  )

  it('publishes media from the canonical prejoin choices', () => {
    const room: ApiRoom = {
      id: 'room_0123456789abcdef0123456789abcdef',
      name: 'Test',
      slug: 'test',
      access_level: ApiAccessLevel.RESTRICTED,
      is_administrable: true,
      can_end: true,
    }
    fetchRoom.mockResolvedValue(room)

    render(<Conference roomId={room.id} initialRoomData={room} />)

    expect(liveKitAudio).toBe(true)
    expect(liveKitVideo).toEqual({ processor: undefined })
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
