import {
  act,
  cleanup,
  fireEvent,
  render as renderView,
  screen,
} from '@testing-library/react'
import { useEffect, type ReactNode, type ReactElement } from 'react'
import { MeetingLifecycleProvider } from '../contexts/MeetingLifecycleProvider'
import { useMeetingLifecycle } from '../contexts/MeetingLifecycleContext'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApiRoom } from '../api/ApiRoom'
import { ApiLobbyStatus } from '../api/requestEntry'
import { ApiError } from '@/api/ApiError'
import { Lobby } from './Lobby'

const fetchRoomLifecycle = vi.fn()
const navigateTo = vi.fn()
const refetchRoom = vi.fn()
const startWaiting = vi.fn()

let lobbyStatus = ApiLobbyStatus.IDLE
let lifecyclePhase: 'active' | 'requesting' | 'ending' | 'uncertain' | 'ended' =
  'active'
let lifecycleCloseRequestId: string | undefined
let roomRecording: ApiRoom['recording']
let nativeCapture: ApiRoom['native_capture']
let roomQueryError: unknown

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: {
      livekit: { token: 'token', url: 'wss://livekit.test' },
      recording: roomRecording,
      native_capture: nativeCapture,
    },
    error: roomQueryError,
    isError: roomQueryError !== undefined,
    isPending: false,
    refetch: refetchRoom,
  }),
}))

vi.mock('valtio', () => ({ useSnapshot: (value: unknown) => value }))
vi.mock('@/api/queryClient', () => ({
  queryClient: { setQueryData: vi.fn() },
}))
vi.mock('@/api/useConfig', () => ({
  useConfig: () => ({ data: {} }),
}))
vi.mock('@/features/auth/api/useUser', () => ({
  useUser: () => ({ isLoggedIn: true, user: { full_name: 'Host' } }),
}))
vi.mock('@/hooks/useLoginHint', () => ({
  useLoginHint: () => ({ openLoginHint: vi.fn() }),
}))
vi.mock('@/stores/user', () => ({
  saveUsername: vi.fn(),
  userStore: { username: 'Host' },
}))
vi.mock('../hooks/useLobby', () => ({
  useLobby: () => ({
    status: lobbyStatus,
    startWaiting,
  }),
}))
vi.mock('../api/fetchRoomLifecycle', () => ({
  fetchRoomLifecycle: (...args: unknown[]) => fetchRoomLifecycle(...args),
}))
vi.mock('@/navigation/navigateTo', () => ({
  navigateTo: (...args: unknown[]) => navigateTo(...args),
}))

vi.mock('@/styled-system/css', () => ({ css: () => '' }))
vi.mock('@/styled-system/jsx', () => ({
  VStack: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/primitives/H', () => ({
  H: ({ children }: { children: ReactNode }) => <h1>{children}</h1>,
}))
vi.mock('@/primitives/Spinner', () => ({
  Spinner: () => <div>spinner</div>,
}))
vi.mock('@/primitives/Field', () => ({
  Field: () => <input aria-label="usernameLabel" />,
}))
vi.mock('@/primitives', () => ({
  Form: ({
    children,
    onSubmit,
    submitLabel,
  }: {
    children: ReactNode
    onSubmit: () => void
    submitLabel: string
  }) => (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        void onSubmit()
      }}
    >
      {children}
      <button type="submit">{submitLabel}</button>
    </form>
  ),
  Text: ({ children }: { children: ReactNode }) => <p>{children}</p>,
}))
vi.mock('./NativeRecordingConsent', () => ({
  NativeRecordingConsent: () => <div>native audio consent</div>,
}))

