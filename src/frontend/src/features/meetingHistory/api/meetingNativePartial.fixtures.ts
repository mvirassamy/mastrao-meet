/** Synthetic wire fixtures for the frozen native_partial DTO. */
export const nativeMeetingRef = 'meeting_native0123456789'
export const nativeEpochA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
export const nativeEpochB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
export const nativeEpochC = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
export const nativeDigest = 'a'.repeat(64)

export const nativeSegment = (changes: Record<string, unknown> = {}) => ({
  id: 's1',
  start_ms: 5000,
  speaker: '  Camille  ',
  text: '  De\u0301cision : ne pas payer 250 €.\n',
  source_epoch: { epoch_ref: nativeEpochA, time_basis: 'source_relative_ms' },
  ...changes,
})

export const nativeCoverage = (changes: Record<string, unknown> = {}) => ({
  version: 1,
  meeting_ref: nativeMeetingRef,
  snapshot_digest: nativeDigest,
  epochs: [
    { epoch_ref: nativeEpochA, state: 'available' },
    { epoch_ref: nativeEpochB, state: 'pending' },
    { epoch_ref: nativeEpochC, state: 'failed' },
  ],
  ...changes,
})

export const nativePartial = (changes: Record<string, unknown> = {}) => ({
  version: 1,
  meeting_ref: nativeMeetingRef,
  snapshot_digest: nativeDigest,
  coverage: 'partial_sources',
  segments: [nativeSegment()],
  truncated: false,
  ...changes,
})

export const nativeProjection = (changes: Record<string, unknown> = {}) => ({
  version: 1,
  state: 'transcribing',
  revision: 1,
  digest: null,
  source: null,
  ...changes,
})

export const nativeDetail = (changes: Record<string, unknown> = {}) => ({
  id: nativeMeetingRef,
  started_at: '2026-10-25T10:00:00Z',
  transcript_projection: nativeProjection(),
  transcript: null,
  native_coverage: nativeCoverage(),
  native_partial: nativePartial(),
  ...changes,
})
