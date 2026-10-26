import { describe, expect, expectTypeOf, it } from 'vitest'
import { normalizeHistoryDetail } from './meetingHistoryApi'
import { normalizeLiveFull, liveFullPreviewRequest } from './meetingLiveFull'
import {
  liveFullDetail,
  liveFullResponse,
  liveFullSource,
} from './meetingLiveFull.fixtures'
import {
  liveDetail,
  liveFinal,
  liveMeetingRef,
  liveModelEvidence,
  liveSnapshot,
  liveSource,
} from './meetingLiveTranscript.fixtures'
import { shouldRequestSummary } from './useMeetingHistory'
import type { MeetingLiveWireEvent } from './types'

const normalize = (
  events: unknown[],
  sourceChanges: Record<string, unknown> = {}
) =>
  normalizeHistoryDetail(
    liveDetail({
      live_transcript: liveSnapshot([liveSource(events, sourceChanges)]),
    })
  )!

describe('retained native model evidence', () => {
  it('ties each wire kind to its version', () => {
    expectTypeOf<
      Extract<MeetingLiveWireEvent, { kind: 'model_evidence' }>['version']
    >().toEqualTypeOf<3>()
    expectTypeOf<
      Extract<MeetingLiveWireEvent, { kind: 'final' }>['version']
    >().toEqualTypeOf<2>()
  })

  it('retains exact evidence separately from historical text and markers', () => {
    const evidence = liveModelEvidence()
    const final = liveFinal()
    const gap = {
      version: 2,
      source_ref: final.source_ref,
      leg_ref: final.leg_ref,
      sequence: 5,
      kind: 'gap',
      reason: 'connection_lost',
    }
    const end = {
      ...gap,
      sequence: 6,
      kind: 'end',
      reason: 'meeting_ended',
      final_event_count: 1,
    }
    const detail = normalize([final, evidence, gap, end])
    expect(detail.transcript.liveSources?.[0].events).toEqual([final, gap, end])
    expect(detail.transcript.liveSources?.[0].modelEvidence).toEqual([evidence])
    expect(
      Object.keys(detail.transcript.liveSources![0].modelEvidence![0])
    ).toEqual([
      'version',
      'source_ref',
      'leg_ref',
      'sequence',
      'kind',
      'provider',
      'models',
      'truncated',
    ])
    expect(detail.transcriptStatus).toBe('failed')
    expect(detail.transcript.segments).toEqual([])
    expect(shouldRequestSummary(detail)).toBe(false)
  })

  it.each([{ models: [] }, { models: liveModelEvidence().models }])(
    'keeps metadata-only history auxiliary even when evidence is truncated: %j',
    ({ models }) => {
      const evidence = liveModelEvidence({ models, truncated: true })
      const detail = normalize([evidence], { incomplete: false })
      expect(detail.transcript.liveSources?.[0].modelEvidence).toEqual([
        evidence,
      ])
      expect(detail.transcript.liveSources?.[0].events).toEqual([])
      expect(detail.transcript.liveSources?.[0].incomplete).toBe(false)
      expect(detail.transcript.liveTruncated).toBe(false)
      expect(detail.transcript.segments).toEqual([])
      expect(detail.transcriptStatus).toBe('failed')
      expect(detail.transcriptProjection.state).toBe('failed')
      expect(detail.summaryStatus).toBe('not_started')
      expect(shouldRequestSummary(detail)).toBe(false)
    }
  )

  it.each(['openai', 'deepgram', 'mistral'])(
    'accepts supplied session, result and terminal facts for %s',
    (provider) => {
      const models = ['session', 'result', 'terminal'].map((origin) => ({
        ...liveModelEvidence().models[0],
        origin,
      }))
      const evidence = liveModelEvidence({ provider, models })
      expect(
        normalize([evidence]).transcript.liveSources?.[0].modelEvidence
      ).toEqual([evidence])
    }
  )

  it('retains independent legs and validates sequence identity across text and evidence', () => {
    const evidence = liveModelEvidence()
    const other = liveModelEvidence({
      leg_ref: 'leg_other0123456789',
      sequence: 5,
    })
    expect(
      normalize([evidence, other]).transcript.liveSources?.[0].modelEvidence
    ).toEqual([evidence, other])
    expect(
      normalize([liveFinal({ sequence: 4 }), evidence]).transcript.liveSources
    ).toBeUndefined()
  })

  it('keeps historical absence distinct from a new empty snapshot', () => {
    expect(
      normalize([liveFinal()]).transcript.liveSources?.[0]
    ).not.toHaveProperty('modelEvidence')
    const evidence = liveModelEvidence({ models: [] })
    expect(
      normalize([evidence]).transcript.liveSources?.[0].modelEvidence
    ).toEqual([evidence])
  })

  it('accepts eight distinct records and 160 UTF-16 units without trimming', () => {
    const models = Array.from({ length: 8 }, (_, index) => ({
      ...liveModelEvidence().models[0],
      model_ref: String(index),
      name: '😀'.repeat(80),
      version: 'é'.repeat(160),
      architecture: 'x'.repeat(160),
    }))
    const evidence = liveModelEvidence({ models })
    expect(
      normalize([evidence]).transcript.liveSources?.[0].modelEvidence
    ).toEqual([evidence])
  })

  it.each(['model_ref', 'name', 'version', 'architecture'])(
    'accepts %s as the only supplied model field',
    (field) => {
      const model = {
        origin: 'session',
        model_ref: null,
        name: null,
        version: null,
        architecture: null,
        [field]: '  supplied  ',
      }
      const evidence = liveModelEvidence({ models: [model] })
      expect(
        normalize([evidence]).transcript.liveSources?.[0].modelEvidence
      ).toEqual([evidence])
    }
  )

  it.each([
    { version: 2 },
    { version: 1 },
    { version: 4 },
    { version: '3' },
    { kind: 'evidence' },
    { provider: 'unknown' },
    { provider: null },
    { source_ref: 'source_foreign0123456789' },
    { source_ref: '../unsafe' },
    { leg_ref: 'bad ref' },
    { leg_ref: undefined },
    { sequence: 0 },
    { sequence: 11 },
    { sequence: true },
    { sequence: 1.5 },
    { models: null },
    { models: {} },
    { models: undefined },
    { truncated: undefined },
    { truncated: 'false' },
    { text: 'not transcript text' },
    { usage: {} },
    { raw: {} },
    {
      models: Array.from({ length: 9 }, (_, index) => ({
        ...liveModelEvidence().models[0],
        model_ref: String(index),
      })),
    },
    { models: [liveModelEvidence().models[0], liveModelEvidence().models[0]] },
  ])('refuses malformed or unbound evidence: %j', (changes) => {
    expect(
      normalize([liveFinal(), liveModelEvidence(changes)]).transcript
        .liveSources
    ).toBeUndefined()
  })

  it.each([
    null,
    [],
    {},
    { origin: 'unknown' },
    { origin: null },
    { origin: undefined },
    { name: undefined },
    { name: 42 },
    { name: '' },
    { name: ' \n\t' },
    { model_ref: 'x'.repeat(161) },
    { name: '😀'.repeat(81) },
    { name: '\ud800' },
    { name: '\udfff' },
    { version: 'x'.repeat(161) },
    { architecture: 'x'.repeat(161) },
    { model_ref: null, name: null, version: null, architecture: null },
    { usage: 1 },
    { text: 'not a model field' },
  ])('refuses invalid model records: %j', (changes) => {
    let record: unknown = changes
    if (changes && !Array.isArray(changes) && Object.keys(changes).length > 0) {
      record = { ...liveModelEvidence().models[0], ...changes }
    }
    expect(
      normalize([liveModelEvidence({ models: [record] })]).transcript
        .liveSources
    ).toBeUndefined()
  })

  it.each([
    liveFinal({ version: 3 }),
    ...['gap', 'end'].map((kind) => {
      const marker = {
        version: 3,
        source_ref: liveFinal().source_ref,
        leg_ref: liveFinal().leg_ref,
        sequence: 4,
        kind,
        reason: 'connection_lost',
      }
      if (kind === 'end')
        return { ...marker, reason: 'meeting_ended', final_event_count: 0 }
      return marker
    }),
  ])('refuses version 3 for historical events: %j', (event) => {
    expect(normalize([event]).transcript.liveSources).toBeUndefined()
  })

  it.each([false, true])(
    'preserves evidence in full consultation with text=%s',
    (withText) => {
      const current = normalizeHistoryDetail(liveFullDetail())!
      const expected = liveFullPreviewRequest(
        liveMeetingRef,
        current.transcript
      )!
      const evidence = liveModelEvidence()
      const events: unknown[] = [evidence]
      if (withText) events.unshift(liveFinal())
      const raw = liveFullResponse({ sources: [liveFullSource(events)] })
      const full = normalizeLiveFull(raw, expected)!
      expect(full.sources[0].modelEvidence).toEqual([evidence])
      expect(full.sources[0].events).toEqual(withText ? [liveFinal()] : [])
      expect(full).not.toHaveProperty('status')
      const invalid = liveFullResponse({
        sources: [liveFullSource([liveModelEvidence({ version: 2 })])],
      })
      expect(normalizeLiveFull(invalid, expected)).toBeUndefined()
    }
  )
})
