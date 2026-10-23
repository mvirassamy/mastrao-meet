import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createNoteEnrichmentApi,
  ENRICHMENT_REQUEST_TIMEOUT,
  NoteEnrichmentRequestError,
} from './noteEnrichmentApi'
import {
  enrichmentState,
  readyProposal,
  endpoint,
  meetingRef,
  proposalId,
} from '../test/enrichmentFixtures'

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('private enrichment transport and fixed DTO', () => {
  it('GETs state and POSTs the exact command under the base note endpoint', async () => {
    const fetch = vi
      .fn()
      .mockImplementation(async () =>
        response(enrichmentState({ proposal: readyProposal() }))
      )
    vi.stubGlobal('fetch', fetch)
    const api = createNoteEnrichmentApi(endpoint, meetingRef)
    const signal = new AbortController().signal
    await api.read(signal)
    const command = {
      version: 1 as const,
      meeting_ref: meetingRef,
      command_id: 'command_0123456789abcdef',
      action: 'apply' as const,
      expected_state_version: 3,
      expected_revision: 1,
      proposal_id: proposalId,
      additions: 'Texte approuvé',
    }
    await api.command(command, signal)
    expect(fetch.mock.calls[0][0]).toContain(`/api/v1.0${endpoint}enrichment/`)
    expect(fetch.mock.calls[0][1]).toMatchObject({
      cache: 'no-store',
      credentials: 'include',
    })
    expect(fetch.mock.calls[0][1].body).toBeUndefined()
    expect(fetch.mock.calls[1][1]).toMatchObject({
      method: 'POST',
      body: JSON.stringify(command),
    })
  })

  it.each([
    null,
    enrichmentState({ version: 2 as 1 }),
    enrichmentState({ state_version: -1 }),
    enrichmentState({ reason: 'guest' as 'empty_note' }),
    { ...enrichmentState(), content: 'unexpected private text' },
    enrichmentState({ proposal: readyProposal({ text: 'x'.repeat(8001) }) }),
    enrichmentState({ proposal: readyProposal({ text: '\u0000' }) }),
    enrichmentState({ proposal: readyProposal({ text: '\ud800' }) }),
    enrichmentState({
      proposal: readyProposal({
        evidence: [
          {
            ...readyProposal().evidence[0],
            citation: { ...readyProposal().evidence[0].citation, end_ms: -1 },
          },
        ],
      }),
    }),
    enrichmentState({
      proposal: readyProposal({
        coverage: { ...readyProposal().coverage!, omitted_segments: 0 },
      }),
    }),
    enrichmentState({ meeting_ref: 'another_0123456789abcdef' }),
    enrichmentState({
      proposal: readyProposal({
        evidence: [
          {
            ...readyProposal().evidence[0],
            citation: {
              ...readyProposal().evidence[0].citation,
              meeting_ref: 'another_0123456789abcdef',
            },
          },
        ],
      }),
    }),
  ])(
    'rejects an invalid, unbounded or differently scoped response',
    async (value) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(value)))
      await expect(
        createNoteEnrichmentApi(endpoint, meetingRef).read(
          new AbortController().signal
        )
      ).rejects.toBeInstanceOf(NoteEnrichmentRequestError)
    }
  )

  it('accepts composed characters and emoji without normalizing the text', async () => {
    const state = enrichmentState({
      proposal: readyProposal({ text: 'e\u0301 😀' }),
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(state)))
    expect(
      await createNoteEnrichmentApi(endpoint, meetingRef).read(
        new AbortController().signal
      )
    ).toEqual(state)
  })

  it('keeps HTTP error bodies opaque', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ notes: 'secret' }, 409))
    )
    const error = await createNoteEnrichmentApi(endpoint, meetingRef)
      .read(new AbortController().signal)
      .catch((error: unknown) => error)
    expect(error).toMatchObject({ statusCode: 409 })
    expect(JSON.stringify(error)).not.toContain('secret')
    expect(String(error)).not.toContain('secret')
  })

  it.each(['timeout', 'scope'])(
    'aborts on %s and releases its timer',
    async (reason) => {
      vi.useFakeTimers()
      let signal: AbortSignal | undefined
      vi.stubGlobal(
        'fetch',
        vi.fn(
          (_url, options: RequestInit) =>
            new Promise((_resolve, reject) => {
              signal = options.signal as AbortSignal
              signal.addEventListener('abort', () =>
                reject(new DOMException('Aborted', 'AbortError'))
              )
            })
        )
      )
      const scope = new AbortController()
      const result = createNoteEnrichmentApi(endpoint, meetingRef)
        .read(scope.signal)
        .catch((error: unknown) => error)
      if (reason === 'timeout')
        await vi.advanceTimersByTimeAsync(ENRICHMENT_REQUEST_TIMEOUT)
      else scope.abort()
      expect(await result).toBeInstanceOf(NoteEnrichmentRequestError)
      expect(signal?.aborted).toBe(true)
      expect(vi.getTimerCount()).toBe(0)
    }
  )
})
