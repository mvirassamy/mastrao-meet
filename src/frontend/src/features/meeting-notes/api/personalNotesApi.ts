import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { PERSONAL_NOTE_LIMIT } from '../model/noteContent'

export interface PersonalNote {
  id: string | null
  content: string
  revision: number
  created_at: string | null
  updated_at: string | null
}

export interface PersonalNotesApi {
  read(signal: AbortSignal): Promise<PersonalNote>
  write(
    content: string,
    expectedRevision: number,
    signal: AbortSignal
  ): Promise<PersonalNote>
}

export const NOTE_REQUEST_TIMEOUT = 15_000

/** Deliberately excludes response bodies, which may contain private notes. */
export class PersonalNoteRequestError extends Error {
  constructor(readonly statusCode?: number) {
    super('Personal note request failed')
  }
}

function parseNote(value: unknown): PersonalNote {
  if (typeof value !== 'object' || value === null) {
    throw new PersonalNoteRequestError()
  }
  const note = value as Partial<PersonalNote>
  const nullableString = (field: unknown) =>
    field === null || typeof field === 'string'
  if (
    !nullableString(note.id) ||
    typeof note.content !== 'string' ||
    Array.from(note.content).length > PERSONAL_NOTE_LIMIT ||
    !Number.isSafeInteger(note.revision) ||
    note.revision! < 0 ||
    !nullableString(note.created_at) ||
    !nullableString(note.updated_at)
  ) {
    throw new PersonalNoteRequestError()
  }
  if (note.id === null && (note.content !== '' || note.revision !== 0)) {
    throw new PersonalNoteRequestError()
  }
  return note as PersonalNote
}

export function createPersonalNotesApi(endpoint: string): PersonalNotesApi {
  async function request(signal: AbortSignal, options?: RequestInit) {
    const controller = new AbortController()
    const abort = () => controller.abort()
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
    const timeout = setTimeout(abort, NOTE_REQUEST_TIMEOUT)
    try {
      return parseNote(
        await fetchApi<unknown>(endpoint, {
          ...options,
          signal: controller.signal,
          cache: 'no-store',
        })
      )
    } catch (error) {
      if (error instanceof ApiError) {
        throw new PersonalNoteRequestError(error.statusCode)
      }
      throw new PersonalNoteRequestError()
    } finally {
      clearTimeout(timeout)
      signal.removeEventListener('abort', abort)
    }
  }

  return {
    read: (signal) => request(signal),
    write: (content, expectedRevision, signal) =>
      request(signal, {
        method: 'PUT',
        body: JSON.stringify({ content, expected_revision: expectedRevision }),
      }),
  }
}
