import { fetchApi } from '@/api/fetchApi'
import { normalizeNativeCoverage } from './meetingNativeCoverage'
import type {
  MeetingNativeCoverage,
  MeetingTranscript,
  MeetingTranscriptSegment,
} from './types'

const REFERENCE = /^[A-Za-z0-9_-]{16,160}$/u
const DIGEST = /^[a-f0-9]{64}$/u
const SEGMENT_ID = /^[A-Za-z0-9_-]{1,160}$/u
const MAX_SEGMENTS = 8192
const MAX_SOURCE_BYTES = 768 * 1024
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024

type NativeFullIdentity = { meetingRef: string; snapshotDigest: string }
export type NativeFullSnapshot = NativeFullIdentity & {
  previewIdentity: string
  projectionRevision: number
}
export type NativeFullRequest = NativeFullIdentity & {
  coverage: MeetingNativeCoverage
}
export type MeetingNativeFull = NativeFullRequest & {
  segments: (MeetingTranscriptSegment & {
    sourceEpoch: NonNullable<MeetingTranscriptSegment['sourceEpoch']>
  })[]
  retentionExpiresAt: number
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const hasFields = (value: Record<string, unknown>, fields: string[]) =>
  Object.keys(value).length === fields.length &&
  fields.every((field) => Object.hasOwn(value, field))

const settledCoverage = (coverage: MeetingNativeCoverage) =>
  coverage.epochsUnavailableReason === undefined &&
  coverage.epochs.some((epoch) => epoch.state === 'available') &&
  !coverage.epochs.some((epoch) => epoch.state === 'pending')

/** Only a settled, truncated native body can offer an expanded consultation. */
export const nativeFullPreviewRequest = (
  meetingRef: string,
  transcript: MeetingTranscript,
  snapshot: NativeFullSnapshot | undefined
): NativeFullRequest | undefined => {
  const coverage = transcript.nativeCoverage
  if (
    !snapshot ||
    snapshot.meetingRef !== meetingRef ||
    !REFERENCE.test(meetingRef) ||
    !DIGEST.test(snapshot.snapshotDigest) ||
    !coverage ||
    !settledCoverage(coverage) ||
    (transcript.status !== 'available' && transcript.status !== 'partial') ||
    (transcript.projection.state !== 'available' &&
      transcript.projection.state !== 'partial') ||
    snapshot.projectionRevision <
      Math.max(
        transcript.projection.revision,
        transcript.projection.pendingRevision ?? transcript.projection.revision
      ) ||
    transcript.nativePartial ||
    !transcript.truncated ||
    transcript.segments.length === 0 ||
    transcript.segments.length > 1000
  )
    return undefined
  const available = new Set(
    coverage.epochs
      .filter((epoch) => epoch.state === 'available')
      .map((epoch) => epoch.epochRef)
  )
  if (
    !transcript.segments.every(
      (segment) =>
        segment.sourceEpoch?.timeBasis === 'source_relative_ms' &&
        available.has(segment.sourceEpoch.epochRef)
    )
  )
    return undefined
  return { meetingRef, snapshotDigest: snapshot.snapshotDigest, coverage }
}

type NativeSegment = MeetingNativeFull['segments'][number]
const normalizeSegment = (
  value: unknown,
  available: Set<string>
): NativeSegment | undefined => {
  if (
    !isRecord(value) ||
    !hasFields(value, ['id', 'start_ms', 'speaker', 'text', 'source_epoch']) ||
    typeof value.id !== 'string' ||
    value.id.length > 197 ||
    typeof value.start_ms !== 'number' ||
    !Number.isSafeInteger(value.start_ms) ||
    value.start_ms < 0 ||
    typeof value.speaker !== 'string' ||
    value.speaker.length > 160 ||
    typeof value.text !== 'string' ||
    value.text.length === 0 ||
    value.text.length > 4000 ||
    !isRecord(value.source_epoch) ||
    !hasFields(value.source_epoch, ['epoch_ref', 'time_basis']) ||
    typeof value.source_epoch.epoch_ref !== 'string' ||
    !available.has(value.source_epoch.epoch_ref) ||
    value.source_epoch.time_basis !== 'source_relative_ms'
  )
    return undefined
  const prefix = `${value.source_epoch.epoch_ref}_`
  if (
    !value.id.startsWith(prefix) ||
    !SEGMENT_ID.test(value.id.slice(prefix.length))
  )
    return undefined
  return {
    id: value.id,
    startMs: value.start_ms,
    speaker: value.speaker,
    text: value.text,
    sourceEpoch: {
      epochRef: value.source_epoch.epoch_ref,
      timeBasis: 'source_relative_ms',
    },
  }
}

const normalizeSegments = (
  values: unknown,
  coverage: MeetingNativeCoverage,
  maximum: number
): NativeSegment[] | undefined => {
  if (!Array.isArray(values) || values.length === 0 || values.length > maximum)
    return undefined
  const available = new Set(
    coverage.epochs
      .filter((epoch) => epoch.state === 'available')
      .map((epoch) => epoch.epochRef)
  )
  const segments: NativeSegment[] = []
  const identities = new Set<string>()
  const closedGroups = new Set<string>()
  const encoder = new TextEncoder()
  let textBytes = 0
  for (const raw of values) {
    const segment = normalizeSegment(raw, available)
    if (!segment || identities.has(segment.id)) return undefined
    const previous = segments[segments.length - 1]
    const epoch = segment.sourceEpoch.epochRef
    if (previous && previous.sourceEpoch.epochRef !== epoch)
      closedGroups.add(previous.sourceEpoch.epochRef)
    if (
      closedGroups.has(epoch) ||
      (previous?.sourceEpoch.epochRef === epoch &&
        segment.startMs < previous.startMs)
    )
      return undefined
    textBytes += encoder.encode(segment.text).byteLength
    if (textBytes > MAX_SOURCE_BYTES) return undefined
    identities.add(segment.id)
    segments.push(segment)
  }
  return segments
}

/** A missing/invalid fresh body must withdraw expansion even when canonical cache survives. */
export const normalizeNativeFullPreviewSnapshot = (
  rawCoverage: unknown,
  rawTranscript: unknown,
  meetingRef: string,
  rawProjection: unknown
): NativeFullSnapshot | undefined => {
  const coverage = normalizeNativeCoverage(rawCoverage, meetingRef)
  if (
    !coverage ||
    !isRecord(rawCoverage) ||
    typeof rawCoverage.snapshot_digest !== 'string' ||
    !settledCoverage(coverage) ||
    !isRecord(rawProjection) ||
    rawProjection.version !== 1 ||
    (rawProjection.state !== 'available' &&
      rawProjection.state !== 'partial') ||
    rawProjection.digest !== rawCoverage.snapshot_digest ||
    rawProjection.refreshPending === true ||
    typeof rawProjection.revision !== 'number' ||
    !Number.isSafeInteger(rawProjection.revision) ||
    rawProjection.revision < 0 ||
    !isRecord(rawTranscript) ||
    !hasFields(rawTranscript, ['status', 'segments', 'truncated']) ||
    rawTranscript.status !== 'available' ||
    rawTranscript.truncated !== true
  )
    return undefined
  const segments = normalizeSegments(rawTranscript.segments, coverage, 1000)
  if (!segments) return undefined
  return {
    meetingRef,
    snapshotDigest: rawCoverage.snapshot_digest,
    previewIdentity: JSON.stringify(segments),
    projectionRevision: rawProjection.revision,
  }
}

export const normalizeNativeFull = (
  value: unknown,
  expected: NativeFullRequest,
  now = Date.now()
): MeetingNativeFull | undefined => {
  if (
    !isRecord(value) ||
    !hasFields(value, [
      'version',
      'meeting_ref',
      'snapshot_digest',
      'coverage',
      'ordering',
      'native_coverage',
      'segments',
      'retention_expires_at',
      'truncated',
    ]) ||
    value.version !== 1 ||
    value.meeting_ref !== expected.meetingRef ||
    value.snapshot_digest !== expected.snapshotDigest ||
    value.coverage !== 'partial_sources' ||
    value.ordering !== 'independent_epoch_groups' ||
    value.truncated !== false ||
    typeof value.retention_expires_at !== 'number' ||
    !Number.isSafeInteger(value.retention_expires_at) ||
    value.retention_expires_at <= now / 1000 ||
    !Array.isArray(value.segments) ||
    value.segments.length === 0 ||
    value.segments.length > MAX_SEGMENTS
  )
    return undefined
  const coverage = normalizeNativeCoverage(
    value.native_coverage,
    expected.meetingRef
  )
  if (
    !coverage ||
    !isRecord(value.native_coverage) ||
    value.native_coverage.snapshot_digest !== expected.snapshotDigest ||
    !settledCoverage(coverage) ||
    JSON.stringify(coverage) !== JSON.stringify(expected.coverage)
  )
    return undefined
  const segments = normalizeSegments(value.segments, coverage, MAX_SEGMENTS)
  if (!segments) return undefined
  // Core checks its original source's 768 KiB budget; this flattened DTO has a separate transport budget.
  if (
    new TextEncoder().encode(JSON.stringify(value)).byteLength >
    MAX_RESPONSE_BYTES
  )
    return undefined
  return {
    ...expected,
    coverage,
    segments,
    retentionExpiresAt: value.retention_expires_at,
  }
}

export class MeetingNativeFullContractError extends Error {
  constructor() {
    super('Invalid retained native transcript consultation')
  }
}

export const fetchNativeFull = async (
  expected: NativeFullRequest,
  signal?: AbortSignal
): Promise<MeetingNativeFull> => {
  if (
    !REFERENCE.test(expected.meetingRef) ||
    !DIGEST.test(expected.snapshotDigest) ||
    !settledCoverage(expected.coverage)
  )
    throw new MeetingNativeFullContractError()
  const raw = await fetchApi<unknown>(
    `meetings/history/${encodeURIComponent(expected.meetingRef)}/native-transcript/?snapshot_digest=${expected.snapshotDigest}`,
    { cache: 'no-store', signal }
  )
  const result = normalizeNativeFull(raw, expected)
  if (!result) throw new MeetingNativeFullContractError()
  return result
}
