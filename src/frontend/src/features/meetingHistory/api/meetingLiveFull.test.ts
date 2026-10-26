import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { mergeHistoryDetail, normalizeHistoryDetail } from './meetingHistoryApi'
import {
  fetchLiveFull,
  liveFullPreviewRequest,
  normalizeLiveFull,
} from './meetingLiveFull'
import {
  liveFullDetail,
  liveFullPreview,
  liveFullResponse,
  liveFullSource,
  liveHeadsDigest,
} from './meetingLiveFull.fixtures'
import { liveFinal, liveMeetingRef } from './meetingLiveTranscript.fixtures'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
afterEach(() => vi.resetAllMocks())
const detail = (changes: Record<string, unknown> = {}) =>
  normalizeHistoryDetail(liveFullDetail(changes))!
const request = () =>
  liveFullPreviewRequest(liveMeetingRef, detail().transcript)!

describe('fresh live consultation binding', () => {
  it.each([
    undefined,
    null,
    {},
    { ...liveFullPreview(), source_heads_digest: 'A'.repeat(64) },
    { ...liveFullPreview(), sources: [] },
    { ...liveFullPreview(), truncated: false },
  ])('offers no expansion for an absent, old or invalid preview: %j', (raw) => {
    const current = detail()
    const incoming = detail({ live_transcript: raw })
    const merged = mergeHistoryDetail(current, incoming)
    expect(merged.transcript.liveFullSnapshot).toBeUndefined()
    expect(merged.transcript.segments).toEqual(current.transcript.segments)
    expect(
      liveFullPreviewRequest(liveMeetingRef, merged.transcript)
    ).toBeUndefined()
  })
  it('uses the exact wire body and meeting independently of a retained HF revision', () => {
    const before = detail()
    const raw = liveFullPreview({ source_heads_digest: 'c'.repeat(64) })
    const incoming = detail({
      live_transcript: raw,
      transcript_projection: {
        version: 1,
        state: 'transcribing',
        revision: 1,
        digest: null,
        source: null,
      },
    })
    const merged = mergeHistoryDetail(before, incoming)
    expect(merged.transcript.projection.revision).toBe(5)
    expect(merged.transcript.liveFullSnapshot?.previewIdentity).toBe(
      JSON.stringify([liveMeetingRef, raw])
    )
    expect(
      liveFullPreviewRequest(liveMeetingRef, merged.transcript)
        ?.sourceHeadsDigest
    ).toBe('c'.repeat(64))
  })
  it('keeps a valid old producer preview usable without an expansion button', () => {
    const old: Record<string, unknown> = liveFullPreview()
    delete old.source_heads_digest
    const transcript = detail({ live_transcript: old }).transcript
    expect(transcript.liveSources).toHaveLength(1)
    expect(transcript.liveFullSnapshot).toBeUndefined()
  })
})