const InitialPhase = () => {
  const { beginEnding, markEndingUncertain } = useMeetingLifecycle()
  useEffect(() => {
    if (lifecyclePhase === 'requesting') beginEnding()
    if (lifecyclePhase === 'uncertain') markEndingUncertain()
  }, [beginEnding, markEndingUncertain])
  return <button onClick={beginEnding}>close during admission</button>
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

describe('Lobby lifecycle reconciliation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    fetchRoomLifecycle.mockReset().mockReturnValue(new Promise(() => undefined))
    lobbyStatus = ApiLobbyStatus.IDLE
    lifecyclePhase = 'active'
    lifecycleCloseRequestId = undefined
    roomQueryError = undefined
    roomRecording = undefined
    nativeCapture = undefined
    refetchRoom.mockResolvedValue({
      data: {
        livekit: { token: 'token', url: 'wss://livekit.test' },
        recording: { mode: 'unrecorded' },
      },
    })
  })

  afterEach(cleanup)

  it('shows the sealed normal provider before Join without native capture or automatic consent', () => {
    roomRecording = {
      mode: 'recorded',
      recording_state: 'collecting',
      decision: 'absent',
      transcription_mode: 'transcribed',
      transcription_profile_ref: 'mistral-eu-standard-managed-demo-v1',
    }
    const enterRoom = vi.fn()
    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={enterRoom}
      />
    )
    expect(screen.getByText('managedProviderNotice')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'joinLabel' })).toBeTruthy()
    expect(enterRoom).not.toHaveBeenCalled()
    expect(refetchRoom).not.toHaveBeenCalled()
  })

  it('does not claim a normal provider before Join without a sealed profile', () => {
    roomRecording = { mode: 'recorded', recording_state: 'collecting' }
    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={vi.fn()}
      />
    )
    expect(screen.queryByText('managedProviderNotice')).toBeNull()
    expect(screen.getByRole('button', { name: 'joinLabel' })).toBeTruthy()
  })

  it.each(['collecting', 'stopping', 'processing'] as const)(
    'keeps joining available while video is %s and undecided',
    (recording_state) => {
      roomRecording = { mode: 'recorded', decision: 'absent', recording_state }
      render(
        <Lobby
          roomId="room_0123456789abcdef0123456789abcdef"
          enterRoom={vi.fn()}
        />
      )
      expect(screen.getByRole('button', { name: 'joinLabel' })).toBeTruthy()
    }
  )

  it('retains the independent native audio notice before joining', () => {
    roomRecording = {
      mode: 'recorded',
      decision: 'refused',
      recording_state: 'collecting',
    }
    nativeCapture = { decision: null } as ApiRoom['native_capture']
    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={vi.fn()}
      />
    )
    expect(screen.getByText('native audio consent')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'joinLabel' })).toBeNull()
  })

  it('keeps a restored close intent out of the join flow', () => {
    lifecyclePhase = 'uncertain'
    const enterRoom = vi.fn()

    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={enterRoom}
      />
    )

    expect(screen.getByRole('heading', { name: 'ending.title' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'joinLabel' })).toBeNull()
    expect(enterRoom).not.toHaveBeenCalled()
  })

  it('reconciles a canonical ended lobby to the shared terminal route', async () => {
    lobbyStatus = ApiLobbyStatus.ENDED
    fetchRoomLifecycle.mockResolvedValueOnce({ state: 'ended' })

    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={vi.fn()}
      />
    )

    await vi.waitFor(() =>
      expect(navigateTo).toHaveBeenCalledWith(
        'feedback',
        {
          outcome: 'ended',
          roomId: 'room_0123456789abcdef0123456789abcdef',
        },
        {
          replace: true,
          state: { room_id: 'room_0123456789abcdef0123456789abcdef' },
        }
      )
    )
  })

  it('resumes guest entry when a transient 404 still belongs to an open meeting', async () => {
    vi.useFakeTimers()
    lobbyStatus = ApiLobbyStatus.ENDED
    fetchRoomLifecycle.mockResolvedValueOnce({ state: 'open' })

    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={vi.fn()}
      />
    )

    await act(async () => undefined)
    // Paced: a lobby 404 that persists must not loop without delay.
    expect(startWaiting).not.toHaveBeenCalled()
    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(startWaiting).toHaveBeenCalledOnce()
    expect(navigateTo).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('does not enter when a submit races with a restored close intent', () => {
    lifecyclePhase = 'requesting'
    const enterRoom = vi.fn()

    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={enterRoom}
      />
    )

    const submit = screen.queryByRole('button', { name: 'joinLabel' })
    if (submit) fireEvent.click(submit)
    expect(refetchRoom).not.toHaveBeenCalled()
    expect(enterRoom).not.toHaveBeenCalled()
  })

  it('keeps a pending close intent when canonical lifecycle is still open', async () => {
    lifecyclePhase = 'uncertain'
    lifecycleCloseRequestId = 'close_existing'
    fetchRoomLifecycle.mockResolvedValueOnce({ state: 'open' })

    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={vi.fn()}
      />
    )

    await vi.waitFor(() => expect(fetchRoomLifecycle).toHaveBeenCalled())
    expect(
      window.sessionStorage.getItem(
        'mastrao-meeting-close-v1:room_0123456789abcdef0123456789abcdef'
      )
    ).toBe('close_existing')
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('does not misreport a masked lifecycle 404 as an ended meeting', async () => {
    vi.useFakeTimers()
    roomQueryError = new ApiError(404, { message: 'not found' })
    fetchRoomLifecycle
      .mockRejectedValueOnce(new ApiError(404, { message: 'not found' }))
      .mockResolvedValueOnce({ state: 'open' })

    render(
      <Lobby
        roomId="room_ffffffffffffffffffffffffffffffff"
        enterRoom={vi.fn()}
      />
    )

    await act(async () => undefined)
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
    expect(navigateTo).not.toHaveBeenCalled()

    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(fetchRoomLifecycle).toHaveBeenCalledTimes(2)
    expect(refetchRoom).not.toHaveBeenCalled()
    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(refetchRoom).toHaveBeenCalledOnce()
    expect(navigateTo).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('does not enter when a successful room response arrives after closing starts', async () => {
    let resolve!: (value: unknown) => void
    refetchRoom.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    const enterRoom = vi.fn()
    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={enterRoom}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: 'joinLabel' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'close during admission' })
    )
    await act(async () => resolve({ data: { livekit: { token: 'token' } } }))
    expect(enterRoom).not.toHaveBeenCalled()
    expect(startWaiting).not.toHaveBeenCalled()
  })

  it('shares one authority read when the room and lobby both report missing', async () => {
    lobbyStatus = ApiLobbyStatus.ENDED
    roomQueryError = new ApiError(404, { message: 'not found' })
    fetchRoomLifecycle.mockResolvedValueOnce({ state: 'ended' })
    render(
      <Lobby
        roomId="room_0123456789abcdef0123456789abcdef"
        enterRoom={vi.fn()}
      />
    )
    await act(async () => undefined)
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
    expect(navigateTo).toHaveBeenCalled()
  })

  it('retries lifecycle reconciliation after a transient server error', async () => {
    vi.useFakeTimers()
    roomQueryError = new ApiError(404, { message: 'not found' })
    fetchRoomLifecycle
      .mockRejectedValueOnce(new ApiError(503, { message: 'unavailable' }))
      .mockResolvedValueOnce({ state: 'ended' })

    render(
      <Lobby
        roomId="room_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        enterRoom={vi.fn()}
      />
    )

    await act(async () => undefined)
    expect(fetchRoomLifecycle).toHaveBeenCalledOnce()
    await act(async () => vi.advanceTimersByTimeAsync(1_000))
    expect(fetchRoomLifecycle).toHaveBeenCalledTimes(2)
    expect(navigateTo).toHaveBeenCalled()
    vi.useRealTimers()
  })
})
