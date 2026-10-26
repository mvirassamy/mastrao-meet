import { describe, expect, it } from 'vitest'
import { mergeHistoryDetail, normalizeHistoryDetail } from './meetingHistoryApi'
import { normalizeNativePartial } from './meetingNativePartial'
import {
  nativeCoverage,
  nativeDetail,
  nativeDigest,
  nativeEpochA,
  nativeEpochB,
  nativeEpochC,
  nativeMeetingRef,
  nativePartial,
  nativeProjection,
  nativeSegment,
} from './meetingNativePartial.fixtures'

const normalize = (value: unknown, coverage: unknown = nativeCoverage()) =>
  normalizeNativePartial(value, coverage, nativeMeetingRef)

const detail = (changes: Record<string, unknown> = {}) => {
  const result = normalizeHistoryDetail(nativeDetail(changes))
  if (!result) throw new Error('Expected valid native history fixture')
  return result
}

describe('native partial normalization', () => {
  it('keeps exact text, attribution and wire order without changing readiness', () => {
    const segments = [
      nativeSegment(),
      nativeSegment({ id: 's2', start_ms: 0, text: '  Après.\n' }),
    ]
    const raw = nativePartial({ segments, truncated: true })
    const before = JSON.stringify(raw)
    const result = normalize(raw)
    expect(result).toMatchObject({
      version: 1,
      meetingRef: nativeMeetingRef,
      snapshotDigest: nativeDigest,
      coverage: 'partial_sources',
      truncated: true,
    })
    expect(result?.segments.map((segment) => segment.text)).toEqual(
      segments.map((segment) => segment.text)
    )
    expect(result?.segments.map((segment) => segment.startMs)).toEqual([
      5000, 0,
    ])
    expect(result?.segments[0].speaker).toBe('  Camille  ')
    expect(JSON.stringify(raw)).toBe(before)
    const meeting = detail()
    expect(meeting.transcript.nativePartial).toBeDefined()
    expect(meeting.transcript.segments).toEqual([])
    expect(meeting.transcriptStatus).toBe('transcribing')
    expect(meeting.transcriptProjection).toMatchObject({
      revision: 1,
      digest: null,
    })
    expect(meeting.summaryStatus).toBe('unknown')
  })

  it.each([undefined, null, [], 1, 'partial'])(
    'rejects absent or non-object partials: %j',
    (value) => expect(normalize(value)).toBeUndefined()
  )

  it.each([
    { version: 2 },
    { meeting_ref: 'meeting_other0123456789' },
    { snapshot_digest: 'b'.repeat(64) },
    { coverage: 'complete' },
    { truncated: undefined },
    { truncated: 'false' },
    { segments: [] },
    { segments: null },
    { segments: {} },
    { canonical_revision: 2 },
  ])('rejects invalid DTO fields: %j', (changes) => {
    expect(normalize(nativePartial(changes))).toBeUndefined()
  })

  it.each([
    undefined,
    null,
    nativeCoverage({ meeting_ref: 'meeting_other0123456789' }),
    nativeCoverage({ snapshot_digest: 'A'.repeat(64) }),
    nativeCoverage({ epochs: [] }),
    nativeCoverage({ epochs: [{ epoch_ref: 'invalid', state: 'available' }] }),
    nativeCoverage({ extra: true }),
  ])('requires valid matching coverage: %j', (coverage) => {
    expect(
      normalizeNativePartial(nativePartial(), coverage, nativeMeetingRef)
    ).toBeUndefined()
  })

  it.each([
    { id: '' },
    { id: 'x'.repeat(198) },
    { start_ms: -1 },
    { start_ms: 0.1 },
    { start_ms: Number.NaN },
    { start_ms: Number.POSITIVE_INFINITY },
    { start_ms: Number.MAX_SAFE_INTEGER + 1 },
    { speaker: null },
    { speaker: 'x'.repeat(161) },
    { text: '' },
    { text: 42 },
    { text: 'x'.repeat(4001) },
    { source_epoch: undefined },
    { source_epoch: null },
    {
      source_epoch: {
        epoch_ref: nativeEpochA,
        time_basis: 'meeting_relative_ms',
      },
    },
    {
      source_epoch: {
        epoch_ref: nativeEpochB,
        time_basis: 'source_relative_ms',
      },
    },
    {
      source_epoch: {
        epoch_ref: nativeEpochC,
        time_basis: 'source_relative_ms',
      },
    },
    {
      source_epoch: {
        epoch_ref: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        time_basis: 'source_relative_ms',
      },
    },
    {
      source_epoch: {
        epoch_ref: nativeEpochA,
        time_basis: 'source_relative_ms',
        aligned: true,
      },
    },
    { source_epoch: { epoch_ref: nativeEpochA } },
    { revision: 1 },
  ])(
    'rejects the entire consultation on one malformed segment: %j',
    (changes) => {
      expect(
        normalize(
          nativePartial({
            segments: [
              nativeSegment(),
              nativeSegment({ id: 's2', ...changes }),
            ],
          })
        )
      ).toBeUndefined()
    }
  )

  it('requires source provenance on every segment, even when an otherwise valid field is missing', () => {
    const withoutEpoch: Record<string, unknown> = { ...nativeSegment() }
    delete withoutEpoch.source_epoch
    expect(
      normalize(nativePartial({ segments: [withoutEpoch] }))
    ).toBeUndefined()
  })

  it('rejects duplicate identities within an epoch but accepts the same ID across epochs', () => {
    expect(
      normalize(nativePartial({ segments: [nativeSegment(), nativeSegment()] }))
    ).toBeUndefined()
    const second = nativeSegment({
      source_epoch: {
        epoch_ref: nativeEpochB,
        time_basis: 'source_relative_ms',
      },
    })
    const coverage = nativeCoverage({
      epochs: [nativeEpochA, nativeEpochB].map((epoch_ref) => ({
        epoch_ref,
        state: 'available',
      })),
    })
    expect(
      normalize(
        nativePartial({ segments: [nativeSegment(), second] }),
        coverage
      )?.segments
    ).toHaveLength(2)
  })

  it('accepts exact limits and rejects 1001 segments without silently truncating', () => {
    const segments = Array.from({ length: 1000 }, (_, index) =>
      nativeSegment({ id: `s${index}` })
    )
    segments[0] = nativeSegment({
      id: 'x'.repeat(197),
      start_ms: Number.MAX_SAFE_INTEGER,
      speaker: 'x'.repeat(160),
      text: 'x'.repeat(4000),
    })
    expect(normalize(nativePartial({ segments }))?.segments).toHaveLength(1000)
    expect(
      normalize(
        nativePartial({
          segments: [...segments, nativeSegment({ id: 'overflow' })],
        })
      )
    ).toBeUndefined()
  })
})

