import { ApiError } from '@/api/ApiError'
import { fetchApi } from '@/api/fetchApi'
import { isNoteEnrichmentState } from './parseNoteEnrichment'
import type {
  NoteEnrichmentApi,
  NoteEnrichmentCommand,
} from './noteEnrichmentTypes'

export const ENRICHMENT_REQUEST_TIMEOUT = 120_000

/** No private response body, note or proposal is attached to this error. */
export class NoteEnrichmentRequestError extends Error {
  constructor(readonly statusCode?: number) {
    super('Note enrichment request failed')
  }
}

export function createNoteEnrichmentApi(
  baseNoteEndpoint: string,
  meetingRef: string
): NoteEnrichmentApi {
  async function request(signal: AbortSignal, command?: NoteEnrichmentCommand) {
    const controller = new AbortController()
    const abort = () => controller.abort()
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
    const timer = setTimeout(abort, ENRICHMENT_REQUEST_TIMEOUT)
    try {
      const options: RequestInit = {
        signal: controller.signal,
        cache: 'no-store',
      }
      if (command) {
        options.method = 'POST'
        options.body = JSON.stringify(command)
      }
      const value = await fetchApi<unknown>(
        `${baseNoteEndpoint}enrichment/`,
        options
      )
      if (
        !isNoteEnrichmentState(value) ||
        value.meeting_ref !== meetingRef ||
        value.proposal?.evidence.some(
          (item) => item.citation.meeting_ref !== meetingRef
        )
      ) {
        throw new NoteEnrichmentRequestError()
      }
      return value
    } catch (error) {
      if (error instanceof ApiError)
        throw new NoteEnrichmentRequestError(error.statusCode)
      throw new NoteEnrichmentRequestError()
    } finally {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
    }
  }
  return {
    read: (signal) => request(signal),
    command: (command, signal) => request(signal, command),
  }
}
