import { normalizeNativeFullPreviewSnapshot } from './meetingNativeFull'
import { normalizeNativeCoverage } from './meetingNativeCoverage'
import type { MeetingTranscript } from './types'

export const fullMeetingRef = 'meeting_full0123456789'
export const fullDigest = 'e'.repeat(64)
export const fullEpochA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
export const fullEpochB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
export const fullFailedEpoch = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'

export const fullCoverage = (changes: Record<string, unknown> = {}) => ({
  version: 1,
  meeting_ref: fullMeetingRef,
  snapshot_digest: fullDigest,
  epochs: [
    { epoch_ref: fullEpochA, state: 'available' },
    { epoch_ref: fullEpochB, state: 'available' },
    { epoch_ref: fullFailedEpoch, state: 'failed' },
  ],
  ...changes,
})

export const fullSegment = (changes: Record<string, unknown> = {}) => ({
  id: `${fullEpochA}_s1`,
  start_ms: 5000,
  speaker: 'Camille',
  text: '  De\u0301cision : ne pas payer 250 €.\n',
  source_epoch: { epoch_ref: fullEpochA, time_basis: 'source_relative_ms' },
  ...changes,
})

export const fullResponse = (changes: Record<string, unknown> = {}) => ({
  version: 1,
  meeting_ref: fullMeetingRef,
  snapshot_digest: fullDigest,
  coverage: 'partial_sources',
  ordering: 'independent_epoch_groups',
  native_coverage: fullCoverage(),
  segments: [
    fullSegment(),
    fullSegment({
      id: `${fullEpochB}_s1`,
      start_ms: 0,
      text: '  Reconnexion.\nNe pas confirmer.  ',
      source_epoch: { epoch_ref: fullEpochB, time_basis: 'source_relative_ms' },
    }),
  ],
  retention_expires_at: Math.floor(Date.now() / 1000) + 3600,
  truncated: false,
  ...changes,
})

export const fullRequest = () => {
  const coverage = normalizeNativeCoverage(fullCoverage(), fullMeetingRef)
  if (!coverage) throw new Error('Expected synthetic full native coverage')
  return { meetingRef: fullMeetingRef, snapshotDigest: fullDigest, coverage }
}

export const fullPreview = (): MeetingTranscript => ({
  status: 'partial',
  projection: {
    version: 1,
    state: 'partial',
    revision: 1,
    digest: fullDigest,
    source: null,
  },
  segments: [
    {
      id: `${fullEpochA}_s1`,
      startMs: 5000,
      speaker: 'Camille',
      text: fullSegment().text,
      sourceEpoch: { epochRef: fullEpochA, timeBasis: 'source_relative_ms' },
    },
  ],
  truncated: true,
  nativeCoverage: fullRequest().coverage,
})

export const fullHistoryDetail = (changes: Record<string, unknown> = {}) => ({
  id: fullMeetingRef,
  started_at: '2026-10-25T10:00:00Z',
  transcript_projection: {
    version: 1,
    state: 'partial',
    revision: 1,
    digest: fullDigest,
    source: null,
  },
  transcript: {
    status: 'available',
    segments: [fullSegment()],
    truncated: true,
  },
  native_coverage: fullCoverage(),
  ...changes,
})

export const fullSnapshot = () => {
  const snapshot = normalizeNativeFullPreviewSnapshot(
    fullCoverage(),
    fullHistoryDetail().transcript,
    fullMeetingRef,
    fullPreview().projection
  )
  if (!snapshot) throw new Error('Expected synthetic native preview snapshot')
  return snapshot
}
