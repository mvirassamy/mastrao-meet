import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeetingPersonalNotesSection } from '@/features/meetingHistory/components/MeetingPersonalNotesSection'
import de from '@/locales/de/personalNotes.json'
import en from '@/locales/en/personalNotes.json'
import fr from '@/locales/fr/personalNotes.json'
import nl from '@/locales/nl/personalNotes.json'
import { NOTE_REQUEST_TIMEOUT } from '../api/personalNotesApi'

const auth = vi.hoisted(() => ({
  user: { id: 'authenticated-account' } as { id: string } | undefined,
}))

vi.mock('@/features/auth/api/useUser', () => ({
  useUser: () => ({ user: auth.user }),
}))

const endpoint = '/meetings/history/meeting/personal-note/'
const saved = {
  id: 'note',
  content: 'Saved fixture',
  revision: 1,
  created_at: null,
  updated_at: null,
}
const i18n = createInstance()

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mountNotes() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MeetingPersonalNotesSection endpoint={endpoint} inTab />
    </I18nextProvider>
  )
}

async function advance(milliseconds = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds)
  })
}

beforeEach(async () => {
  auth.user = { id: 'authenticated-account' }
  vi.useFakeTimers()
  await i18n.init({
    lng: 'fr',
    fallbackLng: false,
    resources: { fr: { personalNotes: fr } },
    interpolation: { escapeValue: false },
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('history notes load feedback through the real API and controller', () => {
  it('reports a 404 initial read without claiming an unsaved draft or allowing writes', async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () =>
        response({ detail: 'Private response fixture' }, 404)
      )
    vi.stubGlobal('fetch', fetch)
    mountNotes()
    await advance()

    const editor = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(editor.disabled).toBe(true)
    expect(editor.value).toBe('')
    expect(fetch).toHaveBeenCalledOnce()
    expect(fetch.mock.calls[0][0]).toContain(`/api/v1.0${endpoint}`)
    expect(fetch.mock.calls[0][1]?.method).toBeUndefined()
    expect(screen.getByRole('alert').textContent).toContain(
      'Impossible de charger les notes.'
    )
    expect(screen.getByRole('alert').textContent).not.toContain('brouillon')
    expect(screen.getByRole('status').textContent).toBe('Notes non chargées')
    expect(screen.queryByText(/Private response fixture/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Réessayer' }))
    await advance(6_000)
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls.every(([, options]) => !options?.method)).toBe(true)
    expect(editor.disabled).toBe(true)
  })

  it.each([401, 403, 409, 429, 500])(
    'reports an initial HTTP %i read failure and retries the GET before enabling editing',
    async (status) => {
      const fetch = vi
        .fn<typeof globalThis.fetch>()
        .mockResolvedValueOnce(response({ detail: 'Private fixture' }, status))
        .mockResolvedValueOnce(response(saved))
      vi.stubGlobal('fetch', fetch)
      mountNotes()
      await advance()

      const editor = screen.getByRole('textbox') as HTMLTextAreaElement
      expect(editor.disabled).toBe(true)
      const alert = screen.getByRole('alert').textContent
      const accessDenied = status === 401 || status === 403
      const expected = accessDenied ? fr.accessError : fr.loadError
      expect(alert).toContain(expected)
      expect(alert).not.toContain('brouillon')
      expect(alert).not.toContain('expiré')
      expect(screen.getByRole('status').textContent).toBe(fr.loadFailed)
      expect(fetch).toHaveBeenCalledOnce()

      if (accessDenied) {
        fireEvent(window, new Event('online'))
        await advance(6_000)
        expect(fetch).toHaveBeenCalledOnce()
      }
      fireEvent.click(screen.getByRole('button', { name: fr.retry }))
      await advance()
      expect(fetch).toHaveBeenCalledTimes(2)
      expect(fetch.mock.calls.every(([, options]) => !options?.method)).toBe(
        true
      )
      expect(editor.disabled).toBe(false)
      expect(editor.value).toBe(saved.content)
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.getByRole('status').textContent).toBe(fr.saved)
    }
  )

  it.each([
    ['network failure', () => Promise.reject(new TypeError('Offline fixture'))],
    ['malformed response', async () => response({})],
  ])(
    'reports a %s as a load failure and recovers on reconnect',
    async (_label, request) => {
      const fetch = vi
        .fn<typeof globalThis.fetch>()
        .mockImplementationOnce(request)
        .mockResolvedValueOnce(response(saved))
      vi.stubGlobal('fetch', fetch)
      mountNotes()
      await advance()
      expect(screen.getByRole('alert').textContent).toContain(fr.loadError)
      expect(screen.getByRole('alert').textContent).not.toContain('brouillon')
      expect(
        (screen.getByRole('textbox') as HTMLTextAreaElement).disabled
      ).toBe(true)

      fireEvent(window, new Event('online'))
      await advance()
      expect(fetch).toHaveBeenCalledTimes(2)
      expect(fetch.mock.calls.every(([, options]) => !options?.method)).toBe(
        true
      )
      expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
        saved.content
      )
      expect(screen.queryByRole('alert')).toBeNull()
    }
  )

  it('reports a read timeout without claiming a draft and allows a manual retry', async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(
        (_url, options) =>
          new Promise((_resolve, reject) => {
            options?.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError'))
            )
          })
      )
      .mockResolvedValueOnce(response(saved))
    vi.stubGlobal('fetch', fetch)
    mountNotes()
    await advance(NOTE_REQUEST_TIMEOUT)
    expect(screen.getByRole('alert').textContent).toContain(fr.loadError)
    expect(screen.getByRole('alert').textContent).not.toContain('brouillon')
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(
      true
    )

    fireEvent.click(screen.getByRole('button', { name: fr.retry }))
    await advance()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls.every(([, options]) => !options?.method)).toBe(true)
    expect(screen.getByRole('status').textContent).toBe(fr.saved)
  })

  it.each([401, 403, 404, 429, 500])(
    'retains an editable draft after a failed HTTP %i write, including a failed recovery GET',
    async (status) => {
      const fetch = vi
        .fn<typeof globalThis.fetch>()
        .mockResolvedValueOnce(response(saved))
        .mockResolvedValueOnce(response({}, status))
        .mockResolvedValueOnce(response({}, status))
        .mockResolvedValueOnce(response(saved))
        .mockResolvedValueOnce(
          response({ ...saved, content: 'Latest draft fixture', revision: 2 })
        )
      vi.stubGlobal('fetch', fetch)
      mountNotes()
      await advance()
      const editor = screen.getByRole('textbox') as HTMLTextAreaElement
      fireEvent.change(editor, { target: { value: 'Draft fixture' } })
      await advance(800)

      const accessDenied = status === 401 || status === 403
      const expected = accessDenied ? fr.accessErrorWithDraft : fr.saveError
      expect(screen.getByRole('alert').textContent).toContain(expected)
      expect(screen.getByRole('status').textContent).toBe(fr.notSaved)
      expect(editor.disabled).toBe(false)
      expect(editor.value).toBe('Draft fixture')
      fireEvent.change(editor, { target: { value: 'Latest draft fixture' } })
      await advance(6_000)
      expect(fetch).toHaveBeenCalledTimes(2)

      fireEvent.click(screen.getByRole('button', { name: fr.retry }))
      await advance()
      expect(fetch).toHaveBeenCalledTimes(3)
      expect(fetch.mock.calls[2][1]?.method).toBeUndefined()
      expect(screen.getByRole('alert').textContent).toContain(expected)
      expect(editor.disabled).toBe(false)
      expect(editor.value).toBe('Latest draft fixture')
      const unload = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(unload)
      expect(unload.defaultPrevented).toBe(true)

      fireEvent.click(screen.getByRole('button', { name: fr.retry }))
      await advance()
      expect(fetch).toHaveBeenCalledTimes(5)
      expect(fetch.mock.calls[3][1]?.method).toBeUndefined()
      expect(fetch.mock.calls[4][1]).toMatchObject({
        method: 'PUT',
        body: JSON.stringify({
          content: 'Latest draft fixture',
          expected_revision: 1,
        }),
      })
      expect(editor.value).toBe('Latest draft fixture')
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.getByRole('status').textContent).toBe(fr.saved)
    }
  )

  it('keeps successful save and reload behavior unchanged', async () => {
    const updated = {
      ...saved,
      content: 'Saved after edit fixture',
      revision: 2,
    }
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(response(saved))
      .mockResolvedValueOnce(response(updated))
      .mockResolvedValueOnce(response(updated))
    vi.stubGlobal('fetch', fetch)
    const first = mountNotes()
    await advance()
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: updated.content },
    })
    await advance(800)
    expect(screen.getByRole('status').textContent).toBe(fr.saved)
    first.unmount()
    mountNotes()
    await advance()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      updated.content
    )
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(
      false
    )
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(
      fetch.mock.calls.map(([, options]) => options?.method ?? 'GET')
    ).toEqual(['GET', 'PUT', 'GET'])
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('status').textContent).toBe(fr.saved)
    expect(screen.queryByText(fr.guestNotice)).toBeNull()
  })

  it.each([
    ['fr', fr],
    ['en', en],
    ['de', de],
    ['nl', nl],
  ] as const)(
    'explains temporary guest notes in %s without a sign-in action or blocking editing',
    async (language, translations) => {
      auth.user = undefined
      i18n.addResourceBundle(language, 'personalNotes', translations)
      await i18n.changeLanguage(language)
      const updated = { ...saved, content: 'Guest draft', revision: 2 }
      const fetch = vi
        .fn<typeof globalThis.fetch>()
        .mockResolvedValueOnce(response(saved))
        .mockResolvedValueOnce(response(updated))
      vi.stubGlobal('fetch', fetch)
      mountNotes()
      await advance()

      const editor = screen.getByRole('textbox') as HTMLTextAreaElement
      const notice = screen.getByText(translations.guestNotice)
      expect(editor.disabled).toBe(false)
      expect(editor.getAttribute('aria-describedby')?.split(' ')).toContain(
        notice.id
      )
      expect(screen.getByRole('status').textContent).toBe(
        translations.temporary
      )
      expect(screen.queryByRole('link')).toBeNull()
      expect(screen.queryByRole('button')).toBeNull()

      fireEvent.change(editor, { target: { value: updated.content } })
      await advance(800)
      expect(editor.value).toBe(updated.content)
      expect(fetch.mock.calls[1][1]).toMatchObject({ method: 'PUT' })
      expect(screen.getByRole('status').textContent).toBe(
        translations.temporary
      )
      expect(screen.queryByRole('alert')).toBeNull()
    }
  )

  it('keeps guest save failures visible alongside the temporary-notes notice', async () => {
    auth.user = undefined
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof globalThis.fetch>()
        .mockResolvedValueOnce(response(saved))
        .mockResolvedValueOnce(response({}, 500))
    )
    mountNotes()
    await advance()
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Unsaved guest draft' },
    })
    await advance(800)

    expect(screen.getByRole('status').textContent).toBe(fr.notSaved)
    expect(screen.getByRole('alert').textContent).toContain(fr.saveError)
    expect(screen.getByText(fr.guestNotice)).toBeTruthy()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      'Unsaved guest draft'
    )
  })

  it.each([
    ['fr', fr],
    ['en', en],
    ['de', de],
    ['nl', nl],
  ] as const)(
    'renders the translated load error for %s without falling back to French',
    async (language, translations) => {
      i18n.addResourceBundle(language, 'personalNotes', translations)
      await i18n.changeLanguage(language)
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({}, 404)))
      mountNotes()
      await advance()
      expect(screen.getByRole('alert').textContent).toContain(
        translations.loadError
      )
      expect(screen.getByRole('status').textContent).toBe(
        translations.loadFailed
      )
    }
  )
})
