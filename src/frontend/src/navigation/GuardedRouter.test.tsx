import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Link, Route, Switch } from 'wouter'
import { fetchApi } from '@/api/fetchApi'
import { PersonalNotesEditor } from '@/features/meeting-notes'
import type { PersonalNote } from '@/features/meeting-notes/api/personalNotesApi'
import { MeetingNotesScope } from '@/features/rooms/components/MeetingNotes'
import { GuardedRouter } from './GuardedRouter'
import { navigateTo } from './navigateTo'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
// Only the route catalog is a fixture; navigation uses the real standalone helper.
vi.mock('@/routes', () => ({
  routes: {
    home: { path: '/' },
    meetingHistory: { path: '/reunions/historique' },
  },
}))

const request = vi.mocked(fetchApi)
const meetingPath = '/meeting-room'
const historyPath = '/reunions/historique'
const endpoint = '/rooms/meeting-room/personal-note/'
const emptyNote: PersonalNote = {
  id: null,
  content: '',
  revision: 0,
  created_at: null,
  updated_at: null,
}
const draft = 'Private draft survives external navigation'

function savedNote(): PersonalNote {
  return { ...emptyNote, id: 'note', content: draft, revision: 1 }
}

function deferredSave() {
  let resolve!: (note: PersonalNote) => void
  let reject!: (error: Error) => void
  const promise = new Promise<PersonalNote>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function MeetingPage({ onUnmount }: { onUnmount: () => void }) {
  useEffect(() => onUnmount, [onUnmount])
  return (
    <MeetingNotesScope endpoint={endpoint} identityKey="host-a">
      <Link to={historyPath}>Meeting list link</Link>
      <PersonalNotesEditor />
    </MeetingNotesScope>
  )
}

function HistoryPage({ onMount }: { onMount: () => void }) {
  useEffect(onMount, [onMount])
  return <h1>Meeting history destination</h1>
}

function renderRoutes() {
  const onMeetingUnmount = vi.fn()
  const onHistoryMount = vi.fn()
  const view = render(
    <GuardedRouter>
      <nav aria-label="Workspace toolbar">
        <button onClick={() => navigateTo('meetingHistory')}>
          Toolbar history
        </button>
      </nav>
      <Switch>
        <Route path={meetingPath}>
          <MeetingPage onUnmount={onMeetingUnmount} />
        </Route>
        <Route path={historyPath}>
          <HistoryPage onMount={onHistoryMount} />
        </Route>
        <Route path="/">
          <h1>Home destination</h1>
        </Route>
      </Switch>
    </GuardedRouter>
  )
  return { ...view, onMeetingUnmount, onHistoryMount }
}

async function editDraft() {
  const editor = await screen.findByRole('textbox', { name: 'title' })
  await waitFor(() =>
    expect((editor as HTMLTextAreaElement).disabled).toBe(false)
  )
  fireEvent.change(editor, { target: { value: draft } })
  return editor as HTMLTextAreaElement
}

beforeEach(() => {
  window.history.replaceState(null, '', meetingPath)
  request.mockReset().mockResolvedValue(emptyNote)
})

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

describe('root navigation guard with browser history and real meeting notes', () => {
  it('retains the draft after standalone toolbar navigation fails to save, then retries before changing views', async () => {
    const firstSave = deferredSave()
    const retrySave = deferredSave()
    request
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(firstSave.promise)
      .mockResolvedValueOnce(emptyNote)
      .mockReturnValueOnce(retrySave.promise)
    const view = renderRoutes()
    const editor = await editDraft()

    fireEvent.click(screen.getByRole('button', { name: 'Toolbar history' }))

    await screen.findByRole('alertdialog', { name: 'saving' })
    expect(editor.isConnected).toBe(true)
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()
    expect(view.onHistoryMount).not.toHaveBeenCalled()
    expect(request.mock.calls[1][1]?.signal?.aborted).toBe(false)

    await act(async () => firstSave.reject(new Error('offline')))
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(editor.isConnected).toBe(true)
    expect(
      (
        within(recovery).getByRole('textbox', {
          name: 'title',
        }) as HTMLTextAreaElement
      ).value
    ).toBe(draft)
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.retry' })
    )
    await waitFor(() => expect(request).toHaveBeenCalledTimes(4))
    expect(request.mock.calls[2][1]?.method).toBeUndefined()
    expect(request.mock.calls[3][1]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify({ content: draft, expected_revision: 0 }),
    })
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()
    expect(view.onHistoryMount).not.toHaveBeenCalled()

    await act(async () => retrySave.resolve(savedNote()))

    await screen.findByRole('heading', { name: 'Meeting history destination' })
    expect(window.location.pathname).toBe(historyPath)
    expect(view.onMeetingUnmount).toHaveBeenCalledOnce()
    expect(view.onHistoryMount).toHaveBeenCalledOnce()
  })

  it('keeps a failed draft mounted after standalone replace navigation until explicit discard', async () => {
    request
      .mockResolvedValueOnce(emptyNote)
      .mockRejectedValueOnce(new Error('offline'))
    const view = renderRoutes()
    const editor = await editDraft()

    act(() => navigateTo('meetingHistory', undefined, { replace: true }))

    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(editor.isConnected).toBe(true)
    expect(editor.value).toBe(draft)
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()
    expect(view.onHistoryMount).not.toHaveBeenCalled()

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.discard' })
    )

    await screen.findByRole('heading', { name: 'Meeting history destination' })
    expect(view.onMeetingUnmount).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('retains the outgoing provider through browser Back and save failure until explicit discard', async () => {
    window.history.replaceState({ page: 'history' }, '', historyPath)
    window.history.pushState({ page: 'meeting' }, '', meetingPath)
    const save = deferredSave()
    request.mockResolvedValueOnce(emptyNote).mockReturnValueOnce(save.promise)
    const view = renderRoutes()
    const editor = await editDraft()

    act(() => window.history.back())

    await screen.findByRole('alertdialog', { name: 'saving' })
    expect(editor.isConnected).toBe(true)
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()
    expect(view.onHistoryMount).not.toHaveBeenCalled()

    await act(async () => save.reject(new Error('offline')))
    const recovery = await screen.findByRole('alertdialog', {
      name: 'exit.title',
    })
    expect(editor.isConnected).toBe(true)
    expect(
      (
        within(recovery).getByRole('textbox', {
          name: 'title',
        }) as HTMLTextAreaElement
      ).value
    ).toBe(draft)
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()

    fireEvent.click(
      within(recovery).getByRole('button', { name: 'exit.discard' })
    )

    await screen.findByRole('heading', { name: 'Meeting history destination' })
    expect(window.location.pathname).toBe(historyPath)
    expect(view.onMeetingUnmount).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('does not unmount on browser Back before the pending save is confirmed', async () => {
    window.history.replaceState(null, '', historyPath)
    window.history.pushState(null, '', meetingPath)
    const save = deferredSave()
    request.mockResolvedValueOnce(emptyNote).mockReturnValueOnce(save.promise)
    const view = renderRoutes()
    const editor = await editDraft()

    act(() => window.history.back())

    await screen.findByRole('alertdialog', { name: 'saving' })
    expect(editor.isConnected).toBe(true)
    expect(view.onHistoryMount).not.toHaveBeenCalled()
    await act(async () => save.resolve(savedNote()))

    await screen.findByRole('heading', { name: 'Meeting history destination' })
    expect(view.onMeetingUnmount).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('uses the latest navigation target without mounting an intermediate page while saving', async () => {
    const save = deferredSave()
    request.mockResolvedValueOnce(emptyNote).mockReturnValueOnce(save.promise)
    const view = renderRoutes()
    const editor = await editDraft()

    act(() => navigateTo('meetingHistory'))
    await screen.findByRole('alertdialog', { name: 'saving' })
    act(() => navigateTo('home'))
    expect(editor.isConnected).toBe(true)
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()
    expect(view.onHistoryMount).not.toHaveBeenCalled()

    await act(async () => save.resolve(savedNote()))

    await screen.findByRole('heading', { name: 'Home destination' })
    expect(window.location.pathname).toBe('/')
    expect(view.onHistoryMount).not.toHaveBeenCalled()
    expect(view.onMeetingUnmount).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('also preserves normal Link deferral when the root guard is present', async () => {
    const save = deferredSave()
    request.mockResolvedValueOnce(emptyNote).mockReturnValueOnce(save.promise)
    const view = renderRoutes()
    await editDraft()

    fireEvent.click(screen.getByRole('link', { name: 'Meeting list link' }))

    await screen.findByRole('alertdialog', { name: 'saving' })
    expect(window.location.pathname).toBe(meetingPath)
    expect(view.onMeetingUnmount).not.toHaveBeenCalled()
    await act(async () => save.resolve(savedNote()))

    await screen.findByRole('heading', { name: 'Meeting history destination' })
    expect(view.onMeetingUnmount).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('lets a clean meeting navigate without writing notes', async () => {
    const view = renderRoutes()
    await screen.findByText('saved')

    fireEvent.click(screen.getByRole('button', { name: 'Toolbar history' }))

    await screen.findByRole('heading', { name: 'Meeting history destination' })
    expect(view.onMeetingUnmount).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledOnce()
  })
})
