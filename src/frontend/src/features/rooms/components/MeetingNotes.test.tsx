import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Link, Route, Router } from 'wouter'
import { memoryLocation } from 'wouter/memory-location'
import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { PersonalNotesEditor } from '@/features/meeting-notes'
import type { PersonalNote } from '@/features/meeting-notes/api/personalNotesApi'
import { useMeetingNotesExit } from '../contexts/MeetingNotesExitContext'
import { MeetingNotes, MeetingNotesScope } from './MeetingNotes'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const request = vi.mocked(fetchApi)
const emptyNote: PersonalNote = {
  id: null,
  content: '',
  revision: 0,
  created_at: null,
  updated_at: null,
}
const roomId = 'room_0123456789abcdef0123456789abcdef'
const endpoint = `/rooms/${roomId}/personal-note/`

function savedNote(content: string, revision = 1): PersonalNote {
  return { ...emptyNote, id: 'note', content, revision }
}

function deferredNote() {
  let resolve!: (note: PersonalNote) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<PersonalNote>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function ExitControls({ onExit }: { onExit: () => void }) {
  const exit = useMeetingNotesExit()!
  return (
    <button disabled={exit.isSaving} onClick={() => exit.requestExit(onExit)}>
      Exit meeting
    </button>
  )
}

function Meeting({
  identityKey = 'host-a',
  onExit,
}: {
  identityKey?: string
  onExit: () => void
}) {
  return (
    <MeetingNotes roomId={roomId} identityKey={identityKey}>
      <PersonalNotesEditor />
      <ExitControls onExit={onExit} />
    </MeetingNotes>
  )
}

async function editDraft(content = 'Private draft') {
  const editor = await screen.findByRole('textbox', { name: 'title' })
  await waitFor(() =>
    expect((editor as HTMLTextAreaElement).disabled).toBe(false)
  )
  fireEvent.change(editor, { target: { value: content } })
  return editor as HTMLTextAreaElement
}

function renderWithNavigation(noteEndpoint: string) {
  const location = memoryLocation({ path: '/current-meeting', record: true })
  render(
    <Router hook={location.hook}>
      <Route path="/current-meeting">
        <MeetingNotesScope endpoint={noteEndpoint} identityKey="host-a">
          <Link to="/meetings">Back to meetings</Link>
          <PersonalNotesEditor />
        </MeetingNotesScope>
      </Route>
      <Route path="/meetings">Meetings destination</Route>
    </Router>
  )
  return location
}

beforeEach(() => {
  request.mockReset().mockResolvedValue(emptyNote)
})

afterEach(() => {
  cleanup()
})

describe('meeting notes exit guard with the real notes provider', () => {
  it('defers SPA navigation and keeps the history draft until a failed save is retried', async () => {
    const firstSave = deferredNote()
    const retrySave = deferredNote()
    request
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(firstSave.promise)
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(retrySave.promise)
    const historyEndpoint = '/meetings/history/meeting-1/personal-note/'
    const location = renderWithNavigation(historyEndpoint)
    await editDraft()

    fireEvent.click(screen.getByRole('link', { name: 'Back to meetings' }))
    await screen.findByRole('alertdialog', { name: 'saving' })
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
    expect(location.history).toEqual(['/current-meeting'])
    expect(screen.queryByText('Meetings destination')).toBeNull()

    await act(async () => firstSave.reject(new Error('offline')))
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(
      (
        within(recovery).getByRole('textbox', {
          name: 'title',
        }) as HTMLTextAreaElement
      ).value
    ).toBe('Private draft')
    expect(location.history).toEqual(['/current-meeting'])

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.retry' })
    )
    await waitFor(() => expect(request).toHaveBeenCalledTimes(4))
    expect(request.mock.calls[2][0]).toBe(historyEndpoint)
    expect(request.mock.calls[2][1]?.method).toBeUndefined()
    expect(request.mock.calls[3]).toEqual([
      historyEndpoint,
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          content: 'Private draft',
          expected_revision: 0,
        }),
      }),
    ])
    expect(location.history).toEqual(['/current-meeting'])

    await act(async () => retrySave.resolve(savedNote('Private draft')))

    await screen.findByText('Meetings destination')
    expect(location.history).toEqual(['/current-meeting', '/meetings'])
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('allows a blocked SPA link only after explicit discard when saving fails', async () => {
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('offline'))
    const location = renderWithNavigation(endpoint)
    await editDraft()

    fireEvent.click(screen.getByRole('link', { name: 'Back to meetings' }))
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(location.history).toEqual(['/current-meeting'])
    expect(screen.queryByText('Meetings destination')).toBeNull()

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.discard' })
    )

    await screen.findByText('Meetings destination')
    expect(location.history).toEqual(['/current-meeting', '/meetings'])
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('leaves a clean meeting without writing a note', async () => {
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await screen.findByText('saved')

    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))

    expect(onExit).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledWith(
      endpoint,
      expect.objectContaining({ cache: 'no-store' })
    )
  })

  it('waits for every edit made during the save before exiting', async () => {
    const firstSave = deferredNote()
    const secondSave = deferredNote()
    request
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(firstSave.promise)
      .mockReturnValueOnce(secondSave.promise)
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await editDraft('First draft')
    const exitButton = screen.getByRole('button', {
      name: 'Exit meeting',
    }) as HTMLButtonElement

    fireEvent.click(exitButton)
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
    expect(onExit).not.toHaveBeenCalled()
    expect(exitButton.disabled).toBe(true)
    const savingDialog = await screen.findByRole('alertdialog', {
      name: 'saving',
    })
    expect(
      (
        within(savingDialog).getByRole('button', {
          name: 'exit.discard',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    fireEvent.change(
      within(savingDialog).getByRole('textbox', { name: 'title' }),
      { target: { value: 'Latest draft' } }
    )

    await act(async () => firstSave.resolve(savedNote('First draft')))
    await waitFor(() => expect(request).toHaveBeenCalledTimes(3))
    expect(onExit).not.toHaveBeenCalled()
    expect(request.mock.calls[2][1]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify({ content: 'Latest draft', expected_revision: 1 }),
    })

    await act(async () => secondSave.resolve(savedNote('Latest draft', 2)))
    await waitFor(() => expect(onExit).toHaveBeenCalledOnce())
  })

  it('keeps a failed draft available in the recovery dialog without exiting', async () => {
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('offline'))
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await editDraft()

    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))

    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(
      (
        within(recovery).getByRole('textbox', {
          name: 'title',
        }) as HTMLTextAreaElement
      ).value
    ).toBe('Private draft')
    expect(within(recovery).getByText('saveError')).toBeTruthy()
    expect(onExit).not.toHaveBeenCalled()
  })

  it('re-reads after a failed save and retries before completing the pending exit', async () => {
    const retrySave = deferredNote()
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(retrySave.promise)
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await editDraft()
    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.retry' })
    )

    await waitFor(() => expect(request).toHaveBeenCalledTimes(4))
    expect(request.mock.calls[2][1]?.method).toBeUndefined()
    expect(request.mock.calls[3][1]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify({ content: 'Private draft', expected_revision: 0 }),
    })
    expect(onExit).not.toHaveBeenCalled()
    await act(async () => retrySave.resolve(savedNote('Private draft')))
    await waitFor(() => expect(onExit).toHaveBeenCalledOnce())
  })

  it('confirms a lost save response by reading before exiting without a duplicate write', async () => {
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValueOnce(savedNote('Private draft'))
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await editDraft()
    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.retry' })
    )

    await waitFor(() => expect(onExit).toHaveBeenCalledOnce())
    expect(request).toHaveBeenCalledTimes(3)
    expect(
      request.mock.calls.filter(([, options]) => options?.method === 'PUT')
    ).toHaveLength(1)
  })

  it('exits after an explicit discard when saving has failed', async () => {
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('offline'))
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await editDraft()
    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(onExit).not.toHaveBeenCalled()

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.discard' })
    )

    expect(onExit).toHaveBeenCalledOnce()
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('keeps an expired-session draft until explicit discard', async () => {
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new ApiError(403, {}))
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await editDraft()
    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))

    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(within(recovery).getByText('sessionExpired')).toBeTruthy()
    expect(
      (
        within(recovery).getByRole('textbox', {
          name: 'title',
        }) as HTMLTextAreaElement
      ).value
    ).toBe('Private draft')
    expect(onExit).not.toHaveBeenCalled()
  })

  it('does not overwrite a concurrent saved version when exit saving conflicts', async () => {
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new ApiError(409, {}))
      .mockResolvedValueOnce(savedNote('Saved in another tab'))
    const onExit = vi.fn()
    render(<Meeting onExit={onExit} />)
    await editDraft()
    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))

    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(within(recovery).getByText('conflict')).toBeTruthy()
    expect(
      (
        within(recovery).getByRole('textbox', {
          name: 'title',
        }) as HTMLTextAreaElement
      ).value
    ).toBe('Private draft')
    expect(onExit).not.toHaveBeenCalled()
    expect(
      request.mock.calls.filter(([, options]) => options?.method === 'PUT')
    ).toHaveLength(1)
  })

  it.each(['success', 'failure'] as const)(
    'ignores a late save %s after unmount',
    async (outcome) => {
      const save = deferredNote()
      request.mockResolvedValueOnce(emptyNote).mockReturnValueOnce(save.promise)
      const onExit = vi.fn()
      const view = render(<Meeting onExit={onExit} />)
      await editDraft()
      fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))
      await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
      const signal = request.mock.calls[1][1]?.signal

      view.unmount()
      await act(async () => {
        if (outcome === 'success') save.resolve(savedNote('Private draft'))
        else save.reject(new Error('late failure'))
      })

      expect(signal?.aborted).toBe(true)
      expect(onExit).not.toHaveBeenCalled()
    }
  )

  it('cancels the old pending exit when the author changes before a late save response', async () => {
    const oldSave = deferredNote()
    request
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(oldSave.promise)
      .mockResolvedValueOnce(savedNote('New author notes'))
    const oldExit = vi.fn()
    const newExit = vi.fn()
    const view = render(<Meeting identityKey="host-a" onExit={oldExit} />)
    await editDraft()
    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2))

    view.rerender(<Meeting identityKey="host-b" onExit={newExit} />)
    await waitFor(() =>
      expect(
        (screen.getByRole('textbox', { name: 'title' }) as HTMLTextAreaElement)
          .value
      ).toBe('New author notes')
    )
    await act(async () => oldSave.resolve(savedNote('Private draft')))

    expect(oldExit).not.toHaveBeenCalled()
    expect(newExit).not.toHaveBeenCalled()
    expect(screen.queryByRole('alertdialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Exit meeting' }))
    expect(newExit).toHaveBeenCalledOnce()
    expect(oldExit).not.toHaveBeenCalled()
  })
})