describe('fresh native partial independently of canonical cache', () => {
  it('replaces a correction and a newly available epoch at the same canonical revision', () => {
    const incoming = detail({
      native_coverage: nativeCoverage({
        epochs: [nativeEpochA, nativeEpochB].map((epoch_ref) => ({
          epoch_ref,
          state: 'available',
        })),
      }),
      native_partial: nativePartial({
        segments: [
          nativeSegment({ text: '  Correction.\n' }),
          nativeSegment({
            source_epoch: {
              epoch_ref: nativeEpochB,
              time_basis: 'source_relative_ms',
            },
            text: 'Epoch arrivée.',
          }),
        ],
      }),
    })
    const merged = mergeHistoryDetail(detail(), incoming)
    expect(merged.transcriptProjection.revision).toBe(1)
    expect(merged.transcript.segments).toEqual([])
    expect(
      merged.transcript.nativePartial?.segments.map((segment) => segment.text)
    ).toEqual(['  Correction.\n', 'Epoch arrivée.'])
    expect(merged.transcript.nativePartial).toEqual(
      incoming.transcript.nativePartial
    )
  })

  it.each([
    undefined,
    null,
    nativePartial({ version: 2 }),
    nativePartial({ snapshot_digest: 'b'.repeat(64) }),
  ])(
    'clears absent, withdrawn or invalid fresh consultations with canonical cache retained: %j',
    (partial) => {
      const canonical = detail({
        transcript_projection: nativeProjection({
          state: 'available',
          revision: 5,
          digest: nativeDigest,
        }),
        transcript: {
          segments: [
            {
              id: 'provider',
              start_ms: 0,
              speaker: 'Provider',
              text: 'Canonical retained.',
            },
          ],
        },
      })
      // An earlier partial poll can coexist in cache with the immutable canonical body.
      const cached = mergeHistoryDetail(canonical, detail())
      expect(cached.transcript.nativePartial).toBeDefined()
      const incoming = detail({ native_partial: partial })
      const merged = mergeHistoryDetail(cached, incoming)
      expect(merged.transcript.nativePartial).toBeUndefined()
      expect(merged.transcriptProjection.revision).toBe(5)
      expect(merged.transcript.segments[0].text).toBe('Canonical retained.')
      expect(
        mergeHistoryDetail(merged, incoming).transcript.nativePartial
      ).toBeUndefined()
    }
  )

  it('withdraws partial content when fresh coverage is missing or no longer authorizes its epoch', () => {
    for (const coverage of [
      null,
      nativeCoverage({
        epochs: [{ epoch_ref: nativeEpochA, state: 'failed' }],
      }),
    ]) {
      const merged = mergeHistoryDetail(
        detail(),
        detail({ native_coverage: coverage })
      )
      expect(merged.transcript.nativePartial).toBeUndefined()
    }
  })

  it('withdraws the auxiliary at stabilization without changing the canonical revision', () => {
    const settled = detail({
      transcript_projection: nativeProjection({
        state: 'partial',
        digest: nativeDigest,
      }),
      transcript: { segments: [nativeSegment()], truncated: false },
      native_partial: null,
    })
    const merged = mergeHistoryDetail(detail(), settled)
    expect(merged.transcript.nativePartial).toBeUndefined()
    expect(merged.transcriptProjection.revision).toBe(1)
    expect(merged.transcript.segments).toHaveLength(1)
  })

  it.each(['available', 'partial'] as const)(
    'gives a readable %s provider/review body priority over a stray auxiliary',
    (state) => {
      const meeting = detail({
        transcript_projection: nativeProjection({
          state,
          digest: nativeDigest,
        }),
        transcript: {
          segments: [
            {
              id: 'review',
              start_ms: 0,
              speaker: 'Review',
              text: 'Reviewed provider.',
            },
          ],
        },
      })
      expect(meeting.transcript.nativePartial).toBeUndefined()
      expect(meeting.transcript.segments[0].text).toBe('Reviewed provider.')
    }
  )
  it('does not reopen a completed-empty canonical result through native consultation', () => {
    const meeting = detail({
      transcript_projection: nativeProjection({ state: 'completed_empty' }),
    })
    expect(meeting.transcript.nativePartial).toBeUndefined()
    expect(meeting.transcriptStatus).toBe('completed_empty')
    expect(meeting.transcript.segments).toEqual([])
  })

  it('requires fresh processing or a pending epoch, independently of available text', () => {
    const availableOnly = nativeCoverage({
      epochs: [{ epoch_ref: nativeEpochA, state: 'available' }],
    })
    expect(
      detail({ native_coverage: availableOnly }).transcript.nativePartial
    ).toBeDefined()
    for (const state of ['failed', 'unknown']) {
      const projection = nativeProjection({ state })
      expect(
        detail({ transcript_projection: projection }).transcript.nativePartial
      ).toBeDefined()
      expect(
        detail({
          transcript_projection: projection,
          native_coverage: availableOnly,
        }).transcript.nativePartial
      ).toBeUndefined()
    }
  })
})

it.each(['epoch_limit_exceeded', 'result_bytes_limit_exceeded'] as const)(
  'withdraws partial consultation on fresh %s at the same canonical revision',
  (reason) => {
    const current = detail()
    const incoming = detail({
      native_coverage: nativeCoverage({
        epochs_unavailable_reason: reason,
        epochs: [],
      }),
    })
    expect(current.transcript.nativePartial).toBeDefined()
    expect(incoming.transcript.nativeCoverage?.epochsUnavailableReason).toBe(
      reason
    )
    expect(incoming.transcript.nativePartial).toBeUndefined()
    expect(
      mergeHistoryDetail(current, incoming).transcript.nativePartial
    ).toBeUndefined()
    expect(
      normalize(
        nativePartial(),
        nativeCoverage({ epochs_unavailable_reason: reason })
      )
    ).toBeUndefined()
  }
)
