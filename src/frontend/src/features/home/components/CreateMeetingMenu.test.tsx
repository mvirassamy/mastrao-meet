import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CreateMeetingMenu } from './CreateMeetingMenu'

vi.mock('@/primitives', () => ({
  Button: ({
    children,
    icon,
    isDisabled,
  }: {
    children: React.ReactNode
    icon?: React.ReactNode
    isDisabled?: boolean
  }) => (
    <button type="button" disabled={isDisabled}>
      {icon}
      {children}
    </button>
  ),
  Menu: ({
    children,
    density,
  }: {
    children: React.ReactNode
    density?: string
  }) => (
    <div data-testid="menu-shell" data-density={density}>
      {children}
    </div>
  ),
}))
vi.mock('react-aria-components', () => ({
  Menu: ({ children }: { children: React.ReactNode }) => (
    <div role="menu">{children}</div>
  ),
  MenuItem: ({
    children,
    isDisabled,
    onAction,
  }: {
    children: React.ReactNode
    isDisabled?: boolean
    onAction?: () => void
  }) => (
    <button
      type="button"
      role="menuitem"
      disabled={isDisabled}
      onClick={onAction}
    >
      {children}
    </button>
  ),
}))

const mutationState = vi.hoisted(() => ({ active: 0, pending: false }))
const createMeeting = vi.hoisted(() => vi.fn())
const navigateTo = vi.hoisted(() => vi.fn())
const reportError = vi.hoisted(() => vi.fn())
const keyCounter = vi.hoisted(() => ({ value: 0 }))

vi.mock('@tanstack/react-query', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('@tanstack/react-query')>()),
  useIsMutating: () => mutationState.active,
}))
vi.mock('@/features/home/api/createCanonicalMeeting', () => ({
  canonicalMeetingMutationKey: ['createCanonicalMeeting'],
  createIdempotencyKey: () => `meet_key_${++keyCounter.value}_0123456789`,
  useCreateCanonicalMeeting: () => ({
    mutateAsync: createMeeting,
    isPending: mutationState.pending,
  }),
}))
vi.mock('@/navigation/navigateTo', () => ({ navigateTo }))
vi.mock('@/features/analytics/telemetry', () => ({ reportError }))
vi.mock('@/features/home/components/LaterMeetingDialog', () => ({
  LaterMeetingDialog: ({ room }: { room: { slug: string } | null }) =>
    room ? <p data-testid="later-room">{room.slug}</p> : null,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => (key === 'createMeeting' ? 'Créer' : key),
  }),
}))

describe('CreateMeetingMenu', () => {
  afterEach(cleanup)

  beforeEach(() => {
    mutationState.active = 0
    mutationState.pending = false
    createMeeting.mockReset()
    navigateTo.mockReset()
    reportError.mockReset()
    keyCounter.value = 0
  })

  it('disables every entry point while any room creation is active', () => {
    mutationState.active = 1
    render(
      <>
        <CreateMeetingMenu />
        <CreateMeetingMenu />
      </>
    )

    for (const button of screen.getAllByRole('button', { name: 'Créer' })) {
      expect((button as HTMLButtonElement).disabled).toBe(true)
    }
  })

  it('opts the create menu into the compact application density', () => {
    render(<CreateMeetingMenu />)

    expect(screen.getByTestId('menu-shell').dataset.density).toBe('app')
  })

  it('shows an actionable error when room creation fails', async () => {
    createMeeting.mockRejectedValueOnce(new Error('network'))
    render(<CreateMeetingMenu />)

    fireEvent.click(
      await screen.findByRole('menuitem', {
        name: 'createMenu.instantOption',
      })
    )

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toBe('createMenu.error')
    )
    expect(reportError).toHaveBeenCalledTimes(1)
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('starts an instant meeting in the canonical room, already as host', async () => {
    createMeeting.mockResolvedValueOnce({
      meetingRef: 'meeting_01',
      roomRef: 'room_0123456789abcdef',
    })
    render(<CreateMeetingMenu />)

    fireEvent.click(
      screen.getByRole('menuitem', { name: 'createMenu.instantOption' })
    )

    await waitFor(() =>
      expect(navigateTo).toHaveBeenCalledWith('room', 'room_0123456789abcdef')
    )
    expect(createMeeting).toHaveBeenCalledWith('meet_key_1_0123456789')
  })

  it('prepares a meeting for later with its room link', async () => {
    createMeeting.mockResolvedValueOnce({
      meetingRef: 'meeting_02',
      roomRef: 'room_fedcba9876543210',
    })
    render(<CreateMeetingMenu />)

    fireEvent.click(
      screen.getByRole('menuitem', { name: 'createMenu.laterOption' })
    )

    expect((await screen.findByTestId('later-room')).textContent).toBe(
      'room_fedcba9876543210'
    )
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('uses a new idempotency key for each user action', async () => {
    createMeeting.mockResolvedValue({
      meetingRef: 'meeting_03',
      roomRef: 'room_0123456789abcdef',
    })
    render(<CreateMeetingMenu />)
    const instant = screen.getByRole('menuitem', {
      name: 'createMenu.instantOption',
    })

    fireEvent.click(instant)
    await waitFor(() => expect(createMeeting).toHaveBeenCalledTimes(1))
    fireEvent.click(instant)
    await waitFor(() => expect(createMeeting).toHaveBeenCalledTimes(2))

    expect(createMeeting.mock.calls.map(([key]) => key)).toEqual([
      'meet_key_1_0123456789',
      'meet_key_2_0123456789',
    ])
  })
})
