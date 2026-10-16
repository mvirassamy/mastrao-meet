import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createPersonalNotesApi,
  NOTE_REQUEST_TIMEOUT,
  PersonalNoteRequestError,
} from './personalNotesApi'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const empty = {
  id: null,
  content: '',
  revision: 0,
  created_at: null,
  updated_at: null,
}
const existing = { ...empty, id: 'note', content: 'private', revision: 1 }
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('personal notes API', () => {
  it.each([
    '/rooms/room/personal-note/',
    '/meetings/history/meeting/personal-note/',
  ])(
    'reads and writes %s using the canonical revision contract',
    async (endpoint) => {
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(response(empty))
        .mockResolvedValueOnce(response(existing))
      vi.stubGlobal('fetch', fetch)
      const api = createPersonalNotesApi(endpoint)
      const signal = new AbortController().signal
      expect(await api.read(signal)).toEqual(empty)
      expect(await api.write('private', 0, signal)).toEqual(existing)
      expect(fetch.mock.calls[0][0]).toContain(`/api/v1.0${endpoint}`)
      expect(fetch.mock.calls[1][1]).toMatchObject({
        method: 'PUT',
        credentials: 'include',
        cache: 'no-store',
        body: JSON.stringify({ content: 'private', expected_revision: 0 }),
      })
    }
  )

  it('discards API error bodies containing private text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ content: 'secret body' }, 409))
    )
    const api = createPersonalNotesApi('/rooms/room/personal-note/')
    const error = await api
      .write('draft', 1, new AbortController().signal)
      .catch((error: unknown) => error)
    expect(error).toBeInstanceOf(PersonalNoteRequestError)
    expect(error).toMatchObject({ statusCode: 409 })
    expect(JSON.stringify(error)).not.toContain('secret')
    expect(String(error)).not.toContain('draft')
  })

  it.each([
    undefined,
    {},
    { ...empty, content: 'not actually empty' },
    { ...empty, revision: -1 },
    { ...empty, revision: 1.5 },
    { ...existing, content: 'x'.repeat(100_001) },
  ])('rejects malformed or oversized read data', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(body)))
    const api = createPersonalNotesApi('/rooms/room/personal-note/')
    await expect(api.read(new AbortController().signal)).rejects.toBeInstanceOf(
      PersonalNoteRequestError
    )
  })

  it('aborts a stalled server request at the timeout', async () => {
    vi.useFakeTimers()
    let requestSignal: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            requestSignal = options.signal as AbortSignal
            requestSignal.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError'))
            )
          })
      )
    )
    const api = createPersonalNotesApi('/rooms/room/personal-note/')
    const outcome = api
      .read(new AbortController().signal)
      .catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(NOTE_REQUEST_TIMEOUT)
    expect(requestSignal?.aborted).toBe(true)
    expect(await outcome).toBeInstanceOf(PersonalNoteRequestError)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('forwards scope cancellation and clears the request timeout', async () => {
    vi.useFakeTimers()
    let requestSignal: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            requestSignal = options.signal as AbortSignal
            requestSignal.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError'))
            )
          })
      )
    )
    const controller = new AbortController()
    const outcome = createPersonalNotesApi('/rooms/room/personal-note/')
      .read(controller.signal)
      .catch((error: unknown) => error)
    controller.abort()
    expect(await outcome).toBeInstanceOf(PersonalNoteRequestError)
    expect(requestSignal?.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })
})
