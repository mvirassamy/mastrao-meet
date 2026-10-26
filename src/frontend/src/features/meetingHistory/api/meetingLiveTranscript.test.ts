import { describe, expect, it } from 'vitest'
import { mergeHistoryDetail, normalizeHistoryDetail } from './meetingHistoryApi'
import { shouldRequestSummary } from './useMeetingHistory'
import {
  liveDetail,
  liveFinal,
  liveLegRef,
  liveSnapshot,
  liveSource,
  liveSourceRef,
} from './meetingLiveTranscript.fixtures'

const normalize = (changes: Record<string, unknown> = {}) => {
  const detail = normalizeHistoryDetail(liveDetail(changes))
  if (!detail) throw new Error('Expected a meeting detail')
  return detail
}

describe('retained live history normalization', () => {
  it('keeps exact corrected text in reader order without promoting canonical readiness', () => {
    const corrected = liveFinal({ sequence: 8, revision: 4 })
    const laterItem = liveFinal({
      sequence: 2,
      item_ref: 'item-2',
      text: 'Non.  ',
    })
    const detail = normalize({
      live_transcript: liveSnapshot([liveSource([corrected, laterItem])]),
    })
    expect(detail.transcript.liveSources?.[0].events).toEqual([
      corrected,
      laterItem,
    ])
    expect(detail.transcript.liveSources?.[0].events[0]).not.toHaveProperty(
      'startMs'
    )
    expect(detail.transcriptStatus).toBe('failed')
    expect(detail.transcriptProjection.state).toBe('failed')
    expect(detail.transcript.segments).toEqual([])
    expect(detail.summaryStatus).toBe('not_started')
    expect(shouldRequestSummary(detail)).toBe(false)
  })

  it('keeps item identities distinct across legs and sequences distinct across sources', () => {
    const otherSource = 'source_other0123456789'
    const otherLeg = 'leg_other0123456789'
    const sources = [
      liveSource([liveFinal(), liveFinal({ leg_ref: otherLeg, sequence: 4 })]),
      liveSource([liveFinal({ source_ref: otherSource })], {
        source_ref: otherSource,
        participant_kind: 'guest',
      }),
    ]
    const detail = normalize({ live_transcript: liveSnapshot(sources) })
    expect(
      detail.transcript.liveSources?.map((source) => source.sourceRef)
    ).toEqual([liveSourceRef, otherSource])
    expect(
      detail.transcript.liveSources?.[0].events.map((event) => event.leg_ref)
    ).toEqual([liveLegRef, otherLeg])
  })

  it('rejects a duplicate source sequence across different capture legs', () => {
    const detail = normalize({
      live_transcript: liveSnapshot([
        liveSource([
          liveFinal(),
          liveFinal({ leg_ref: 'leg_other0123456789' }),
        ]),
      ]),
    })
    expect(detail.transcript.liveSources).toBeUndefined()
  })

  it('retains gap/end markers, unavailable timing, empty sources and truncation', () => {
    const final = liveFinal({ timing: { state: 'unavailable' } })
    const gap = {
      version: 2,
      kind: 'gap',
      source_ref: liveSourceRef,
      leg_ref: liveLegRef,
      sequence: 4,
      reason: 'connection_lost',
    }
    const end = {
      ...gap,
      kind: 'end',
      sequence: 5,
      reason: 'capture_incomplete',
      final_event_count: 2,
    }
    const detail = normalize({
      live_transcript: {
        ...liveSnapshot([
          liveSource([final, gap, end]),
          liveSource([], {
            source_ref: 'source_empty0123456789',
            confirmed_sequence: 0,
            state: 'collecting',
          }),
        ]),
        truncated: true,
      },
    })
    expect(detail.transcript.liveSources?.[0].events).toEqual([final, gap, end])
    expect(detail.transcript.liveSources?.[1].events).toEqual([])
    expect(detail.transcript.liveTruncated).toBe(true)
  })

  it.each([
    { version: 2 },
    { meeting_ref: 'meeting_other0123456789' },
    { meeting_ref: 'bad ref' },
    { matter_ref: '../unsafe' },
    { coverage: 'complete' },
    { meeting_coverage: 'verified' },
    { truncated: 'true' },
    { sources: {} },
  ])('rejects invalid live metadata: %j', (changes) => {
    expect(
      normalize({ live_transcript: { ...liveSnapshot(), ...changes } })
        .transcript.liveSources
    ).toBeUndefined()
  })

  it.each([
    { source_ref: '../unsafe' },
    { participant_kind: 'speaker' },
    { participant_ref: ' ' },
    { state: 'complete' },
    { incomplete: null },
    { confirmed_sequence: true },
    { confirmed_sequence: 100001 },
    { events: null },
  ])('rejects invalid source metadata: %j', (changes) => {
    expect(
      normalize({
        live_transcript: liveSnapshot([liveSource(undefined, changes)]),
      }).transcript.liveSources
    ).toBeUndefined()
  })

  it.each([
    { version: 1 },
    { kind: 'interim' },
    { source_ref: 'source_other0123456789' },
    { leg_ref: 'bad ref' },
    { item_ref: 'bad:item' },
    { sequence: 0 },
    { sequence: 11 },
    { sequence: 1.5 },
    { revision: 0 },
    { revision: Number.MAX_SAFE_INTEGER + 1 },
    { text: ' \n\t' },
    { text: 42 },
    { text: 'x'.repeat(4001) },
    { language: undefined },
    { language: 'english' },
    { timing: null },
    { timing: { state: 'word', start_ms: 1000 } },
    { timing: { state: 'unavailable', start_ms: 0 } },
    {
      timing: {
        state: 'source_buffer',
        sample_rate: 48000,
        start_sample: 0,
        end_sample: 1,
      },
    },
    {
      timing: {
        state: 'source_buffer',
        sample_rate: 24000,
        start_sample: 1,
        end_sample: 1,
      },
    },
    {
      timing: {
        state: 'source_buffer',
        sample_rate: 24000,
        start_sample: -1,
        end_sample: 1,
      },
    },
    {
      timing: {
        state: 'source_buffer',
        sample_rate: 24000,
        start_sample: 0,
        end_sample: 540 * 24000 + 1,
      },
    },
  ])('never displays an invalid final: %j', (changes) => {
    expect(
      normalize({
        live_transcript: liveSnapshot([liveSource([liveFinal(changes)])]),
      }).transcript.liveSources
    ).toBeUndefined()
  })

  it.each([
    [liveSource(), liveSource()],
    [liveSource([liveFinal(), liveFinal({ item_ref: 'other' })])],
    [liveSource([liveFinal(), liveFinal({ sequence: 4, revision: 3 })])],
    [
      liveSource([
        {
          version: 2,
          kind: 'gap',
          source_ref: liveSourceRef,
          leg_ref: liveLegRef,
          sequence: 4,
          reason: 'unknown',
        },
      ]),
    ],
    [
      liveSource([
        {
          version: 2,
          kind: 'end',
          source_ref: liveSourceRef,
          leg_ref: liveLegRef,
          sequence: 4,
          reason: 'meeting_ended',
          final_event_count: 4,
        },
      ]),
    ],
  ])('rejects ambiguous identities or invalid markers: %j', (...sources) => {
    expect(
      normalize({ live_transcript: liveSnapshot(sources) }).transcript
        .liveSources
    ).toBeUndefined()
  })

  it('accepts 32 sources and 1000 total events, rejects either excess', () => {
    const events = Array.from({ length: 1000 }, (_, index) =>
      liveFinal({ sequence: index + 1, item_ref: `item-${index}` })
    )
    const sources = Array.from({ length: 32 }, (_, index) =>
      liveSource([], {
        source_ref: `source_bounded_${String(index).padStart(3, '0')}`,
      })
    )
    sources[0] = liveSource(events, { confirmed_sequence: 1000 })
    expect(
      normalize({ live_transcript: liveSnapshot(sources) }).transcript
        .liveSources
    ).toHaveLength(32)
    expect(
      normalize({ live_transcript: liveSnapshot([...sources, liveSource()]) })
        .transcript.liveSources
    ).toBeUndefined()
    sources[1] = liveSource([liveFinal()], {
      source_ref: 'source_excess01234567',
    })
    expect(
      normalize({ live_transcript: liveSnapshot(sources) }).transcript
        .liveSources
    ).toBeUndefined()
  })

  it('retains valid live data alongside a canonical transcript larger than the live budget', () => {
    const canonicalText = 'é'.repeat(512 * 1024)
    const detail = normalize({
      transcript: {
        status: 'available',
        segments: [{ id: 's1', text: canonicalText, start_ms: 0 }],
      },
    })
    expect(detail.transcript.liveSources?.[0].events).toEqual([liveFinal()])
    expect(detail.transcript.segments[0].text).toBe(canonicalText)
  })

  it('bounds only live DTO UTF-8 bytes while leaving canonical text unchanged', () => {
    const events = Array.from({ length: 140 }, (_, index) =>
      liveFinal({ sequence: index + 1, item_ref: `item-${index}`, text: 'x' })
    )
    const live = liveSnapshot([liveSource(events, { confirmed_sequence: 140 })])
    let remainingBytes = 512 * 1024 - new Blob([JSON.stringify(live)]).size
    for (const event of events) {
      const addedBytes = Math.min(3999, remainingBytes)
      event.text += 'x'.repeat(addedBytes)
      remainingBytes -= addedBytes
    }
    expect(remainingBytes).toBe(0)
    const response = liveDetail({
      transcript: {
        status: 'available',
        segments: [{ id: 's1', text: 'Canonique.', start_ms: 0 }],
      },
      live_transcript: live,
    })
    expect(
      normalizeHistoryDetail(response)?.transcript.liveSources
    ).toHaveLength(1)
    events[events.length - 1].text += 'é'
    expect(
      normalizeHistoryDetail(response)?.transcript.liveSources
    ).toBeUndefined()
    expect(normalizeHistoryDetail(response)?.transcript.segments[0].text).toBe(
      'Canonique.'
    )
  })
})

