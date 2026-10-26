import { describe, expect, it } from 'vitest'
import { mergeHistoryDetail, normalizeHistoryDetail } from './meetingHistoryApi'
import { liveFullDetail, liveFullPreview } from './meetingLiveFull.fixtures'
import { liveDomainFixture } from './meetingLiveObservedDomain.fixtures'
import {
  liveDomainBlockerReasons,
  normalizeLiveObservedDomain,
} from './meetingLiveObservedDomain'

const detail = (domain: unknown) =>
  normalizeHistoryDetail(
    liveFullDetail({
      live_transcript: liveFullPreview({ observed_domain: domain }),
    })
  )!

describe('live observed-domain read boundary', () => {
  it('retains the separate projection identity without promoting text or rewriting source heads', () => {
    const raw = liveDomainFixture()
    const value = detail(raw)
    expect(value.transcript.liveObservedDomain).toMatchObject({
      projectionDigest: raw.projection_digest,
      snapshotDigest: raw.snapshot_digest,
      state: 'observed',
      counts: raw.counts,
    })
    expect(value.transcript.liveFullSnapshot?.sourceHeadsDigest).toBe(
      'a'.repeat(64)
    )
    expect(value.transcript.liveFullSnapshot?.previewIdentity).toContain(
      raw.projection_digest
    )
    expect(value.transcript.segments[0].text).toBe('Canonique prioritaire.')
    expect(value.transcriptProjection.revision).toBe(5)
  })

  it('keeps unknown counts null and valid observed zeros distinct', () => {
    expect(
      normalizeLiveObservedDomain(
        liveDomainFixture({
          state: 'unknown',
          counts: null,
          blockers: [{ reason: 'inventory_contradiction', epoch_ref: null }],
        })
      )?.counts
    ).toBeNull()
    const counts = Object.fromEntries(
      Object.keys(liveDomainFixture().counts).map((key) => [key, 0])
    )
    expect(
      normalizeLiveObservedDomain(liveDomainFixture({ counts }))?.counts
    ).toEqual(counts)
  })

  const invalid = [
    { scope: 'complete_meeting' },
    { extra: true },
    { closure_state: 'open' },
    { domain_ref: '10000000-0000-4000-8000-00000000000A' },
    { domain_ref: 'not-a-uuid' },
    { producer_revision: 0 },
    { producer_revision: 1025 },
    { producer_revision: -0 },
    { producer_revision: Number.MAX_SAFE_INTEGER },
    { projection_digest: 'A'.repeat(64) },
    { retention_expires_at: -0 },
    { retention_expires_at: 1.1 },
    { retention_expires_at: Infinity },
    { counts: null },
    { state: 'unknown' },
    { state: 'unknown', counts: null, blockers: [] },
    { blockers: [{ reason: 'unknown', epoch_ref: null }] },
    { blockers: [{ reason: 'journal_end_unconfirmed', epoch_ref: 'bad' }] },
    {
      blockers: [
        { reason: 'journal_end_unconfirmed', epoch_ref: null, extra: true },
      ],
    },
    {
      blockers: Array.from({ length: 129 }, () => ({
        reason: 'journal_end_unconfirmed',
        epoch_ref: null,
      })),
    },
    { counts: { ...liveDomainFixture().counts, observations: 129 } },
    {
      counts: {
        ...liveDomainFixture().counts,
        microphone_epochs: 0,
        correlated_epochs: 0,
      },
    },
    {
      counts: {
        ...liveDomainFixture().counts,
        microphone_epochs: 32,
        unclassified_epochs: 1,
      },
    },
    { counts: { ...liveDomainFixture().counts, correlated_epochs: 2 } },
    { counts: { ...liveDomainFixture().counts, admitted_sources: 0 } },
    { counts: { ...liveDomainFixture().counts, observations: -0 } },
    { counts: { ...liveDomainFixture().counts, extra: 0 } },
    {
      state: 'unknown',
      counts: null,
      blockers: [{ reason: 'revision_exhausted', epoch_ref: null }],
    },
    {
      state: 'unknown',
      counts: null,
      blockers: [
        {
          reason: 'blocker_limit_exceeded',
          epoch_ref: '10000000-0000-4000-8000-000000000001',
        },
      ],
    },
  ]
  it.each(invalid.map((changes, index) => ({ changes, index })))(
    'rejects ambiguous or noncanonical metadata $index through history normalization',
    ({ changes }) => {
      const raw = liveDomainFixture(changes)
      expect(normalizeLiveObservedDomain(raw)).toBeUndefined()
      expect(detail(raw).transcript.liveSources).toBeUndefined()
      expect(detail(raw).transcript.liveObservedDomain).toBeUndefined()
      expect(detail(raw).transcript.segments[0].text).toBe(
        'Canonique prioritaire.'
      )
    }
  )

  it.each(liveDomainBlockerReasons)(
    'preserves explicit uncertainty: %s',
    (reason) => {
      const producer_revision =
        reason === 'revision_exhausted' ? Number.MAX_SAFE_INTEGER : 1
      const raw = liveDomainFixture({
        producer_revision,
        state: 'unknown',
        counts: null,
        blockers: [{ reason, epoch_ref: null }],
      })
      expect(normalizeLiveObservedDomain(raw)?.blockers).toEqual([
        { reason, epochRef: null },
      ])
    }
  )

  it.each([undefined, null, liveDomainFixture({ extra: 'invalid' })])(
    'withdraws old observed metadata while retaining the canonical cached text',
    (raw) => {
      const current = detail(liveDomainFixture())
      const incoming = detail(raw)
      const merged = mergeHistoryDetail(current, incoming)
      expect(merged.transcript.liveObservedDomain).toBeUndefined()
      expect(merged.transcript.segments).toEqual(current.transcript.segments)
    }
  )
})
