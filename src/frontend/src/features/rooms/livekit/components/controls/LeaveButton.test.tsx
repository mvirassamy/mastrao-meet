import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConnectionState } from 'livekit-client'

import { LeaveButton } from './LeaveButton'
import { fetchApi } from '@/api/fetchApi'
import { PersonalNotesEditor } from '@/features/meeting-notes'
import type { PersonalNote } from '@/features/meeting-notes/api/personalNotesApi'
import { MeetingNotes } from '@/features/rooms/components/MeetingNotes'

const disconnect = vi.fn()
const navigateTo = vi.fn()
const reportError = vi.fn()
const endMeeting = vi.fn()
const beginEnding = vi.fn()
const markEnding = vi.fn()
const markEndingUncertain = vi.fn()
const markEnded = vi.fn()
const markActive = vi.fn()
let connectionState = ConnectionState.Connected

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => ({ disconnect }),
  useConnectionState: () => connectionState,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/navigation/navigateTo', () => ({
  navigateTo: (...args: unknown[]) => navigateTo(...args),
}))
vi.mock('@/features/analytics/telemetry', () => ({
  reportError: (...args: unknown[]) => reportError(...args),
}))
vi.mock('@/features/rooms/api/endMeeting', () => ({
  endMeeting: (...args: unknown[]) => endMeeting(...args),
  isRetryableEndMeetingError: (error: { statusCode?: unknown }) =>
    typeof error.statusCode !== 'number' || error.statusCode >= 500,
}))
vi.mock('@/features/rooms/contexts/MeetingLifecycleContext', () => ({
  useMeetingLifecycle: () => ({
    phase: 'active',
    beginEnding,
    markActive,
    markEnding,
    markEndingUncertain,
    markEnded,
  }),
}))
vi.mock('@/primitives', () => ({
  Button: ({
    children,
    onPress,
    isDisabled,
    'aria-label': ariaLabel,
  }: {
    children: ReactNode
    onPress: () => void
    isDisabled?: boolean
    'aria-label'?: string
  }) => (
    <button aria-label={ariaLabel} disabled={isDisabled} onClick={onPress}>
      {children}
    </button>
  ),
  Dialog: ({
    children,
    isOpen,
    title,
    role,
  }: {
    children: ReactNode
    isOpen: boolean
    title: string
    role?: string
  }) =>
    isOpen ? (
      <section role={role} aria-label={title}>
        {children}
      </section>
    ) : null,
  P: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogActions: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}))

