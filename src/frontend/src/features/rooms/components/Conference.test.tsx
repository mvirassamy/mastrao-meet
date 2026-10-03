import {
  act,
  cleanup,
  render as renderView,
  waitFor,
} from '@testing-library/react'
import { useEffect, type ReactNode, type ReactElement } from 'react'
import { MeetingLifecycleProvider } from '../contexts/MeetingLifecycleProvider'
import { useMeetingLifecycle } from '../contexts/MeetingLifecycleContext'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Conference } from './Conference'
import { ApiAccessLevel, type ApiRoom } from '../api/ApiRoom'
import { activateRecording } from '../api/recordingConsent'
import { userChoicesStore } from '@/stores/userChoices'
import { ApiError } from '@/api/ApiError'

const createRoom = vi.fn()
const fetchRoomLifecycle = vi.fn()
const fetchRoom = vi.fn()
const navigateTo = vi.fn()
let liveKitOnDisconnected: ((reason: number) => void) | undefined
let liveKitOnConnected: (() => Promise<void>) | undefined
let liveKitAudio: unknown
let liveKitVideo: unknown
let createdRoomOptions: unknown
const roomInstances: unknown[] = []
let localTrackPublished: (() => void) | undefined
const refetchRoom = vi.fn().mockResolvedValue(undefined)

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
    useEffect(() => {
      void queryFn().catch(() => undefined)
      // The real query is not rerun on every provider render.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
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
    constructor(options?: unknown) {
      createdRoomOptions = options
      roomInstances.push(this)
    }
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
  VideoPresets: { h360: { resolution: { width: 640, height: 360 } } },
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
    audioDeviceId: 'microphone-initial',
    videoDeviceId: 'camera-initial',
    audioOutputDeviceId: 'speaker-initial',
    videoPublishResolution: undefined,
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

const InitialPhase = () => {
  const { beginEnding, markEndingUncertain } = useMeetingLifecycle()
  useEffect(() => {
    if (lifecyclePhase === 'requesting') beginEnding()
    if (lifecyclePhase === 'uncertain') markEndingUncertain()
  }, [beginEnding, markEndingUncertain])
  return null
}

const render = (ui: ReactElement<{ roomId: string }>) => {
  if (lifecycleCloseRequestId) {
    window.sessionStorage.setItem(
      `mastrao-meeting-close-v1:${ui.props.roomId}`,
      lifecycleCloseRequestId
    )
  }
  const wrap = (element: ReactElement<{ roomId: string }>) => (
    <MeetingLifecycleProvider
      key={element.props.roomId}
      roomId={element.props.roomId}
    >
      <InitialPhase />
      {element}
    </MeetingLifecycleProvider>
  )
  const result = renderView(wrap(ui))
  return {
    ...result,
    rerender: (element: ReactElement<{ roomId: string }>) =>
      result.rerender(wrap(element)),
  }
}

vi.mock('../api/endMeeting', () => ({
  endMeeting: () => new Promise(() => undefined),
  isRetryableEndMeetingError: () => true,
}))

describe('Conference room lookup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    fetchRoomLifecycle.mockReset().mockReturnValue(new Promise(() => undefined))
    liveKitOnDisconnected = undefined
    liveKitOnConnected = undefined
    liveKitAudio = undefined
    liveKitVideo = undefined
    createdRoomOptions = undefined
    roomInstances.length = 0
    userChoicesStore.audioDeviceId = 'microphone-initial'
    userChoicesStore.videoDeviceId = 'camera-initial'
    userChoicesStore.audioOutputDeviceId = 'speaker-initial'
    userChoicesStore.videoPublishResolution = undefined
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
    expect(createdRoomOptions).toEqual(
      expect.objectContaining({
        publishDefaults: { videoCodec: 'h264' },
      })
    )
  })

  it('preserves the joined Room when device and resolution preferences change', () => {
    fetchRoom.mockResolvedValue({})
    const { rerender } = render(<Conference roomId="abc-defg-hij" />)
    const joinedRoom = roomInstances[0]

    userChoicesStore.audioDeviceId = 'microphone-next'
    userChoicesStore.videoDeviceId = 'camera-next'
    userChoicesStore.audioOutputDeviceId = 'speaker-next'
    userChoicesStore.videoPublishResolution = 'h360'
    rerender(<Conference roomId="abc-defg-hij" />)

    expect(roomInstances).toEqual([joinedRoom])
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

  it('keeps leaving distinct from ending and does not read lifecycle on voluntary exit', async () => {
    fetchRoom.mockResolvedValue({})
    render(<Conference roomId="room_0123456789abcdef0123456789abcdef" />)
    await act(async () => liveKitOnDisconnected?.(1))
    expect(navigateTo).toHaveBeenCalledWith(
      'feedback',
      { outcome: 'left', roomId: 'room_0123456789abcdef0123456789abcdef' },
      expect.any(Object)
    )
    expect(fetchRoomLifecycle).not.toHaveBeenCalled()
    expect(createRoom).not.toHaveBeenCalled()
  })

  it('observes canonical room deletion once and follows its terminal state', async () => {
    fetchRoom.mockResolvedValue({})
    fetchRoomLifecycle.mockResolvedValueOnce({ state: 'ended' })
    render(<Conference roomId="room_0123456789abcdef0123456789abcdef" />)
    await act(async () => liveKitOnDisconnected?.(4))
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
    expect(navigateTo).toHaveBeenCalledWith(
      'feedback',
      { outcome: 'ended', roomId: 'room_0123456789abcdef0123456789abcdef' },
      expect.any(Object)
    )
    expect(createRoom).not.toHaveBeenCalled()
  })

  it('keeps a pending close intent when canonical lifecycle is still open', async () => {
    lifecyclePhase = 'uncertain'
    lifecycleCloseRequestId = 'close_existing'
    fetchRoom.mockResolvedValue({})
    fetchRoomLifecycle.mockResolvedValueOnce({ state: 'open' })

    render(<Conference roomId="room_0123456789abcdef0123456789abcdef" />)

    await waitFor(() => expect(fetchRoomLifecycle).toHaveBeenCalled())
    expect(
      window.sessionStorage.getItem(
        'mastrao-meeting-close-v1:room_0123456789abcdef0123456789abcdef'
      )
    ).toBe('close_existing')
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('does not misreport a masked lifecycle 404 as an ended meeting', async () => {
    vi.useFakeTimers()
    lifecyclePhase = 'uncertain'
    lifecycleCloseRequestId = 'close_existing'
    fetchRoom.mockResolvedValue({})
    fetchRoomLifecycle
      .mockRejectedValueOnce(new ApiError(404, { message: 'not found' }))
      .mockResolvedValueOnce({ state: 'open' })

    render(<Conference roomId="room_0123456789abcdef0123456789abcdef" />)

    await act(async () => undefined)
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
    expect(navigateTo).not.toHaveBeenCalled()

    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(fetchRoomLifecycle).toHaveBeenCalledTimes(2)
    expect(
      window.sessionStorage.getItem(
        'mastrao-meeting-close-v1:room_0123456789abcdef0123456789abcdef'
      )
    ).toBe('close_existing')
    expect(navigateTo).not.toHaveBeenCalled()
    vi.useRealTimers()
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})