describe('strict retained live JSON', () => {
  it('keeps corrected order, revisions, Unicode, local timing and markers exact', () => {
    const raw = liveFullResponse()
    const full = normalizeLiveFull(raw, request())!
    expect(full.sources[0].events).toEqual(raw.sources[0].events)
    expect(full.snapshotDigest).toBe('b'.repeat(64))
  })
  it('keeps an eventless consultation auxiliary instead of inventing completed_empty', () => {
    const full = normalizeLiveFull(
      liveFullResponse({ sources: [liveFullSource([])] }),
      request()
    )
    expect(full?.sources[0].events).toEqual([])
    expect(full).not.toHaveProperty('status')
  })
  it.each([
    { version: 2 },
    { meeting_ref: 'meeting_other0123456789' },
    { matter_ref: 'matter_other0123456789' },
    { source_heads_digest: 'c'.repeat(64) },
    { snapshot_digest: 'B'.repeat(64) },
    { coverage: 'complete' },
    { meeting_coverage: 'verified' },
    { ordering: 'global' },
    { truncated: true },
    { retention_expires_at: 0 },
    { retention_expires_at: 0.5 },
    { retention_expires_at: Number.MAX_SAFE_INTEGER + 1 },
    { retention_expires_at: Math.floor(Date.now() / 1000) },
    { extra: true },
    { sources: [] },
    { sources: null },
  ])('refuses malformed or mismatched envelopes: %j', (changes) => {
    expect(
      normalizeLiveFull(liveFullResponse(changes), request())
    ).toBeUndefined()
  })
  it.each([
    { source_ref: 'source_other0123456789' },
    { participant_kind: 'guest' },
    { participant_ref: 'participant_other0123456789' },
    { state: 'collecting' },
    { incomplete: false },
    { confirmed_sequence: 99999 },
    { extra: true },
  ])(
    'compares every source header even when the declared digest matches: %j',
    (changes) => {
      const raw = liveFullResponse({ sources: [liveFullSource([], changes)] })
      expect(normalizeLiveFull(raw, request())).toBeUndefined()
    }
  )
  it('compares canonical source order rather than treating heads as an unordered set', () => {
    const first = liveFullSource([])
    const second = liveFullSource([], { source_ref: 'source_second0123456789' })
    const transcript = detail({
      live_transcript: liveFullPreview({ sources: [first, second] }),
    }).transcript
    const expected = liveFullPreviewRequest(liveMeetingRef, transcript)!
    expect(
      normalizeLiveFull(
        liveFullResponse({ sources: [first, second] }),
        expected
      )
    ).toBeTruthy()
    expect(
      normalizeLiveFull(
        liveFullResponse({ sources: [second, first] }),
        expected
      )
    ).toBeUndefined()
  })
  it.each([
    [liveFinal(), liveFinal({ sequence: 4 })],
    [liveFinal(), liveFinal({ item_ref: 'other' })],
    [liveFinal({ revision: 0 })],
    [liveFinal({ text: '  ' })],
    [
      liveFinal({
        timing: {
          state: 'source_buffer',
          sample_rate: 24000,
          start_sample: 2,
          end_sample: 1,
        },
      }),
    ],
    [{ ...liveFinal(), kind: 'gap', reason: 'unknown' }],
  ])(
    'shares preview event invariants for retained responses: %j',
    (...events) => {
      expect(
        normalizeLiveFull(
          liveFullResponse({ sources: [liveFullSource(events)] }),
          request()
        )
      ).toBeUndefined()
    }
  )
  it('accepts 8192 compact valid events in full and rejects the sentinel without extending preview', () => {
    const sourceRef = 's'.repeat(16)
    const events = Array.from({ length: 8192 }, (_, i) =>
      liveFinal({
        source_ref: sourceRef,
        leg_ref: 'l'.repeat(16),
        sequence: i + 1,
        item_ref: String(i),
        text: 'x',
        timing: { state: 'unavailable' },
      })
    )
    const sources = [liveFullSource(events, { source_ref: sourceRef })]
    const preview = liveFullPreview({
      sources: [liveFullSource([], { source_ref: sourceRef })],
    })
    const expected = liveFullPreviewRequest(
      liveMeetingRef,
      detail({ live_transcript: preview }).transcript
    )!
    expect(
      normalizeLiveFull(liveFullResponse({ sources }), expected)?.sources[0]
        .events
    ).toHaveLength(8192)
    expect(
      detail({ live_transcript: liveFullPreview({ sources }) }).transcript
        .liveSources
    ).toBeUndefined()
    events.push({ ...events[0], sequence: 8193, item_ref: 'excess' })
    expect(
      normalizeLiveFull(liveFullResponse({ sources }), expected)
    ).toBeUndefined()
  })
  it('keeps the 32-source bound on both surfaces', () => {
    const sources = Array.from({ length: 32 }, (_, i) =>
      liveFullSource([], {
        source_ref: `source_boundary_${i.toString().padStart(3, '0')}`,
      })
    )
    const expected = liveFullPreviewRequest(
      liveMeetingRef,
      detail({ live_transcript: liveFullPreview({ sources }) }).transcript
    )!
    expect(
      normalizeLiveFull(liveFullResponse({ sources }), expected)?.sources
    ).toHaveLength(32)
    sources.push(liveFullSource([]))
    expect(
      normalizeLiveFull(liveFullResponse({ sources }), expected)
    ).toBeUndefined()
  })
  it('accepts exactly 2 MiB of UTF-8 JSON and refuses the next byte', () => {
    const events = Array.from({ length: 600 }, (_, i) =>
      liveFinal({ sequence: i + 1, item_ref: `item-${i}`, text: 'x' })
    )
    const raw = liveFullResponse({ sources: [liveFullSource(events)] })
    let remaining = 2 * 1024 * 1024 - new Blob([JSON.stringify(raw)]).size
    for (const event of events) {
      const count = Math.min(3998, remaining)
      event.text += 'x'.repeat(count)
      remaining -= count
    }
    expect(remaining).toBe(0)
    expect(normalizeLiveFull(raw, request())).toBeTruthy()
    events.at(-1)!.text += 'x'
    expect(normalizeLiveFull(raw, request())).toBeUndefined()
  })
  it('requires every field of the retained envelope', () => {
    for (const field of Object.keys(liveFullResponse())) {
      const raw: Record<string, unknown> = liveFullResponse()
      delete raw[field]
      expect(normalizeLiveFull(raw, request()), field).toBeUndefined()
    }
  })
})

it('fetches once through the authenticated proxy, no-store and abortable, without rewriting finals', async () => {
  const expected = request()
  vi.mocked(fetchApi).mockResolvedValue(liveFullResponse())
  const controller = new AbortController()
  expect(await fetchLiveFull(expected, controller.signal)).toEqual(
    normalizeLiveFull(liveFullResponse(), expected)
  )
  expect(fetchApi).toHaveBeenCalledExactlyOnceWith(
    `meetings/history/${liveMeetingRef}/live-transcript/?source_heads_digest=${liveHeadsDigest}`,
    { cache: 'no-store', signal: controller.signal }
  )
})
