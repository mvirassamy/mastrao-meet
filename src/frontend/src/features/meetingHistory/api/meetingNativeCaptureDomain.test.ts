import { describe, expect, it } from 'vitest'
import { mergeHistoryDetail, normalizeHistoryDetail } from './meetingHistoryApi'
import { nativeDomainFixture } from './meetingNativeCaptureDomain.fixtures'
import { normalizeNativeCaptureDomain } from './meetingNativeCaptureDomain'
import { normalizeNativeCoverage } from './meetingNativeCoverage'
import {
  fullCoverage,
  fullHistoryDetail,
  fullMeetingRef,
} from './meetingNativeFull.fixtures'

const withDomain = (domain: unknown) => fullCoverage({ capture_domain: domain })
const normalize = (domain: unknown) =>
  normalizeNativeCoverage(withDomain(domain), fullMeetingRef)

describe('known capture domain public boundary', () => {
  it.each(['open', 'closing', 'closed'])(
    'retains %s without claiming meeting completeness',
    (closure_state) => {
      const raw = nativeDomainFixture({ closure_state })
      const before = JSON.stringify(raw)
      const result = normalize(raw)
      expect(result?.meetingCoverage).toBe('unverified')
      expect(result?.captureDomain).toMatchObject({
        closureState: closure_state,
        producerRevision: 3,
        counts: {
          terminal_captures: 2,
          failed_captures: 1,
          verified_results: 1,
        },
      })
      expect(JSON.stringify(raw)).toBe(before)
    }
  )
  it.each([undefined, null])(
    'allows legacy producer without domain: %j',
    (value) => {
      expect(normalize(value)?.captureDomain).toBeUndefined()
      expect(normalize(value)?.epochs).toHaveLength(3)
    }
  )
  it('keeps unknown invalidation but removes counts across the actual merge', () => {
    const current = normalizeHistoryDetail(
      fullHistoryDetail({ native_coverage: withDomain(nativeDomainFixture()) })
    )!
    const incoming = normalizeHistoryDetail(
      fullHistoryDetail({
        native_coverage: withDomain(
          nativeDomainFixture({
            producer_revision: 4,
            snapshot_digest: 'f'.repeat(64),
            closure_state: 'unknown',
            reason: 'payload_limit_exceeded',
            counts: null,
          })
        ),
      })
    )!
    const merged = mergeHistoryDetail(current, incoming)
    expect(merged.transcript.nativeCoverage?.captureDomain).toMatchObject({
      producerRevision: 4,
      closureState: 'unknown',
      counts: null,
    })
    expect(
      current.transcript.nativeCoverage?.captureDomain?.counts?.epochs
    ).toBe(2)
    expect(
      mergeHistoryDetail(
        merged,
        normalizeHistoryDetail(
          fullHistoryDetail({ native_coverage: withDomain(null) })
        )!
      ).transcript.nativeCoverage?.captureDomain
    ).toBeUndefined()
  })
  it.each([
    { version: 2 },
    { scope: 'meeting_complete' },
    { domain_ref: 'private' },
    { producer_revision: 0 },
    { producer_revision: 1.1 },
    { producer_revision: Number.MAX_SAFE_INTEGER + 1 },
    { snapshot_digest: 'D'.repeat(64) },
    { closure_state: 'complete' },
    { reason: 'source_missing' },
    { counts: null },
    { counts: {} },
    { counts: { ...nativeDomainFixture().counts, epochs: 33 } },
    { counts: { ...nativeDomainFixture().counts, observations: 129 } },
    { counts: { ...nativeDomainFixture().counts, verified_results: -1 } },
    { counts: { ...nativeDomainFixture().counts, failed_captures: 1.2 } },
    { counts: { ...nativeDomainFixture().counts, object_key: 'private' } },
    { closure_state: 'unknown', reason: 'late_core_facts' },
    { closure_state: 'unknown', counts: null },
    { reason: 'late_core_facts' },
    {
      blockers: [
        { reason: 'start_unresolved', epoch_ref: null, observation_ref: null },
      ],
    },
    {
      closure_state: 'closing',
      blockers: [
        { reason: 'invented', epoch_ref: null, observation_ref: null },
      ],
    },
    {
      closure_state: 'closing',
      blockers: [
        { reason: 'start_unresolved', epoch_ref: 'bad', observation_ref: null },
      ],
    },
    {
      closure_state: 'closing',
      blockers: [{ reason: 'start_unresolved', epoch_ref: null }],
    },
    {
      closure_state: 'closing',
      blockers: Array.from({ length: 257 }, () => ({
        reason: 'start_unresolved',
        epoch_ref: null,
        observation_ref: null,
      })),
    },
    { provider_binding_digest: 'a'.repeat(64) },
    { room_sid: 'RM_private' },
    { object_key: 'private' },
  ])(
    'withdraws coverage and full consultation for invalid/private data: %j',
    (changes) => {
      const domain = nativeDomainFixture(changes)
      expect(normalize(domain)).toBeUndefined()
      expect(
        normalizeHistoryDetail(
          fullHistoryDetail({ native_coverage: withDomain(domain) })
        )?.transcript.nativeFullSnapshot
      ).toBeUndefined()
    }
  )
  it('preserves bounded blockers and accepts zero results in closed inventory', () => {
    const raw = nativeDomainFixture({
      closure_state: 'closing',
      blockers: [
        {
          reason: 'capture_not_drained',
          epoch_ref: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          observation_ref: null,
        },
        { reason: 'epoch_not_ended', epoch_ref: null, observation_ref: null },
      ],
    })
    expect(normalizeNativeCaptureDomain(raw)?.blockers).toEqual([
      {
        reason: 'capture_not_drained',
        epochRef: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        observationRef: null,
      },
      { reason: 'epoch_not_ended', epochRef: null, observationRef: null },
    ])
    expect(
      normalize(
        nativeDomainFixture({
          counts: {
            ...nativeDomainFixture().counts,
            verified_sources: 0,
            verified_results: 0,
          },
        })
      )?.captureDomain?.closureState
    ).toBe('closed')
  })
})
