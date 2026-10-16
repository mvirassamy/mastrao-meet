import { StrictMode } from 'react'
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createPersonalNotesApi,
  PersonalNoteRequestError,
  type PersonalNote,
  type PersonalNotesApi,
} from '../api/personalNotesApi'
import { PersonalNotesProvider } from './PersonalNotesProvider'
import { PersonalNotes } from './PersonalNotes'
import { PERSONAL_NOTE_LIMIT } from '../model/noteContent'
import {
  usePersonalNotes,
  type PersonalNotesContextValue,
} from '../hooks/usePersonalNotes'

vi.mock('../api/personalNotesApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/personalNotesApi')>()),
  createPersonalNotesApi: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, options: { defaultValue: string; count?: number }) =>
      options.defaultValue.replace('{{count}}', String(options.count)),
  }),
}))

const endpoint = '/rooms/room/personal-note/'
const empty: PersonalNote = {
  id: null,
  content: '',
  revision: 0,
  created_at: null,
  updated_at: null,
}
const note = (content: string, revision = 1): PersonalNote => ({
  ...empty,
  id: 'note',
  content,
  revision,
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

let latest: PersonalNotesContextValue | null
function Probe() {
  latest = usePersonalNotes()
  return null
}

function Surface({
  visible = true,
  identityKey = 'owner',
  url = endpoint,
}: {
  visible?: boolean
  identityKey?: string
  url?: string
}) {
  return (
    <PersonalNotesProvider endpoint={url} identityKey={identityKey}>
      <Probe />
      {visible && <PersonalNotes endpoint={url} />}
    </PersonalNotesProvider>
  )
}

async function advance(milliseconds = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds)
  })
}

function mockApi(initial: PersonalNote = empty) {
  const read = vi.fn<PersonalNotesApi['read']>().mockResolvedValue(initial)
  const write = vi
    .fn<PersonalNotesApi['write']>()
    .mockImplementation(async (content, revision) =>
      note(content, revision + 1)
    )
  vi.mocked(createPersonalNotesApi).mockReturnValue({ read, write })
  return { read, write }
}

