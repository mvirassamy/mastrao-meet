import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiLobbyStatus } from '../api/requestEntry'
import { Lobby } from './Lobby'

const fetchRoomLifecycle = vi.fn()
const navigateTo = vi.fn()
const refetchRoom = vi.fn()
const startWaiting = vi.fn()
const markActive = vi.fn()
const markEnding = vi.fn()
const markEnded = vi.fn()

let lobbyStatus = ApiLobbyStatus.IDLE
let lifecyclePhase: 'active' | 'requesting' | 'ending' | 'uncertain' | 'ended' =
  'active'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: {
      livekit: { token: 'token', url: 'wss://livekit.test' },
      recording: { mode: 'unrecorded' },
    },
    error: undefined,
    isError: false,
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
vi.mock('../contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({
    phase: lifecyclePhase,
    isEnding: lifecyclePhase !== 'active',
    beginEnding: vi.fn(),
    markActive,
    markEnding,
    markEndingUncertain: vi.fn(),
    markEnded,
  }),
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
vi.mock('./RecordingConsent', () => ({
  RecordingConsent: () => <div>recording consent</div>,
}))

describe('Lobby lifecycle reconciliation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    lobbyStatus = ApiLobbyStatus.IDLE
    lifecyclePhase = 'active'
    refetchRoom.mockResolvedValue({
      data: {
        livekit: { token: 'token', url: 'wss://livekit.test' },
        recording: { mode: 'unrecorded' },
      },
    })
  })

  afterEach(cleanup)

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
})