describe('LeaveButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchApi).mockReset()
    connectionState = ConnectionState.Connected
    disconnect.mockResolvedValue(undefined)
    beginEnding.mockReturnValue('close_0123456789abcdef')
  })
  afterEach(cleanup)

  it('lets a disconnected participant leave even when the SDK emits no event', async () => {
    connectionState = ConnectionState.Disconnected
    render(<LeaveButton roomId="abc-defg-hij" />)
    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    await waitFor(() =>
      expect(navigateTo).toHaveBeenCalledWith('feedback', {
        outcome: 'left',
        roomId: 'abc-defg-hij',
      })
    )
    expect(disconnect).toHaveBeenCalledWith(true)
  })

  it('keeps the existing SDK disconnection flow during an active call', () => {
    render(<LeaveButton roomId="abc-defg-hij" />)
    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    expect(disconnect).toHaveBeenCalledWith(true)
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('asks a host whether to leave alone or end the meeting', () => {
    render(<LeaveButton roomId="abc-defg-hij" canEnd />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.leave' })
    )

    expect(disconnect).toHaveBeenCalledWith(true)
    expect(endMeeting).not.toHaveBeenCalled()
  })

  it('raises the ending fence before closing the meeting for everyone', async () => {
    endMeeting.mockResolvedValueOnce({ state: 'ended' })
    const onEnded = vi.fn()
    render(<LeaveButton roomId="abc-defg-hij" canEnd onEnded={onEnded} />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )

    await waitFor(() => expect(markEnded).toHaveBeenCalledOnce())
    expect(beginEnding.mock.invocationCallOrder[0]).toBeLessThan(
      endMeeting.mock.invocationCallOrder[0]
    )
    expect(endMeeting.mock.calls[0][1]).toBe('close_0123456789abcdef')
    expect(onEnded).toHaveBeenCalledOnce()
    expect(disconnect).not.toHaveBeenCalled()
  })

  it('keeps an uncertain close available for retry', async () => {
    endMeeting.mockRejectedValueOnce(new Error('response lost'))
    endMeeting.mockResolvedValueOnce({ state: 'ending' })
    render(<LeaveButton roomId="abc-defg-hij" canEnd />)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )
    await screen.findByText('endMeeting.dialog.error')
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )

    await waitFor(() => expect(markEnding).toHaveBeenCalledOnce())
    expect(endMeeting.mock.calls[1][1]).toBe(endMeeting.mock.calls[0][1])
    expect(markEndingUncertain).toHaveBeenCalledOnce()
  })

  const emptyNote: PersonalNote = {
    id: null,
    content: '',
    revision: 0,
    created_at: null,
    updated_at: null,
  }

  const renderWithDraft = async (canEnd = false) => {
    render(
      <MeetingNotes roomId="abc-defg-hij" identityKey="host-a">
        <PersonalNotesEditor />
        <LeaveButton roomId="abc-defg-hij" canEnd={canEnd} />
      </MeetingNotes>
    )
    const editor = await screen.findByRole('textbox', { name: 'title' })
    await waitFor(() =>
      expect((editor as HTMLTextAreaElement).disabled).toBe(false)
    )
    fireEvent.change(editor, { target: { value: 'Unsaved private note' } })
  }

  it.each([false, true])(
    'saves before disconnecting when host choice is %s',
    async (canEnd) => {
      let resolveSave!: (note: PersonalNote) => void
      vi.mocked(fetchApi)
        .mockResolvedValueOnce(emptyNote)
        .mockReturnValueOnce(
          new Promise<PersonalNote>((resolve) => {
            resolveSave = resolve
          })
        )
      await renderWithDraft(canEnd)

      fireEvent.click(screen.getByRole('button', { name: 'leave' }))
      if (canEnd) {
        fireEvent.click(
          screen.getByRole('button', { name: 'endMeeting.dialog.leave' })
        )
      }
      await waitFor(() => expect(fetchApi).toHaveBeenCalledTimes(2))
      expect(disconnect).not.toHaveBeenCalled()
      expect(navigateTo).not.toHaveBeenCalled()
      expect(endMeeting).not.toHaveBeenCalled()

      await act(async () =>
        resolveSave({
          ...emptyNote,
          id: 'note',
          content: 'Unsaved private note',
          revision: 1,
        })
      )

      await waitFor(() => expect(disconnect).toHaveBeenCalledWith(true))
      expect(endMeeting).not.toHaveBeenCalled()
    }
  )

  it('persists notes before raising the ending fence and sending the close command', async () => {
    let resolveSave!: (note: PersonalNote) => void
    vi.mocked(fetchApi)
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(
        new Promise<PersonalNote>((resolve) => {
          resolveSave = resolve
        })
      )
    endMeeting.mockResolvedValueOnce({ state: 'ended' })
    await renderWithDraft(true)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )
    await waitFor(() => expect(fetchApi).toHaveBeenCalledTimes(2))
    expect(beginEnding).not.toHaveBeenCalled()
    expect(endMeeting).not.toHaveBeenCalled()
    expect(disconnect).not.toHaveBeenCalled()

    await act(async () =>
      resolveSave({
        ...emptyNote,
        id: 'note',
        content: 'Unsaved private note',
        revision: 1,
      })
    )

    await waitFor(() => expect(endMeeting).toHaveBeenCalledOnce())
    expect(beginEnding).toHaveBeenCalledOnce()
    expect(markEnded).toHaveBeenCalledOnce()
  })

  it('does not navigate an already disconnected participant away from a failed draft', async () => {
    connectionState = ConnectionState.Disconnected
    vi.mocked(fetchApi)
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('offline'))
    await renderWithDraft()

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))

    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(
      (
        within(recovery).getByRole('textbox', {
          name: 'title',
        }) as HTMLTextAreaElement
      ).value
    ).toBe('Unsaved private note')
    expect(disconnect).not.toHaveBeenCalled()
    expect(navigateTo).not.toHaveBeenCalled()

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.discard' })
    )

    expect(disconnect).toHaveBeenCalledWith(true)
    expect(navigateTo).toHaveBeenCalledWith('feedback', {
      outcome: 'left',
      roomId: 'abc-defg-hij',
    })
  })

  it('keeps a failed draft and does not close the meeting until explicit discard', async () => {
    vi.mocked(fetchApi)
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('offline'))
    endMeeting.mockResolvedValueOnce({ state: 'ended' })
    await renderWithDraft(true)

    fireEvent.click(screen.getByRole('button', { name: 'leave' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'endMeeting.dialog.confirm' })
    )
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(beginEnding).not.toHaveBeenCalled()
    expect(endMeeting).not.toHaveBeenCalled()

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.discard' })
    )

    await waitFor(() => expect(endMeeting).toHaveBeenCalledOnce())
    expect(markEnded).toHaveBeenCalledOnce()
  })
})