describe('live content refresh independently of canonical projection retention', () => {
  const cached = () =>
    normalize({
      transcript_projection: {
        version: 1,
        state: 'available',
        revision: 5,
        digest: 'a'.repeat(64),
        source: null,
      },
      transcript: {
        segments: [{ id: 's1', start_ms: 0, text: 'Canonical retained.' }],
      },
    })

  it.each([undefined, null, { ...liveSnapshot(), coverage: 'complete' }])(
    'clears live content on each absent, null or invalid fresh read: %j',
    (live) => {
      const incoming = normalize({ live_transcript: live })
      const merged = mergeHistoryDetail(cached(), incoming)
      expect(merged.transcript.segments[0].text).toBe('Canonical retained.')
      expect(merged.transcript.liveSources).toBeUndefined()
      expect(merged.transcript.liveTruncated).toBeUndefined()
      expect(
        mergeHistoryDetail(merged, incoming).transcript.liveSources
      ).toBeUndefined()
    }
  )

  it('replaces older live content even when an older canonical revision is retained', () => {
    const replacement = liveFinal({ text: 'Correction exacte.  ' })
    const merged = mergeHistoryDetail(
      cached(),
      normalize({ live_transcript: liveSnapshot([liveSource([replacement])]) })
    )
    expect(merged.transcript.projection.revision).toBe(5)
    expect(merged.transcript.liveSources?.[0].events).toEqual([replacement])
  })
})