beforeEach(() => {
  vi.useFakeTimers()
  latest = null
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('personal notes provider and editor', () => {
  it('returns null outside its provider without requiring a query provider', () => {
    const { result } = renderHook(usePersonalNotes)
    expect(result.current).toBeNull()
  })

  it('keeps the draft and pending save when a tab unmounts the editor', async () => {
    const { read, write } = mockApi(note('saved'))
    const pending = deferred<PersonalNote>()
    write.mockReturnValueOnce(pending.promise)
    const { rerender } = render(<Surface />)
    await advance()
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'private draft' },
    })
    rerender(<Surface visible={false} />)
    await advance()
    expect(write).toHaveBeenCalledOnce()
    expect(latest?.hasUnsavedChanges).toBe(true)
    rerender(<Surface />)
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      'private draft'
    )
    pending.resolve(note('private draft', 2))
    await advance()
    expect(latest?.hasUnsavedChanges).toBe(false)
    expect(read).toHaveBeenCalledOnce()
  })

  it('only warns about unloading while changes are unconfirmed', async () => {
    mockApi()
    render(<Surface />)
    await advance()
    const clean = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(clean)
    expect(clean.defaultPrevented).toBe(false)
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'draft' },
    })
    const dirty = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirty)
    expect(dirty.defaultPrevented).toBe(true)
    await advance(800)
    const saved = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(saved)
    expect(saved.defaultPrevented).toBe(false)
  })

  it('does not save browser IME input until composition ends', async () => {
    const { write } = mockApi()
    render(<Surface />)
    await advance()
    const editor = screen.getByRole('textbox')
    fireEvent.compositionStart(editor)
    fireEvent.change(editor, { target: { value: 'に' } })
    await advance(6_000)
    expect(write).not.toHaveBeenCalled()
    fireEvent.compositionEnd(editor, {
      data: '日本語',
      target: { value: '日本語' },
    })
    await advance()
    expect(write.mock.calls[0][0]).toBe('日本語')
  })

  it('rejects IME overflow on commit and blur while preserving the previous draft', async () => {
    const { write } = mockApi(note('saved version'))
    render(<Surface />)
    await advance()
    const editor = screen.getByRole('textbox') as HTMLTextAreaElement
    const draft = '😀'.repeat(PERSONAL_NOTE_LIMIT - 1) + 'Z'
    fireEvent.change(editor, { target: { value: draft } })
    fireEvent.compositionStart(editor)
    fireEvent.change(editor, { target: { value: '日本語' + draft } })
    await advance(6_000)
    expect(write).not.toHaveBeenCalled()
    fireEvent.compositionEnd(editor, {
      target: { value: '日本語' + draft },
    })
    expect(editor.value).toBe(draft)
    expect(screen.getByRole('alert').textContent).toContain(
      'Votre texte précédent est conservé.'
    )
    fireEvent.blur(editor)
    await advance()
    expect(editor.value).toBe(draft)
    expect(write).toHaveBeenCalledExactlyOnceWith(
      draft,
      1,
      expect.any(AbortSignal)
    )
    expect(latest?.wasRejected).toBe(true)
  })

  it('disables editing after a read failure and retries without writing an empty note', async () => {
    const { read, write } = mockApi()
    read.mockRejectedValueOnce(new Error('private response details'))
    render(<Surface />)
    await advance()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(
      true
    )
    expect(screen.getByRole('status').textContent).not.toContain(
      'private response details'
    )
    read.mockResolvedValue(note('recovered'))
    fireEvent.click(screen.getByRole('button', { name: 'Réessayer' }))
    await advance()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      'recovered'
    )
    expect(write).not.toHaveBeenCalled()
  })

  it('requires confirmation before replacing a conflicting server version', async () => {
    const { read, write } = mockApi(note('base'))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<Surface />)
    await advance()
    read.mockResolvedValue(note('other tab', 2))
    write.mockRejectedValueOnce(new PersonalNoteRequestError(409))
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'my draft' },
    })
    await advance(800)
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Enregistrer mon brouillon à la place',
      })
    )
    await advance()
    expect(confirm).toHaveBeenCalledOnce()
    expect(write).toHaveBeenCalledOnce()
    confirm.mockReturnValue(true)
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Enregistrer mon brouillon à la place',
      })
    )
    await advance()
    expect(write.mock.calls[1].slice(0, 2)).toEqual(['my draft', 2])
  })

  it.each(['identity', 'endpoint'])(
    'clears the old draft and ignores stale responses on %s changes',
    async (change) => {
      const { read } = mockApi()
      const oldRead = deferred<PersonalNote>()
      read
        .mockReturnValueOnce(oldRead.promise)
        .mockResolvedValueOnce(note('new owner note'))
      const { rerender } = render(<Surface />)
      await advance()
      const oldSignal = read.mock.calls[0][0]
      if (change === 'identity') {
        rerender(<Surface identityKey="new-owner" />)
      } else {
        rerender(<Surface url="/meetings/history/other/personal-note/" />)
      }
      await advance()
      expect(oldSignal.aborted).toBe(true)
      expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
        'new owner note'
      )
      oldRead.resolve(note('old owner secret'))
      await advance()
      expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
        'new owner note'
      )
    }
  )

  it('ignores an old save acknowledgement after an author change', async () => {
    const { read, write } = mockApi(note('old saved'))
    const pending = deferred<PersonalNote>()
    write.mockReturnValueOnce(pending.promise)
    const { rerender } = render(<Surface />)
    await advance()
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'old private draft' },
    })
    await advance(800)
    read.mockResolvedValue(note('new saved', 4))
    rerender(<Surface identityKey="new-owner" />)
    await advance()
    pending.resolve(note('old private draft', 2))
    await advance()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      'new saved'
    )
    expect(latest?.hasUnsavedChanges).toBe(false)
    expect(write).toHaveBeenCalledOnce()
  })

  it('recovers on reconnect but does not automatically retry expired authorization', async () => {
    const { write } = mockApi()
    render(<Surface />)
    await advance()
    write.mockRejectedValueOnce(new Error('offline'))
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'draft' },
    })
    await advance(800)
    fireEvent(window, new Event('online'))
    await advance()
    expect(write).toHaveBeenCalledTimes(2)
    write.mockRejectedValueOnce(new PersonalNoteRequestError(403))
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'new draft' },
    })
    await advance(800)
    fireEvent(window, new Event('online'))
    await advance()
    expect(write).toHaveBeenCalledTimes(3)
    expect(latest?.status).toBe('unauthorized')
  })

  it('loads and saves under React StrictMode without duplicate writes', async () => {
    const { write } = mockApi()
    render(
      <StrictMode>
        <Surface />
      </StrictMode>
    )
    await advance()
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'draft' },
    })
    await advance(800)
    expect(write).toHaveBeenCalledOnce()
    expect(latest?.status).toBe('ready')
  })
})
